"""Collaborator payouts (PayPal Payouts API): a paid milestone with a split queues ONE payout proposal; only the
freelancer's Approve sends it; guardrails re-check at approval; the request matches PayPal's Payouts v1 shape.
No real network: httpx.MockTransport stands in for PayPal."""
import json
from datetime import date, timedelta

import httpx
from fastapi.testclient import TestClient

from app import config, payouts_flow, store, webhooks
from app.adapters import payouts as po
from app.main import app
from app.models import Invoice

PP = "https://api-m.sandbox.paypal.com"


def _inv(status="PAID", provider="mock", inv_id="INV2-MOCK-PAY-0001", **kw) -> Invoice:
    base = dict(id=inv_id, number="S2C-T-01", milestone="UX & prototype", amount=1200.0, currency="USD",
                due_date=(date.today() - timedelta(days=3)).isoformat(), client_email="hello@harbor.example.com",
                status=status, provider=provider, client_name="Harbor Yoga")
    base.update(kw)
    return store.save(Invoice(**base))


def _pending(c):
    return [a for a in c.get("/api/agent/actions?status=pending").json() if a["tool"] == "create_payout"]


def test_sample_pipeline_shows_one_payout_proposal_and_approve_pays_once(monkeypatch):
    c = TestClient(app)
    c.post("/api/demo/seed")
    props = _pending(c)
    assert len(props) == 1
    p = props[0]
    assert p["invoice_number"] == "S2C-SAMPLE-01" and p["params"]["amount"] == 180.0
    assert p["params"]["receiver"] == "mia@studio.example.com" and "PayPal Payouts" in p["summary"]
    assert len(_pending(c)) == 1  # listing again does not duplicate (sweep is idempotent)
    r = c.post(f"/api/agent/actions/{p['id']}/approve").json()
    assert r["action"]["status"] == "executed" and r["invoice"]["payout_status"] == "SUCCESS"
    assert r["invoice"]["payout_batch_id"].startswith("MOCK-PAYOUT-")
    assert c.post(f"/api/agent/actions/{p['id']}/approve").status_code == 409  # can't approve twice
    assert _pending(c) == []  # paid out: never re-queued
    assert any(e["event"] == "payout_created" for e in store.audit_log())


def test_split_on_unpaid_milestone_waits_for_the_paid_webhook():
    c = TestClient(app)
    inv = _inv(status="SENT")
    r = c.post(f"/api/invoices/{inv.id}/split", json={"label": "Copywriter (Sam)", "email": "sam@words.example.com", "percent": 25})
    assert r.status_code == 200 and r.json()["split_amount"] == 300.0
    assert _pending(c) == []  # client hasn't paid yet
    res = webhooks.deliver_mock(store.get(inv.id), "INVOICING.INVOICE.PAID")
    assert res.invoice_status == "PAID" and "payout queued" in res.detail
    props = _pending(c)
    assert len(props) == 1 and props[0]["params"]["amount"] == 300.0


def test_split_validation_and_public_demo_recipients(monkeypatch):
    c = TestClient(app)
    inv = _inv(status="SENT")
    too_much = c.post(f"/api/invoices/{inv.id}/split", json={"label": "X", "email": "x@a.example.com", "amount": 5000})
    assert too_much.status_code == 409 and "exceed" in too_much.json()["detail"]
    assert c.post(f"/api/invoices/{inv.id}/split", json={"label": "X", "email": "nope", "amount": 10}).status_code == 409
    monkeypatch.setenv("SCOPE2CASH_PUBLIC_DEMO", "1")
    config.reload_settings()
    real = c.post(f"/api/invoices/{inv.id}/split", json={"label": "X", "email": "someone@gmail.com", "amount": 10})
    assert real.status_code == 409 and "sandbox" in real.json()["detail"]


def test_reject_is_final_but_changing_the_split_requeues():
    c = TestClient(app)
    inv = _inv()
    c.post(f"/api/invoices/{inv.id}/split", json={"label": "Dev (Ana)", "email": "ana@dev.example.com", "amount": 400})
    [p] = _pending(c)
    assert c.post(f"/api/agent/actions/{p['id']}/reject").json()["action"]["status"] == "rejected"
    assert _pending(c) == []  # a human said no: the sweep doesn't bring it back
    c.post(f"/api/invoices/{inv.id}/split", json={"label": "Dev (Ana)", "email": "ana@dev.example.com", "amount": 350})
    [p2] = _pending(c)  # a new split is a new decision
    assert p2["params"]["amount"] == 350.0


def test_guard_rechecks_at_approval():
    c = TestClient(app)
    inv = _inv()
    c.post(f"/api/invoices/{inv.id}/split", json={"label": "Dev", "email": "ana@dev.example.com", "amount": 400})
    [p] = _pending(c)
    store.save(store.get(inv.id).model_copy(update={"status": "REFUNDED"}))  # client refunded before the approval
    r = c.post(f"/api/agent/actions/{p['id']}/approve").json()
    assert r["action"]["status"] == "failed" and "after the client has paid" in r["action"]["result"]
    assert not store.get(inv.id).payout_batch_id
    # clearing the split withdraws a queued payout
    inv2 = _inv(inv_id="INV2-MOCK-PAY-0002", number="S2C-T-02")
    c.post(f"/api/invoices/{inv2.id}/split", json={"label": "Dev", "email": "ana@dev.example.com", "amount": 100})
    assert any(a["invoice_id"] == inv2.id for a in _pending(c))
    c.post(f"/api/invoices/{inv2.id}/split", json={"amount": 0})
    assert not any(a["invoice_id"] == inv2.id for a in _pending(c))
    assert payouts_flow.guard({"invoice_id": inv2.id, "receiver": "ana@dev.example.com", "amount": 100}) == "S2C-T-02 has no collaborator split"


def test_paypal_payouts_request_shape_and_status_refresh(live):
    """Contract: POST /v1/payments/payouts (one EMAIL item, sender_batch_id = PayPal-Request-Id = s2c-<invoice id>),
    then GET /v1/payments/payouts/{batch} -> item status UNCLAIMED (receiver has no PayPal account yet)."""
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if req.method == "POST" and req.url.path == "/v1/payments/payouts":
            return httpx.Response(201, json={"batch_header": {"payout_batch_id": "5UXD2E8A7EBQJ", "batch_status": "PENDING",
                                                              "sender_batch_header": json.loads(req.content)["sender_batch_header"]}})
        if req.method == "GET" and req.url.path == "/v1/payments/payouts/5UXD2E8A7EBQJ":
            return httpx.Response(200, json={"batch_header": {"payout_batch_id": "5UXD2E8A7EBQJ", "batch_status": "SUCCESS"},
                                             "items": [{"payout_item_id": "8AELMXH8UB2P8", "transaction_status": "UNCLAIMED",
                                                        "payout_batch_id": "5UXD2E8A7EBQJ"}]})
        raise AssertionError(f"unexpected {req.method} {req.url}")

    live(handler)
    c = TestClient(app)
    inv = _inv(provider="paypal-sandbox", inv_id="INV2-LIVE-PAYO-0001", number="0051", client_email="sb-buyer@personal.example.com")
    c.post(f"/api/invoices/{inv.id}/split", json={"label": "Designer (Mia)", "email": "mia@studio.example.com", "amount": 360})
    [p] = _pending(c)
    assert not [r for r in seen if "payouts" in r.url.path]  # queuing never touches PayPal
    r = c.post(f"/api/agent/actions/{p['id']}/approve").json()
    assert r["action"]["status"] == "executed" and r["invoice"]["payout_batch_id"] == "5UXD2E8A7EBQJ"
    post = next(x for x in seen if x.method == "POST" and x.url.path == "/v1/payments/payouts")
    body = json.loads(post.content)
    assert str(post.url).startswith(PP) and post.headers["authorization"] == "Bearer tok"
    assert post.headers["paypal-request-id"] == "s2c-INV2-LIVE-PAYO-0001" == body["sender_batch_header"]["sender_batch_id"]
    [item] = body["items"]
    assert item == {**item, "recipient_type": "EMAIL", "receiver": "mia@studio.example.com",
                    "amount": {"value": "360.00", "currency": "USD"}, "sender_item_id": "s2c-INV2-LIVE-PAYO-0001-1"}
    got = c.post(f"/api/invoices/{inv.id}/payout/refresh").json()
    assert got["payout_status"] == "UNCLAIMED" and got["payout_item_id"] == "8AELMXH8UB2P8"


def test_live_money_mode_refuses_payouts(live, monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "live")
    monkeypatch.setenv("PAYPAL_ALLOW_LIVE", "1")
    config.reload_settings()
    inv = _inv(provider="paypal-live", inv_id="INV2-LIVE-PAYO-0002", split_label="Dev", split_email="ana@dev.example.com", split_amount=50.0)
    assert "sandbox-only" in payouts_flow.guard({"invoice_id": inv.id, "receiver": "ana@dev.example.com", "amount": 50.0})
    assert payouts_flow.queue_if_paid(inv) is None


def test_payout_body_builder():
    inv = Invoice(id="X1", number="7", milestone="Build", amount=100.0, currency="EUR", due_date="2026-11-01",
                  client_email="a@b.example.com", split_label="", split_email="c@d.example.com", split_amount=12.5)
    b = po.build_payout_body(inv)
    assert b["items"][0]["amount"] == {"value": "12.50", "currency": "EUR"} and "collaborator" in b["items"][0]["note"]


def test_checkout_capture_queues_the_payout_without_calling_payouts():
    """Orders v2 path: the capture marks the milestone PAID and queues the split's payout (the payout itself waits)."""
    c = TestClient(app)
    inv = _inv(status="SENT", inv_id="INV2-MOCK-CHK-PAYO")
    c.post(f"/api/invoices/{inv.id}/split", json={"label": "Dev (Ana)", "email": "ana@dev.example.com", "amount": 240})
    c.post(f"/api/invoices/{inv.id}/checkout")
    assert c.post(f"/api/demo/{inv.id}/sandbox-card-pay").json()["invoice"]["status"] == "PAID"
    [a] = [x for x in store.actions("pending") if x.tool == "create_payout"]  # queued directly by the capture hook
    assert a.invoice_id == inv.id and not store.get(inv.id).payout_batch_id
