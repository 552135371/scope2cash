"""PayPal-track features: webhook listener (verify-webhook-signature), the collections agent built on the official
PayPal Agent Toolkit, and the cross-client pipeline. No real network: httpx.MockTransport stands in for PayPal
and Nebius."""
import json
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from app import config, paypal_agent, store, webhooks
from app.main import app
from app.models import Invoice

PP = "https://api-m.sandbox.paypal.com"
NEBIUS = "https://api.tokenfactory.nebius.com/v1"
BRIEF = ("Hi! I'm Sarah from Bloom Pilates Studio. We need an iOS app where members sign up, book classes, "
         "get reminder notifications, buy a monthly subscription, plus an admin dashboard and an AI assistant for FAQs.")


def _drafts(c):
    scope = c.post("/api/scope", json={"requirement": BRIEF}).json()["scope"]
    pricing = c.post("/api/pricing", json={"scope": scope}).json()["pricing"]
    quote = c.post("/api/quote", json={"scope": scope, "pricing": pricing}).json()["quote"]
    return c.post("/api/invoices", json={"scope": scope, "quote": quote, "client_email": "sb-buyer@personal.example.com"}).json()["invoices"]


def _post_event(c, raw: bytes, headers):
    return c.post("/api/paypal/webhook", content=raw, headers=headers)


def _live_invoice(status="SENT", days_late=8, inv_id="INV2-LIVE-AAAA-BBBB") -> Invoice:
    due = (date.today() - timedelta(days=days_late)).isoformat()
    inv = Invoice(id=inv_id, number="0042", milestone="Core build", amount=1800.0, currency="USD", due_date=due,
                  client_email="sb-buyer@personal.example.com", status=status, provider="paypal-sandbox",
                  provider_status="UNPAID", client_name="Bloom Pilates Studio", project="Bloom app", days_overdue=days_late)
    return store.save(inv)


def _pp_invoice(inv_id, status="PAID", due=None):
    return {"id": inv_id, "status": status, "detail": {"invoice_number": "0042", "currency_code": "USD",
            "payment_term": {"due_date": due or date.today().isoformat()}, "metadata": {"recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#" + inv_id}},
            "amount": {"currency_code": "USD", "value": "1800.00"}, "due_amount": {"currency_code": "USD", "value": "0.00" if status == "PAID" else "1800.00"},
            "primary_recipients": [{"billing_info": {"email_address": "sb-buyer@personal.example.com"}}]}


# ================================================================ webhooks: mock path
def test_mock_webhook_marks_paid_and_is_idempotent():
    c = TestClient(app)
    inv = _drafts(c)[0]
    c.post(f"/api/invoices/{inv['id']}/send")
    raw = webhooks.mock_event(store.get(inv["id"]), "INVOICING.INVOICE.PAID")
    h = webhooks.sign_mock(raw)
    r = _post_event(c, raw, h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "processed" and body["invoice_status"] == "PAID" and body["verified_by"] == "mock-hmac"
    got = store.get(inv["id"])
    assert got.status == "PAID" and got.paid_at and got.last_event == "INVOICING.INVOICE.PAID" and got.amount_due == 0
    again = _post_event(c, raw, h).json()  # PayPal delivers at least once
    assert again["status"] == "duplicate"
    assert c.get("/api/webhooks/events").json()[0]["event_type"] == "INVOICING.INVOICE.PAID"
    assert "webhook" in [e["event"] for e in c.get("/api/audit").json()]


def test_mock_webhook_rejects_bad_signature_stale_and_unsigned():
    c = TestClient(app)
    inv = _drafts(c)[0]
    raw = webhooks.mock_event(store.get(inv["id"]), "INVOICING.INVOICE.PAID")
    h = webhooks.sign_mock(raw)
    tampered = raw.replace(b'"PAID"', b'"PAID" ', 1)
    assert _post_event(c, tampered, h).status_code == 400
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _post_event(c, raw, webhooks.sign_mock(raw, transmission_time=old))
    assert r.status_code == 400 and "stale" in r.json()["detail"]
    paypal_style = {**h, "PAYPAL-AUTH-ALGO": "SHA256withRSA"}  # looks like PayPal, but no keys to verify it with
    assert _post_event(c, raw, paypal_style).status_code == 400
    assert store.get(inv["id"]).status == "DRAFT"
    assert "webhook_rejected" in [e["event"] for e in c.get("/api/audit").json()]


def test_mock_webhook_cancel_unknown_nested_and_stale_order():
    c = TestClient(app)
    invs = _drafts(c)
    a, b = store.get(invs[0]["id"]), store.get(invs[1]["id"])
    # demo button routes through the webhook pipeline
    assert c.post(f"/api/demo/{a.id}/cancel").json()["status"] == "CANCELLED"
    # resource nested under "invoice" (older invoicing event shape) is handled too
    ev = json.loads(webhooks.mock_event(b, "INVOICING.INVOICE.PAID"))
    ev["resource"] = {"invoice": ev["resource"]}
    raw = json.dumps(ev).encode()
    assert _post_event(c, raw, webhooks.sign_mock(raw)).json()["invoice_status"] == "PAID"
    # a late SENT/UPDATED event must not resurrect a paid invoice
    raw = webhooks.mock_event(store.get(b.id).model_copy(update={"status": "SENT"}), "INVOICING.INVOICE.UPDATED")
    ev = json.loads(raw); ev["resource"]["status"] = "SENT"; raw = json.dumps(ev).encode()
    r = _post_event(c, raw, webhooks.sign_mock(raw)).json()
    assert r["status"] == "processed" and "stale" in r["detail"] and store.get(b.id).status == "PAID"
    # events for invoices Scope2Cash didn't create are acknowledged (2xx, so PayPal stops retrying) but ignored
    ghost = json.loads(webhooks.mock_event(b, "INVOICING.INVOICE.PAID")); ghost["resource"]["id"] = "INV2-NOPE-0000-0000"
    raw = json.dumps(ghost).encode()
    assert _post_event(c, raw, webhooks.sign_mock(raw)).json()["status"] == "ignored"


# ================================================================ webhooks: live path (PayPal postback verification)
def _live_handler(seen, verification="SUCCESS", paypal_status="PAID", verify_status=200):
    def handler(req: httpx.Request):
        seen.append(req)
        u = str(req.url)
        if u == f"{PP}/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21AA-test", "token_type": "Bearer", "expires_in": 32400})
        if u == f"{PP}/v1/notifications/verify-webhook-signature":
            if verify_status != 200:
                return httpx.Response(verify_status, json={"name": "INTERNAL_SERVICE_ERROR"})
            return httpx.Response(200, json={"verification_status": verification})
        if req.method == "GET" and u.startswith(f"{PP}/v2/invoicing/invoices/"):
            return httpx.Response(200, json=_pp_invoice(u.rsplit("/", 1)[-1], paypal_status))
        raise AssertionError(f"unexpected {req.method} {u}")
    return handler


PAYPAL_HEADERS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": f"{PP}/v1/notifications/certs/CERT-360caa42-fca2a594-ab66f33d",
                  "PAYPAL-TRANSMISSION-ID": "db49fb10-1343-11ef-ac58-e32457403f67", "PAYPAL-TRANSMISSION-TIME": "2026-11-02T05:19:23Z",
                  "PAYPAL-TRANSMISSION-SIG": "ab2tJk1VCFm4EqdSuKqezr38rTdY3JeRQlw94V1xYAIQ==", "Content-Type": "application/json"}


def _paypal_event_raw(inv_id):
    # Deliberately odd spacing/key order: verification must forward these exact bytes, not a re-serialisation.
    return ('{"id":"WH-3F562076HD293871E-75F399086E414290U",  "event_version":"1.0","create_time":"2026-11-02T05:19:19.355Z",'
            '"resource_type":"invoices","event_type":"INVOICING.INVOICE.PAID","summary":"An invoice was paid",'
            '"resource":{"id":"%s","status":"PAID","detail":{"invoice_number":"0042"},'
            '"amount":{"currency_code":"USD","value":"1800.00"}}}' % inv_id).encode()


def test_live_webhook_verified_by_paypal_then_refreshed(live, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "0NH55953DH663215D")
    config.reload_settings()
    seen = []
    live(_live_handler(seen))
    inv = _live_invoice()
    raw = _paypal_event_raw(inv.id)
    r = _post_event(TestClient(app), raw, PAYPAL_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["verified_by"] == "paypal-api" and r.json()["invoice_status"] == "PAID"
    ver = next(q for q in seen if q.url.path == "/v1/notifications/verify-webhook-signature")
    assert ver.headers["authorization"] == "Bearer A21AA-test"
    assert raw in ver.content  # raw event spliced in verbatim
    body = json.loads(ver.content)
    assert body["webhook_id"] == "0NH55953DH663215D" and body["auth_algo"] == "SHA256withRSA"
    assert body["transmission_id"] == PAYPAL_HEADERS["PAYPAL-TRANSMISSION-ID"] and body["cert_url"] == PAYPAL_HEADERS["PAYPAL-CERT-URL"]
    assert body["transmission_sig"] == PAYPAL_HEADERS["PAYPAL-TRANSMISSION-SIG"]
    assert body["webhook_event"]["event_type"] == "INVOICING.INVOICE.PAID"
    assert any(q.method == "GET" and q.url.path == f"/v2/invoicing/invoices/{inv.id}" for q in seen)  # API is the truth
    got = store.get(inv.id)
    assert got.status == "PAID" and got.provider_status == "PAID" and got.last_event == "INVOICING.INVOICE.PAID"


def test_live_webhook_failure_missing_id_and_mock_forgery(live, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "0NH55953DH663215D")
    config.reload_settings()
    seen = []
    live(_live_handler(seen, verification="FAILURE"))
    inv = _live_invoice()
    c = TestClient(app)
    raw = _paypal_event_raw(inv.id)
    r = _post_event(c, raw, PAYPAL_HEADERS)
    assert r.status_code == 400 and "FAILURE" in r.json()["detail"] and store.get(inv.id).status == "SENT"
    # a locally signed (mock) event cannot touch a real PayPal invoice, even with a valid mock signature
    fake = json.dumps({"id": "WH-FORGED-1", "event_type": "INVOICING.INVOICE.PAID", "resource": {"id": inv.id, "status": "PAID"}}).encode()
    assert _post_event(c, fake, webhooks.sign_mock(fake)).json()["status"] == "ignored"
    assert store.get(inv.id).status == "SENT"
    # PayPal verification API down -> 503 so PayPal retries later
    live(_live_handler(seen, verify_status=500))
    raw2 = raw.replace(b"WH-3F56", b"WH-4F56")
    assert _post_event(c, raw2, PAYPAL_HEADERS).status_code == 503
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "")
    config.reload_settings()
    assert _post_event(c, raw2, PAYPAL_HEADERS).status_code == 503


# ================================================================ collections agent: PayPal Agent Toolkit
def test_agent_uses_official_toolkit_catalog_and_handlers():
    tools = paypal_agent.selected_tools()
    assert set(tools) == {"list_invoices", "get_invoice", "send_invoice_reminder", "cancel_sent_invoice"}
    assert tools["send_invoice_reminder"]["execute"].__module__ == "paypal_agent_toolkit.shared.invoices.tool_handlers"
    schemas = {t["function"]["name"]: t["function"] for t in paypal_agent.tool_schemas()}
    assert schemas["get_invoice"]["parameters"] == tools["get_invoice"]["args_schema"].model_json_schema()
    assert "NOT executed immediately" in schemas["cancel_sent_invoice"]["description"]
    h = TestClient(app).get("/api/health").json()["adapters"]
    assert h["agent_toolkit"]["available"] and "paypal-agent-toolkit" in h["agent_toolkit"]["provider"]
    assert h["webhooks"]["path"] == "/api/paypal/webhook" and h["webhooks"]["mode"] == "mock"


def test_agent_mock_plan_queues_reminders_and_needs_approval():
    c = TestClient(app)
    seeded = c.post("/api/demo/seed").json()
    assert len(seeded) == 8 and all(i["sample"] and i["provider"] == "mock" for i in seeded)
    r = c.post("/api/agent/collections", json={"instruction": "Nudge everyone who is more than 3 days late"})
    assert r.status_code == 200, r.text
    out = r.json()
    tools_used = [t["tool"] for t in out["tool_calls"]]
    assert tools_used[0] == "list_invoices" and "get_invoice" in tools_used and "send_invoice_reminder" in tools_used
    nums = sorted(p["invoice_number"] for p in out["proposals"])
    assert nums == ["S2C-SAMPLE-02", "S2C-SAMPLE-07"]  # 9 and 17 days late; the 2-day-late one is below the threshold
    assert all(p["status"] == "pending" for p in out["proposals"]) and "approve" in out["answer"].lower()
    assert out["trace"][0]["step"] == "Collections agent" and any("PayPal Agent Toolkit" in t["step"] for t in out["trace"])
    by_num = {i["number"]: i for i in c.get("/api/invoices").json()}
    assert not by_num["S2C-SAMPLE-02"]["reminded_at"]  # nothing executed yet
    first, second = out["proposals"]
    ok = c.post(f"/api/agent/actions/{first['id']}/approve").json()
    assert ok["action"]["status"] == "executed" and ok["invoice"]["reminded_at"]
    assert c.post(f"/api/agent/actions/{first['id']}/approve").status_code == 409
    assert c.post(f"/api/agent/actions/{second['id']}/reject").json()["action"]["status"] == "rejected"
    assert c.post("/api/agent/actions/act_nope/approve").status_code == 404
    events = [e["event"] for e in c.get("/api/audit").json()]
    assert {"agent_proposed", "agent_approved", "agent_executed", "agent_rejected"} <= set(events)


def test_agent_cancel_only_when_named_and_guardrails():
    c = TestClient(app)
    c.post("/api/demo/seed")
    vague = c.post("/api/agent/collections", json={"instruction": "cancel something please"}).json()
    assert vague["proposals"] == [] and "never cancel on a guess" in vague["answer"]
    named = c.post("/api/agent/collections", json={"instruction": "Cancel invoice S2C-SAMPLE-07, Harbor Yoga paused the project"}).json()
    assert [p["tool"] for p in named["proposals"]] == ["cancel_sent_invoice"]
    res = c.post(f"/api/agent/actions/{named['proposals'][0]['id']}/approve").json()
    assert res["invoice"]["status"] == "CANCELLED"
    # guardrails: paid invoices, unknown invoices, bad args
    paid = next(i for i in c.get("/api/invoices").json() if i["status"] == "PAID")
    run = paypal_agent.Run()
    assert not run.propose("send_invoice_reminder", {"invoice_id": paid["id"]})[0]
    assert "only act on invoices Scope2Cash created" in run.propose("send_invoice_reminder", {"invoice_id": "INV2-GHOST"})[1]
    assert "invalid arguments" in run.propose("send_invoice_reminder", {"invoice_id": "bad id!"})[1]


def test_agent_live_tool_calling_loop_with_nemotron_and_paypal(live):
    inv = _live_invoice(days_late=8)
    nebius_bodies, pp = [], []

    def handler(req: httpx.Request):
        u = str(req.url)
        if u == f"{NEBIUS}/chat/completions":
            b = json.loads(req.content)
            nebius_bodies.append(b)
            n = len(nebius_bodies)
            if n == 1:
                msg = {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function",
                       "function": {"name": "list_invoices", "arguments": "{}"}}]}
            elif n == 2:
                msg = {"role": "assistant", "content": "Bloom is 8 days late.", "tool_calls": [
                    {"id": "call-2", "type": "function", "function": {"name": "send_invoice_reminder",
                     "arguments": json.dumps({"invoice_id": inv.id, "subject": "Following up: invoice 0042"})}},
                    {"id": "call-3", "type": "function", "function": {"name": "send_invoice_reminder",
                     "arguments": json.dumps({"invoice_id": "INV2-HALLUCINATED"})}}]}
            else:
                msg = {"role": "assistant", "content": "<think>ok</think>Queued a reminder for invoice 0042 (8 days late), waiting for your approval."}
            return httpx.Response(200, json={"id": "x", "object": "chat.completion", "model": b["model"],
                                             "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                                             "usage": {"total_tokens": 300}})
        pp.append(req)
        if u == f"{PP}/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21AA-test", "expires_in": 32400})
        if req.method == "GET" and req.url.path == "/v2/invoicing/invoices":
            return httpx.Response(200, json={"total_items": 1, "items": [_pp_invoice(inv.id, "UNPAID", inv.due_date)]})
        if req.method == "POST" and req.url.path == f"/v2/invoicing/invoices/{inv.id}/remind":
            return httpx.Response(204)
        if req.method == "GET" and req.url.path == f"/v2/invoicing/invoices/{inv.id}":
            return httpx.Response(200, json=_pp_invoice(inv.id, "UNPAID", inv.due_date))
        raise AssertionError(f"unexpected {req.method} {u}")

    live(handler)
    c = TestClient(app)
    out = c.post("/api/agent/collections", json={"instruction": "Who owes me money? Chase anyone late."}).json()
    first = nebius_bodies[0]
    assert first["model"] == config.DEFAULT_MODEL_MAIN and first["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in first["tools"]} == {"list_invoices", "get_invoice", "send_invoice_reminder", "cancel_sent_invoice",
                                                              "get_cash_flow_summary", "propose_collaborator_payout"}
    tool_msgs = [m for m in nebius_bodies[2]["messages"] if m["role"] == "tool"]
    assert json.loads(tool_msgs[0]["content"])["items"][0]["id"] == inv.id  # compacted list_invoices result
    assert "QUEUED_FOR_HUMAN_APPROVAL" in tool_msgs[1]["content"] and "refused" in tool_msgs[2]["content"]
    assert out["answer"].startswith("Queued a reminder") and out["trace"][0]["mode"] == "live" and out["trace"][0]["tokens"] == 900
    assert [p["invoice_id"] for p in out["proposals"]] == [inv.id]
    assert not any(q.url.path.endswith("/remind") for q in pp)  # nothing sent before approval
    res = c.post(f"/api/agent/actions/{out['proposals'][0]['id']}/approve").json()
    assert res["action"]["status"] == "executed"
    remind = next(q for q in pp if q.url.path.endswith("/remind"))
    body = json.loads(remind.content)
    assert "invoice_id" not in body and None not in body.values() and body["subject"] == "Following up: invoice 0042"
    assert remind.headers["paypal-request-id"] and remind.headers["authorization"] == "Bearer A21AA-test"


def test_agent_live_llm_failure_falls_back_to_planner(live):
    _live_invoice(days_late=5)

    def handler(req: httpx.Request):
        u = str(req.url)
        if u.startswith(NEBIUS):
            return httpx.Response(503, json={"detail": "overloaded"})
        if u == f"{PP}/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21AA-test", "expires_in": 32400})
        if req.method == "GET" and req.url.path == "/v2/invoicing/invoices":
            inv = store.all_invoices()[0]
            return httpx.Response(200, json={"items": [_pp_invoice(inv.id, "UNPAID", inv.due_date)]})
        if req.method == "GET":
            inv = store.all_invoices()[0]
            return httpx.Response(200, json=_pp_invoice(inv.id, "UNPAID", inv.due_date))
        raise AssertionError(f"unexpected {req.method} {u}")

    live(handler)
    out = TestClient(app).post("/api/agent/collections", json={"instruction": "remind late clients"}).json()
    assert out["trace"][0]["mode"] == "fallback" and "offline planner" in out["trace"][0]["warning"]
    assert len(out["proposals"]) == 1


def test_agent_live_mode_sees_paypal_and_sample_invoices_and_routes_writes(live):
    """With PayPal keys set, labelled SAMPLE (mock) invoices stay visible to the agent; their writes never hit PayPal."""
    real = _live_invoice(days_late=6)
    c = TestClient(app)
    c.post("/api/demo/seed")
    pp = []

    def handler(req: httpx.Request):
        u = str(req.url)
        if u.startswith(NEBIUS):
            return httpx.Response(503, json={"detail": "overloaded"})  # use the deterministic planner
        pp.append(req)
        if u == f"{PP}/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21AA-test", "expires_in": 32400})
        if req.method == "GET" and req.url.path == "/v2/invoicing/invoices":
            return httpx.Response(200, json={"total_items": 1, "items": [_pp_invoice(real.id, "UNPAID", real.due_date)]})
        if req.method == "GET" and req.url.path == f"/v2/invoicing/invoices/{real.id}":
            return httpx.Response(200, json=_pp_invoice(real.id, "UNPAID", real.due_date))
        if req.method == "POST" and req.url.path == f"/v2/invoicing/invoices/{real.id}/remind":
            return httpx.Response(204)
        raise AssertionError(f"unexpected {req.method} {u}")

    live(handler)
    out = c.post("/api/agent/collections", json={"instruction": "Nudge everyone more than 3 days late"}).json()
    got = {p["invoice_number"]: p for p in out["proposals"]}
    assert set(got) == {real.number, "S2C-SAMPLE-02", "S2C-SAMPLE-07"}
    for p in out["proposals"]:
        assert c.post(f"/api/agent/actions/{p['id']}/approve").json()["action"]["status"] == "executed"
    reminds = [q.url.path for q in pp if q.url.path.endswith("/remind")]
    assert reminds == [f"/v2/invoicing/invoices/{real.id}/remind"]  # only the real sandbox invoice went to PayPal
    assert all(store.get(got[n]["invoice_id"]).reminded_at for n in ("S2C-SAMPLE-02", "S2C-SAMPLE-07"))


# ================================================================ pipeline
def test_pipeline_accumulates_projects_and_reseed_replaces_samples():
    c = TestClient(app)
    a = _drafts(c)
    b = _drafts(c)
    allinv = c.get("/api/invoices").json()
    assert len(allinv) == len(a) + len(b) and allinv[0]["client_name"] == "Bloom Pilates Studio" and allinv[0]["project"]
    nums = [i["number"] for i in allinv]
    assert len(set(nums)) == len(nums)  # a second batch the same day continues the sequence instead of reusing numbers
    c.post("/api/demo/seed")
    c.post("/api/demo/seed")
    allinv = c.get("/api/invoices").json()
    assert sum(i["sample"] for i in allinv) == 8 and len(allinv) == len(a) + len(b) + 8


def test_frontend_ships_pinned_ag_grid_community_and_keeps_nebius_flow():
    c = TestClient(app)
    html = c.get("/").text
    # MIT-licensed Community build, version-pinned; no Enterprise/Studio bundle (those need a commercial licence key).
    assert "ag-grid-community@36.2.0/dist/ag-grid-community.min.js" in html
    assert "ag-grid-enterprise" not in html and "ag-studio" not in html
    # The original five-step Nebius flow is still there, and the pipeline is additive.
    for el in ('id="s1"', 'id="s5"', 'id="sample"', 'id="pipe"', 'id="grid"', 'id="ask"'):
        assert el in html, el
    js = c.get("/static/pipeline.js")
    assert js.status_code == 200 and "createGrid" in js.text and "plain" in js.text  # AG Grid + no-CDN fallback
