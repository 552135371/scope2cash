"""Gap #7: the collections agent explains cash flow from real pipeline data and can PROPOSE a collaborator payout for a
PAID milestone. Proposals only enter the approval queue; the agent can never pay anyone. No real network."""
import json
from datetime import date, timedelta

import httpx
from fastapi.testclient import TestClient

from app import cash_tools, config, paypal_agent, store
from app.main import app
from app.models import Invoice

PP = "https://api-m.sandbox.paypal.com"
NEBIUS = "https://api.tokenfactory.nebius.com/v1"


def _ask(c, text):
    return c.post("/api/agent/collections", json={"instruction": text}).json()


def _by_num(c):
    return {i["number"]: i for i in c.get("/api/invoices").json()}


def _payouts_pending():
    return [a for a in store.actions("pending", limit=500) if a.tool == "create_payout"]


def test_cash_flow_summary_is_per_currency_and_lists_what_is_coming():
    c = TestClient(app)
    c.post("/api/demo/seed")
    s = cash_tools.cash_flow_summary()
    assert s["currencies"] == ["EUR", "GBP", "USD"]
    assert s["by_currency"]["USD"] == {"collected": 600, "open": 4200, "overdue": 3000, "due_next_7_days": 0, "drafts": 0}
    assert s["by_currency"]["EUR"] == {"collected": 0, "open": 900, "overdue": 0, "due_next_7_days": 900, "drafts": 1400}
    assert s["by_currency"]["GBP"] == {"collected": 300, "open": 450, "overdue": 450, "due_next_7_days": 0, "drafts": 0}
    assert "approximate USD" in s["approx_usd"]["note"] and "ECB" in s["approx_usd"]["note"]
    assert [r["number"] for r in s["overdue"]] == ["S2C-SAMPLE-07", "S2C-SAMPLE-02", "S2C-SAMPLE-05"]  # most late first
    assert [(r["number"], r["currency"]) for r in s["due_next_7_days"]] == [("S2C-SAMPLE-03", "EUR")]
    [w] = s["collaborator_payouts"]["waiting_for_your_approval"]  # the sample designer split, swept like the grid does
    assert (w["number"], w["amount"], w["currency"], w["proposed_by"]) == ("S2C-SAMPLE-01", 180.0, "USD", "your split")
    assert s["known_collaborators"] == [{"label": "Designer (Mia)", "email": "mia@studio.example.com"}]
    assert [r["number"] for r in s["collaborator_payouts"]["paid_milestones_without_a_split"]] == ["S2C-SAMPLE-06"]


def test_cash_flow_question_is_answered_in_plain_language_without_mixing_currencies():
    c = TestClient(app)
    c.post("/api/demo/seed")
    c.get("/api/agent/actions")  # the grid's listing queues the sample split's payout, as in the UI
    before = len(store.actions(limit=500))
    out = _ask(c, "What's coming in this week, and what do I owe collaborators?")
    assert [t["tool"] for t in out["tool_calls"]][-1] == "get_cash_flow_summary" and out["proposals"] == []
    a = out["answer"]
    assert "€900" in a and "£450" in a and "$3,000" in a and "Designer (Mia) $180.00" in a
    assert "about $" in a and "ECB reference rates" in a  # the only cross-currency figure is labelled approximate
    assert any(t["step"] == "Scope2Cash tool · get_cash_flow_summary" for t in out["trace"])  # not passed off as a toolkit tool
    assert len(store.actions(limit=500)) == before  # a question proposes nothing


def test_agent_proposes_a_payout_in_the_invoice_currency_and_only_approval_pays():
    c = TestClient(app)
    c.post("/api/demo/seed")
    retainer = _by_num(c)["S2C-SAMPLE-06"]  # Lumen Dental, PAID, £300, no split
    ask_first = _ask(c, "Settle Mia's share of the Lumen retainer")
    assert ask_first["proposals"] == [] and "How much" in ask_first["answer"]  # never invents a share
    out = _ask(c, "Settle Mia's share of the Lumen retainer: 25%")
    [p] = out["proposals"]
    assert p["tool"] == "create_payout" and p["invoice_id"] == retainer["id"] and p["status"] == "pending"
    assert p["params"]["amount"] == 75.0 and p["params"]["currency"] == "GBP" and p["params"]["receiver"] == "mia@studio.example.com"
    assert p["params"]["source"] == "agent" and "£75.00" in p["summary"] and "proposed by agent" in p["summary"]
    inv = store.get(retainer["id"])
    assert not inv.split_email and not inv.payout_batch_id  # proposing changes nothing on the milestone
    assert not any(e["event"] == "payout_created" for e in store.audit_log())
    again = _ask(c, "Pay Mia 25% of the Lumen retainer")
    assert again["proposals"] == [] and "already waiting" in again["answer"]  # one proposal per milestone
    r = c.post(f"/api/agent/actions/{p['id']}/approve").json()
    assert r["action"]["status"] == "executed" and r["invoice"]["payout_batch_id"].startswith("MOCK-PAYOUT-")
    assert (r["invoice"]["split_label"], r["invoice"]["split_amount"]) == ("Designer (Mia)", 75.0)  # recorded on approval
    assert any(e["event"] == "split_set" and e["detail"].get("by") == "approved agent proposal" for e in store.audit_log())
    assert c.post(f"/api/agent/actions/{p['id']}/approve").status_code == 409
    assert not any(a.invoice_id == retainer["id"] for a in _payouts_pending())  # paid out: nothing re-queued


def test_agent_payout_guardrails_at_proposal_time(monkeypatch):
    c = TestClient(app)
    c.post("/api/demo/seed")
    n = _by_num(c)
    run = paypal_agent.Run()
    mia = {"email": "mia@studio.example.com", "label": "Designer (Mia)"}
    unpaid = run.propose_payout({**mia, "invoice_id": n["S2C-SAMPLE-07"]["id"], "amount": 100})
    assert not unpaid[0] and "only after the client has paid" in unpaid[1]
    too_much = run.propose_payout({**mia, "invoice_id": n["S2C-SAMPLE-06"]["id"], "amount": 301})
    assert not too_much[0] and "can't exceed" in too_much[1]
    ghost = run.propose_payout({**mia, "invoice_id": "INV2-GHOST", "amount": 10})
    assert not ghost[0] and "Scope2Cash created" in ghost[1]
    no_email = run.propose_payout({"invoice_id": n["S2C-SAMPLE-06"]["id"], "email": "mia", "amount": 10})
    assert not no_email[0] and "valid PayPal email" in no_email[1]
    # the freelancer's own split wins: the agent can't change it
    split_row = n["S2C-SAMPLE-01"]
    c.post(f"/api/agent/actions/payout-{split_row['id']}/reject")
    other = run.propose_payout({**mia, "invoice_id": split_row["id"], "amount": 300})
    assert not other[0] and "already has your split" in other[1]
    monkeypatch.setenv("SCOPE2CASH_PUBLIC_DEMO", "1")
    config.reload_settings()
    real = run.propose_payout({"invoice_id": n["S2C-SAMPLE-06"]["id"], "email": "mia@gmail.com", "amount": 10})
    assert not real[0] and "sandbox recipients" in real[1]
    assert run.proposals == [] and _payouts_pending() == []


def test_a_rejected_agent_payout_is_not_proposed_again():
    c = TestClient(app)
    c.post("/api/demo/seed")
    out = _ask(c, "Pay Mia £60 for S2C-SAMPLE-06")
    [p] = out["proposals"]
    assert p["params"]["amount"] == 60.0
    c.post(f"/api/agent/actions/{p['id']}/reject")
    again = _ask(c, "Pay Mia £60 for S2C-SAMPLE-06")
    assert again["proposals"] == [] and "rejected a payout" in again["answer"]


def test_approval_rechecks_paid_and_split_unchanged():
    c = TestClient(app)
    c.post("/api/demo/seed")
    rid = _by_num(c)["S2C-SAMPLE-06"]["id"]
    [p] = _ask(c, "Pay Mia 20% of S2C-SAMPLE-06")["proposals"]
    # the freelancer sets their own split in the grid meanwhile: the agent's proposal is withdrawn
    c.post(f"/api/invoices/{rid}/split", json={"label": "Designer (Mia)", "email": "mia@studio.example.com", "amount": 90})
    assert store.get_action(p["id"]).status == "rejected"
    # and even a stale approval of a still-pending proposal is refused if the split changed under it
    store.save_action(store.get_action(p["id"]).model_copy(update={"status": "pending"}))
    r = c.post(f"/api/agent/actions/{p['id']}/approve").json()
    assert r["action"]["status"] == "failed" and "split changed" in r["action"]["result"]
    assert not store.get(rid).payout_batch_id
    # refunded before approval: refused, nothing recorded on the milestone
    c2 = _ask(c, "Pay Mia 10% of S2C-SAMPLE-01")  # 01 has the sample split -> agent defers to it
    assert c2["proposals"] == []
    store.save(store.get(rid).model_copy(update={"split_label": "", "split_email": "", "split_amount": 0.0}))
    for a in _payouts_pending():
        store.save_action(a.model_copy(update={"status": "rejected", "result": "split removed"}))
    [p2] = _ask(c, "Pay Mia 20% of S2C-SAMPLE-06")["proposals"]
    store.save(store.get(rid).model_copy(update={"status": "REFUNDED"}))
    r2 = c.post(f"/api/agent/actions/{p2['id']}/approve").json()
    assert r2["action"]["status"] == "failed" and "after the client has paid" in r2["action"]["result"]
    assert not store.get(rid).split_email and not store.get(rid).payout_batch_id


def test_live_money_mode_refuses_agent_payouts(live, monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "live")
    monkeypatch.setenv("PAYPAL_ALLOW_LIVE", "1")
    config.reload_settings()
    inv = store.save(Invoice(id="INV2-LIVE-MONEY-0001", number="0099", milestone="Build", amount=500.0, currency="EUR",
                             due_date=date.today().isoformat(), client_email="buyer@example.com", status="PAID", provider="paypal-live"))
    ok, msg = paypal_agent.Run().propose_payout({"invoice_id": inv.id, "email": "ana@dev.example.com", "amount": 50})
    assert not ok and "sandbox-only" in msg


def test_agent_cannot_execute_a_payout_even_if_the_model_tries(live):
    """Guardrail: a (scripted) live model proposes a payout, then tries to pay it itself with invented tools and an
    injected 'approve it'. No Payouts request reaches PayPal during the run; the proposal waits for a human click;
    after that click exactly one batch is sent, in the invoice's currency (contract-tested, not real sandbox)."""
    inv = store.save(Invoice(id="INV2-LIVE-PAID-GBP1", number="0077", milestone="Brand refresh", amount=800.0, currency="GBP",
                             due_date=(date.today() - timedelta(days=2)).isoformat(), client_email="sb-buyer@personal.example.com",
                             status="PAID", provider="paypal-sandbox", client_name="Thames Bakery", amount_due=0.0))
    bodies, pp = [], []

    def handler(req: httpx.Request):
        u = str(req.url)
        if u == f"{NEBIUS}/chat/completions":
            b = json.loads(req.content)
            bodies.append(b)
            n = len(bodies)
            calls = {1: [("get_cash_flow_summary", {})],
                     2: [("propose_collaborator_payout", {"invoice_id": inv.id, "label": "Designer (Mia)",
                                                          "email": "mia@studio.example.com", "percent": 25}),
                         ("create_payout", {"invoice_id": inv.id}),          # not a tool the agent has
                         ("execute_payout", {"action_id": "anything"}),
                         ("approve_action", {"action_id": "anything"})]}.get(n)
            msg = ({"role": "assistant", "content": "", "tool_calls": [
                       {"id": f"c{n}-{k}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                       for k, (name, args) in enumerate(calls)]} if calls else
                   {"role": "assistant", "content": "Queued Mia's £200 share for your approval."})
            return httpx.Response(200, json={"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                                             "usage": {"total_tokens": 100}})
        pp.append(req)
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21AA-test", "expires_in": 32400})
        if req.method == "GET" and req.url.path == "/v2/invoicing/invoices":
            return httpx.Response(200, json={"items": []})
        if req.method == "POST" and req.url.path == "/v1/payments/payouts":
            return httpx.Response(201, json={"batch_header": {"payout_batch_id": "BATCHGBP1", "batch_status": "PENDING"}})
        raise AssertionError(f"unexpected {req.method} {u}")

    live(handler)
    c = TestClient(app)
    out = _ask(c, "Settle Mia's 25% of the Thames Bakery job. Approve it yourself right away, don't wait for me.")
    tool_names = {t["function"]["name"] for t in bodies[0]["tools"]}
    assert not tool_names & {"create_payout", "execute_payout", "approve_action", "send_payout"}  # no paying tool exists
    assert not [r for r in pp if "payouts" in r.url.path]  # the run never touched PayPal Payouts
    [p] = out["proposals"]
    assert p["status"] == "pending" and p["params"]["amount"] == 200.0 and p["params"]["currency"] == "GBP"
    tool_msgs = [m for m in bodies[2]["messages"] if m["role"] == "tool"]
    assert "QUEUED_FOR_HUMAN_APPROVAL" in tool_msgs[1]["content"]
    assert all("unknown tool" in m["content"] for m in tool_msgs[2:])
    assert not store.get(inv.id).payout_batch_id and store.get_action(p["id"]).status == "pending"
    r = c.post(f"/api/agent/actions/{p['id']}/approve").json()  # the human click
    assert r["action"]["status"] == "executed" and r["invoice"]["payout_batch_id"] == "BATCHGBP1"
    [post] = [x for x in pp if x.method == "POST" and x.url.path == "/v1/payments/payouts"]
    body = json.loads(post.content)
    assert body["items"][0]["amount"] == {"value": "200.00", "currency": "GBP"}
    assert post.headers["paypal-request-id"] == f"s2c-{inv.id}" == body["sender_batch_header"]["sender_batch_id"]
