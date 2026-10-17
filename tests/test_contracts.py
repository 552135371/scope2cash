"""Contract tests: verify the exact HTTP requests our live adapters send and that they parse realistic,
documented responses (Nebius Token Factory, Tavily, PayPal Invoicing v2), plus retries, timeouts, and the
visible fallback to mock. No real network: httpx.MockTransport stands in for every provider."""
import base64
import json
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent, config
from app.adapters import http, llm, payments
from app.main import app
from app.models import Invoice, Scope

BRIEF = ("Hi! I'm Sarah from Bloom Pilates Studio. We need an iOS app where members sign up, book classes, "
         "get reminder notifications, buy a monthly subscription, plus an admin dashboard and an AI assistant for FAQs.")
NEBIUS = "https://api.tokenfactory.nebius.com/v1"
PP = "https://api-m.sandbox.paypal.com"

SCOPE_JSON = {
    "project_title": "Bloom Pilates iOS app", "client_name": "Bloom Pilates Studio", "summary": "Member app.",
    "project_type": "ios", "confidence": 0.8, "client_budget": None, "deadline": None, "language": "en",
    "milestones": [
        {"title": "Discovery & UX", "deliverables": ["Wireframes"], "est_hours": 10, "weeks": 1},
        {"title": "Core build", "deliverables": ["Booking", "Auth"], "est_hours": "40-50", "weeks": 2},
        {"title": "QA & launch", "deliverables": ["App Store submission"], "est_hours": 8, "weeks": 1}],
    "assumptions": ["Client provides copy"], "risks": ["IAP fees"], "open_questions": ["Launch date?"]}


def chat_response(content, model="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", reasoning=None):
    """Shape from docs.tokenfactory.nebius.com/api-reference/introduction (OpenAI chat.completion)."""
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"id": "chatcmpl-1", "object": "chat.completion", "created": 1717516032, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": msg}],
            "usage": {"prompt_tokens": 412, "completion_tokens": 230, "total_tokens": 642}}


# ---------------------------------------------------------------- Nebius Token Factory
def test_nebius_chat_request_shape_and_parsing(live):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        content = "<think>plan the milestones</think>\n```json\n" + json.dumps(SCOPE_JSON) + "\n```"
        return httpx.Response(200, json=chat_response(content), headers={"x-ratelimit-remaining-requests": "59"})

    live(handler)
    scope, trace = agent.build_scope(BRIEF)
    req = seen[0]
    assert req.method == "POST" and str(req.url) == f"{NEBIUS}/chat/completions"
    assert req.headers["authorization"] == "Bearer test-nebius-key"
    body = json.loads(req.content)
    assert body["model"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"  # fast tier for a routine English brief
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2 and body["max_tokens"] == 4096 and "reasoning_effort" not in body
    assert scope.milestones[1].est_hours == 45  # "40-50" coerced
    assert trace[0].mode == "live" and trace[0].tier == "fast" and trace[0].tokens == 642 and not trace[0].warning


def test_nebius_reasoning_effort_and_reasoning_content(live, monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    config.reload_settings()
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=chat_response(None, reasoning=json.dumps(SCOPE_JSON)))  # JSON only in reasoning field

    live(handler)
    scope, trace = agent.build_scope(BRIEF)
    assert bodies[0]["reasoning_effort"] == "low" and scope.project_title == "Bloom Pilates iOS app"


def test_nebius_retries_429_with_retry_after(live):
    calls, sleeps = [], []
    http.set_sleep(sleeps.append)

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"detail": "Rate limit exceeded"}, headers={"Retry-After": "2"})
        return httpx.Response(200, json=chat_response(json.dumps(SCOPE_JSON)))

    live(handler)
    _, trace = agent.build_scope(BRIEF)
    assert len(calls) == 2 and sleeps == [2.0] and trace[0].mode == "live"


def test_nebius_json_mode_rejected_retries_without_it(live):
    bodies = []

    def handler(req):
        b = json.loads(req.content)
        bodies.append(b)
        if "response_format" in b:
            return httpx.Response(400, json={"detail": "response_format json_object is not supported for this model"})
        return httpx.Response(200, json=chat_response("Here you go: " + json.dumps(SCOPE_JSON)))

    live(handler)
    _, trace = agent.build_scope(BRIEF)
    assert "response_format" in bodies[0] and "response_format" not in bodies[1]
    assert trace[0].mode == "live"
    agent.build_scope(BRIEF)  # remembered per model: no second rejected attempt
    assert "response_format" not in bodies[2]


def test_nebius_repair_round_on_invalid_json(live):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return httpx.Response(200, json=chat_response('{"project_title": "x"}'))  # fails Scope validation
        return httpx.Response(200, json=chat_response(json.dumps(SCOPE_JSON)))

    live(handler)
    scope, trace = agent.build_scope(BRIEF)
    assert len(bodies) == 2 and bodies[1]["messages"][-1]["role"] == "user" and "invalid" in bodies[1]["messages"][-1]["content"]
    assert trace[0].mode == "live" and trace[0].tokens == 642 * 2


def test_nebius_timeout_falls_back_with_visible_warning(live):
    calls = []

    def handler(req):
        calls.append(1)
        raise httpx.ReadTimeout("read timed out", request=req)

    live(handler)
    r = TestClient(app).post("/api/scope", json={"requirement": BRIEF}).json()
    step = r["trace"][0]
    assert len(calls) == 3  # 1 try + LLM_MAX_RETRIES=2
    assert step["mode"] == "fallback" and "timed out" in step["warning"] and "offline heuristic" in step["warning"]
    assert r["scope"]["client_name"] == "Bloom Pilates Studio"  # heuristic still produced a usable scope


def test_nebius_auth_error_no_retry_and_502_without_fallback(live, monkeypatch):
    monkeypatch.setenv("FALLBACK_TO_MOCK", "0")
    config.reload_settings()
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"detail": "Couldn't authenticate. Reason: token is not present"})

    live(handler)
    r = TestClient(app).post("/api/scope", json={"requirement": BRIEF})
    assert r.status_code == 502 and "Couldn't authenticate" in r.json()["detail"] and len(calls) == 1


def test_model_check_resolves_ids_and_reports_missing(live, monkeypatch):
    monkeypatch.setenv("MODEL_FAST", "nvidia/Nemotron-3-Nano-30B-A3B")  # the old ID from our first brief
    monkeypatch.setenv("MODEL_DEEP", "nvidia/does-not-exist")
    config.reload_settings()
    seen = []

    def handler(req):
        seen.append(req)
        # Shape from docs.tokenfactory.nebius.com/api-reference/models/list-models
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": i, "created": 1717511223, "object": "model", "owned_by": "system"} for i in
            ["nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nvidia/nemotron-3-super-120b-a12b",
             "nvidia/Nemotron-3-Ultra-550b-a55b", "meta-llama/Llama-3.3-70B-Instruct"]]})

    live(handler)
    chk = llm.check_models()
    assert str(seen[0].url) == f"{NEBIUS}/models" and seen[0].headers["authorization"] == "Bearer test-nebius-key"
    assert chk.resolved["fast"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B" and chk.notes
    assert chk.missing == ["deep: nvidia/does-not-exist"] and not chk.ok
    assert "meta-llama/Llama-3.3-70B-Instruct" not in chk.nvidia_available
    h = TestClient(app).get("/api/health").json()
    assert h["adapters"]["llm"]["models"]["fast"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert any("does-not-exist" in w for w in h["warnings"])
    assert llm.NebiusLLM().model_for("fast") == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def test_model_check_network_failure_is_reported_not_fatal(live):
    live(lambda req: (_ for _ in ()).throw(httpx.ConnectError("dns", request=req)))
    chk = llm.check_models()
    assert chk.checked and not chk.ok and "network error" in chk.error


# ---------------------------------------------------------------- Tavily
def tavily_response(query, results):
    """Shape from docs.tavily.com/documentation/api-reference/endpoint/search (200)."""
    return {"query": query, "answer": None, "images": [], "results": results, "response_time": "1.67",
            "usage": {"credits": 1}, "request_id": "123e4567-e89b-12d3-a456-426614174111"}


def _scope():
    return Scope.model_validate(SCOPE_JSON)


def test_tavily_request_shape_dedupe_and_grounded_pricing(live):
    tav = []

    def handler(req):
        if req.url.host == "api.tavily.com":
            tav.append(req)
            q = json.loads(req.content)["query"]
            return httpx.Response(200, json=tavily_response(q, [
                {"title": "iOS developer rates 2026", "url": "https://example.org/rates", "score": 0.62,
                 "content": "Freelance iOS developers charge $60 to $120 per hour in the US.", "raw_content": None},
                {"title": "App cost guide", "url": "https://example.org/cost", "score": 0.81,
                 "content": "A native MVP typically costs $15,000-$40,000.", "raw_content": None}]))
        return httpx.Response(200, json=chat_response(json.dumps(
            {"hourly_low": 70, "hourly_high": 110, "rationale": "Sources [1] cite $60-$120/h."})))

    live(handler)
    pricing, trace = agent.build_pricing(_scope())
    assert len(tav) == 2
    req = tav[0]
    assert req.method == "POST" and str(req.url) == "https://api.tavily.com/search"
    assert req.headers["authorization"] == "Bearer tvly-test"
    body = json.loads(req.content)
    assert body["search_depth"] == "basic" and body["max_results"] == 4 and body["include_usage"] is True
    assert body["topic"] == "general" and body["query"].startswith("freelance iOS developer hourly rate")
    assert [s.url for s in pricing.sources] == ["https://example.org/cost", "https://example.org/rates"]  # by score, deduped
    assert pricing.grounded and not pricing.mock and (pricing.hourly_low, pricing.hourly_high) == (70, 110)
    assert "$60/h" in pricing.evidence and trace[0].mode == "live" and "2 credits" in trace[0].note


def test_pricing_guardrail_clamps_ungrounded_model_range(live):
    def handler(req):
        if req.url.host == "api.tavily.com":
            return httpx.Response(200, json=tavily_response("q", [
                {"title": "Rates", "url": "https://example.org/r", "score": 0.7, "content": "Typical rates are $40-$75/h."}]))
        return httpx.Response(200, json=chat_response(json.dumps({"hourly_low": 150, "hourly_high": 250, "rationale": "x"})))

    live(handler)
    pricing, trace = agent.build_pricing(_scope())
    assert pricing.hourly_high <= 75 * 1.2 and pricing.grounded
    assert "Guardrail" in trace[1].warning


def test_pricing_without_figures_is_flagged_ungrounded(live):
    def handler(req):
        if req.url.host == "api.tavily.com":
            return httpx.Response(200, json=tavily_response("q", [
                {"title": "Hiring tips", "url": "https://example.org/t", "score": 0.5, "content": "Ask for a portfolio."}]))
        return httpx.Response(200, json=chat_response(json.dumps({"hourly_low": 90, "hourly_high": 140, "rationale": "x"})))

    live(handler)
    pricing, trace = agent.build_pricing(_scope())
    assert not pricing.grounded and "placeholder" in pricing.rationale and "UNGROUNDED" in trace[1].note


def test_tavily_plan_limit_falls_back_to_labelled_samples(live):
    def handler(req):
        if req.url.host == "api.tavily.com":
            return httpx.Response(432, json={"detail": {"error": "This request exceeds your plan's set usage limit."}})
        return httpx.Response(200, json=chat_response(json.dumps({"hourly_low": 60, "hourly_high": 100, "rationale": "x"})))

    live(handler)
    r = TestClient(app).post("/api/pricing", json={"scope": SCOPE_JSON}).json()
    assert r["trace"][0]["mode"] == "fallback" and "usage limit" in r["trace"][0]["warning"]
    assert r["pricing"]["mock"] and all("mock" in s["title"] for s in r["pricing"]["sources"])


# ---------------------------------------------------------------- PayPal Invoicing v2
def _invoice(**kw):
    base = dict(id="pending", number="S2C-1", milestone="Core build", amount=1234.0, currency="USD",
                due_date=(date.today() + timedelta(days=14)).isoformat(), client_email="client@example.com")
    base.update(kw)
    return Invoice(**base)


def pp_invoice(inv_id, status, due, value="1234.00", number="0042"):
    """Trimmed invoice representation per PayPal's invoicing_v2.json (invoice schema)."""
    return {"id": inv_id, "status": status,
            "detail": {"invoice_number": number, "currency_code": "USD", "payment_term": {"due_date": due},
                       "metadata": {"create_time": "2026-10-17T07:00:00Z",
                                    "recipient_view_url": f"https://www.sandbox.paypal.com/invoice/p/#{inv_id}",
                                    "invoicer_view_url": f"https://www.sandbox.paypal.com/invoice/details/{inv_id}"}},
            "invoicer": {"email_address": "sb-merchant@business.example.com"},
            "primary_recipients": [{"billing_info": {"email_address": "client@example.com"}}],
            "amount": {"currency_code": "USD", "value": value,
                       "breakdown": {"item_total": {"currency_code": "USD", "value": value}}},
            "due_amount": {"currency_code": "USD", "value": value},
            "links": [{"href": f"{PP}/v2/invoicing/invoices/{inv_id}", "rel": "self", "method": "GET"}]}


class FakePayPal:
    def __init__(self, due):
        self.due, self.calls, self.status, self.tokens = due, [], "DRAFT", 0

    def __call__(self, req: httpx.Request):
        self.calls.append(req)
        p = req.url.path
        if p == "/v1/oauth2/token":
            self.tokens += 1
            return httpx.Response(200, json={"scope": "https://uri.paypal.com/services/invoicing", "access_token": f"A21AA-{self.tokens}",
                                             "token_type": "Bearer", "app_id": "APP-80W284485P519543T", "expires_in": 32400, "nonce": "n"})
        if p == "/v2/invoicing/generate-next-invoice-number":
            return httpx.Response(200, json={"invoice_number": "0042"})
        if p == "/v2/invoicing/invoices" and req.method == "POST":
            return httpx.Response(201, json=pp_invoice("INV2-Z56S-5LLA-Q52L-CPZ5", "DRAFT", self.due))
        if p.endswith("/send"):
            self.status = "UNPAID"
            return httpx.Response(202, json={"href": "https://www.sandbox.paypal.com/invoice/p/#INV2-Z56S-5LLA-Q52L-CPZ5",
                                             "rel": "payer-view", "method": "GET"})
        if p.endswith("/remind"):
            return httpx.Response(204)
        if req.method == "GET" and p.startswith("/v2/invoicing/invoices/"):
            return httpx.Response(200, json=pp_invoice(p.split("/")[-1], self.status, self.due))
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})


def test_paypal_full_lifecycle_request_shapes(live):
    due = (date.today() + timedelta(days=14)).isoformat()
    fake = FakePayPal(due)
    live(fake)
    pp = payments.PayPalInvoicing()
    inv = pp.create_draft(_invoice(due_date=due), "Bloom Pilates iOS app", "Milestone 2 of 4", ["Booking", "Auth"],
                          ["Due on milestone date"], 2, 4)
    tok, nxt, create = fake.calls[0], fake.calls[1], fake.calls[2]
    assert str(tok.url) == f"{PP}/v1/oauth2/token" and tok.method == "POST"
    assert tok.headers["authorization"] == "Basic " + base64.b64encode(b"test-client:test-secret").decode()
    assert tok.content == b"grant_type=client_credentials"
    assert nxt.method == "POST" and nxt.headers["authorization"] == "Bearer A21AA-1"
    body = json.loads(create.content)
    assert create.headers["prefer"] == "return=representation" and create.headers["paypal-request-id"]
    assert body["detail"]["currency_code"] == "USD" and body["detail"]["invoice_number"] == "0042"
    assert body["detail"]["payment_term"] == {"due_date": due}  # due_date only, never with term_type
    assert body["detail"]["reference"] == "Milestone 2/4: Core build"
    assert body["primary_recipients"] == [{"billing_info": {"email_address": "client@example.com"}}]
    assert body["items"][0]["unit_amount"] == {"currency_code": "USD", "value": "1234.00"}
    assert body["items"][0]["quantity"] == "1" and body["items"][0]["unit_of_measure"] == "AMOUNT"
    assert body["invoicer"] == {"email_address": "sb-merchant@business.example.com"}
    assert "amount" not in body  # computed by PayPal from items; we reconcile instead
    assert inv.id == "INV2-Z56S-5LLA-Q52L-CPZ5" and inv.status == "DRAFT" and inv.number == "0042"
    assert inv.provider == "paypal-sandbox" and inv.payer_link.endswith("#INV2-Z56S-5LLA-Q52L-CPZ5") and not pp.warnings

    sent = pp.send(inv)
    send_req = [c for c in fake.calls if c.url.path.endswith("/send")][0]
    assert json.loads(send_req.content) == {"send_to_recipient": True, "send_to_invoicer": True}
    assert sent.status == "SENT" and sent.provider_status == "UNPAID"
    reminded = pp.remind(sent)
    rem_req = [c for c in fake.calls if c.url.path.endswith("/remind")][0]
    assert rem_req.method == "POST" and reminded.status == "SENT"
    assert fake.tokens == 1  # token cached across calls


def test_paypal_overdue_and_status_mapping():
    due = (date.today() - timedelta(days=5)).isoformat()
    inv = payments.apply_paypal_invoice(_invoice(due_date=due), pp_invoice("INV2-A", "UNPAID", due))
    assert inv.status == "OVERDUE" and inv.days_overdue == 5 and inv.amount_due == 1234.0
    for raw, ours in [("MARKED_AS_PAID", "PAID"), ("PARTIALLY_PAID", "OVERDUE"), ("PAYMENT_PENDING", "OVERDUE"),
                      ("CANCELLED", "CANCELLED"), ("REFUNDED", "REFUNDED"), ("DRAFT", "DRAFT")]:
        assert payments.apply_paypal_invoice(_invoice(due_date=due), pp_invoice("INV2-A", raw, due)).status == ours
    future = (date.today() + timedelta(days=3)).isoformat()
    assert payments.apply_paypal_invoice(_invoice(), pp_invoice("INV2-A", "PARTIALLY_PAID", future)).status == "PARTIALLY_PAID"
    unknown = payments.apply_paypal_invoice(_invoice(status="SENT"), pp_invoice("INV2-A", "SOMETHING_NEW", future))
    assert unknown.status == "SENT" and unknown.provider_status == "SOMETHING_NEW"


def test_paypal_minimal_create_response_follows_link(live):
    due = (date.today() + timedelta(days=7)).isoformat()

    def handler(req):
        p = req.url.path
        if p == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 32400})
        if p.endswith("generate-next-invoice-number"):
            return httpx.Response(500, json={"name": "INTERNAL_SERVER_ERROR"})
        if p == "/v2/invoicing/invoices":
            return httpx.Response(201, json={"rel": "self", "href": f"{PP}/v2/invoicing/invoices/INV2-MIN1", "method": "GET"})
        return httpx.Response(200, json=pp_invoice("INV2-MIN1", "DRAFT", due, value="999.00", number="0007"))

    live(handler)
    pp = payments.PayPalInvoicing()
    inv = pp.create_draft(_invoice(due_date=due), "T", "n")
    assert inv.id == "INV2-MIN1" and inv.number == "0007"
    assert any("auto-number" in w for w in pp.warnings) and any("differs from quote" in w for w in pp.warnings)


def test_paypal_expired_token_is_refreshed_once(live):
    fake = FakePayPal((date.today() + timedelta(days=7)).isoformat())
    state = {"first": True}

    def handler(req):
        if req.url.path.startswith("/v2/invoicing/invoices/") and req.method == "GET" and state["first"]:
            state["first"] = False
            return httpx.Response(401, json={"error": "invalid_token", "error_description": "Token signature verification failed"})
        return fake(req)

    live(handler)
    inv = payments.PayPalInvoicing().refresh(_invoice(id="INV2-X", status="SENT", provider="paypal-sandbox"))
    assert inv.status == "DRAFT" and fake.tokens == 2


DUP = {"name": "UNPROCESSABLE_ENTITY", "message": "The requested action could not be performed, semantically incorrect, or failed business validation.",
       "details": [{"field": "/detail/invoice_number", "issue": "DUPLICATE_INVOICE_NUMBER", "description": "Invoice number already exists."}],
       "debug_id": "b1d1f06c7246c"}


def _quote_payload():
    c = TestClient(app)
    import os
    os.environ["SCOPE2CASH_FORCE_MOCK"] = "1"
    config.reload_settings()
    scope = c.post("/api/scope", json={"requirement": BRIEF}).json()["scope"]
    pricing = c.post("/api/pricing", json={"scope": scope}).json()["pricing"]
    quote = c.post("/api/quote", json={"scope": scope, "pricing": pricing}).json()["quote"]
    return {"scope": scope, "quote": quote, "client_email": "client@example.com"}


def test_paypal_create_failure_falls_back_to_labelled_mock_drafts(live, monkeypatch):
    payload = _quote_payload()
    for k, v in __import__("tests.conftest", fromlist=["LIVE_ENV"]).LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    config.reload_settings()

    def handler(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 32400})
        if req.url.path.endswith("generate-next-invoice-number"):
            return httpx.Response(200, json={"invoice_number": "0042"})
        return httpx.Response(422, json=DUP)

    live(handler)
    c = TestClient(app)
    r = c.post("/api/invoices", json=payload).json()
    step = r["trace"][0]
    assert step["mode"] == "fallback" and "DUPLICATE_INVOICE_NUMBER" in step["warning"] and "debug_id=b1d1f06c7246c" in step["warning"]
    assert all(i["provider"] == "mock" and i["id"].startswith("INV2-MOCK-") for i in r["invoices"])
    # a mock draft stays mock (and sendable) even though PayPal is live
    assert c.post(f"/api/invoices/{r['invoices'][0]['id']}/send").json()["status"] == "SENT"

    monkeypatch.setenv("FALLBACK_TO_MOCK", "0")
    config.reload_settings()
    r2 = c.post("/api/invoices", json=payload)
    assert r2.status_code == 502 and "DUPLICATE_INVOICE_NUMBER" in r2.json()["detail"]


def test_paypal_send_failure_never_fakes_a_send(live, monkeypatch):
    from app import store
    due = (date.today() + timedelta(days=7)).isoformat()
    store.save(_invoice(id="INV2-LIVE", due_date=due, provider="paypal-sandbox"), seq=1)

    def handler(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 32400})
        return httpx.Response(503, json={"name": "SERVICE_UNAVAILABLE", "debug_id": "x1"})

    live(handler)
    r = TestClient(app).post("/api/invoices/INV2-LIVE/send")
    assert r.status_code == 502 and "SERVICE_UNAVAILABLE" in r.json()["detail"]
    assert store.get("INV2-LIVE").status == "DRAFT"
