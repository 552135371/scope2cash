"""PayPal Orders v2 checkout links + the PAYPAL_MODE switch. httpx.MockTransport stands in for PayPal."""
import json
from datetime import date, timedelta

import httpx
from fastapi.testclient import TestClient

from app import config, store
from app.adapters import checkout
from app.main import app
from app.models import Invoice

PP = "https://api-m.sandbox.paypal.com"


def _inv(status="SENT", provider="paypal-sandbox", inv_id="INV2-CHK-AAAA-BBBB"):
    return store.save(Invoice(id=inv_id, number="0007", milestone="Design", amount=250.0, currency="USD",
                              due_date=(date.today() + timedelta(days=5)).isoformat(), client_email="sb-buyer@personal.example.com",
                              status=status, provider=provider, provider_status="UNPAID"))


class FakePayPal:
    def __init__(self, approved=False):
        self.calls, self.approved, self.invoice_status = [], approved, "UNPAID"

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        try:
            body = json.loads(req.content) if req.content else {}
        except ValueError:  # the OAuth call is form-encoded
            body = {}
        self.calls.append((req.method, p, body))
        assert str(req.url).startswith(PP), req.url
        cap = {"id": "CAP123", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "250.00"}}
        if p == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if p == "/v2/checkout/orders" and req.method == "POST":
            if "card" in body["payment_source"]:
                return httpx.Response(201, json={"id": "ORD-CARD", "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [cap]}}]})
            return httpx.Response(200, json={"id": "ORD1", "status": "PAYER_ACTION_REQUIRED",
                                             "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORD1"}]})
        if p == "/v2/checkout/orders/ORD1" and req.method == "GET":
            return httpx.Response(200, json={"id": "ORD1", "status": "APPROVED" if self.approved else "PAYER_ACTION_REQUIRED"})
        if p == "/v2/checkout/orders/ORD1/capture":
            return httpx.Response(201, json={"id": "ORD1", "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [cap]}}]})
        if p.endswith("/payments") and req.method == "POST":
            self.invoice_status = "MARKED_AS_PAID"
            return httpx.Response(200, json={"payment_id": "EXTR-1"})
        if p.startswith("/v2/invoicing/invoices/") and req.method == "GET":
            return httpx.Response(200, json={"id": p.rsplit("/", 1)[-1], "status": self.invoice_status,
                                             "due_amount": {"currency_code": "USD", "value": "0.00" if self.invoice_status != "UNPAID" else "250.00"}})
        return httpx.Response(404, json={"name": "NOT_FOUND"})


def test_paypal_mode_switch(monkeypatch, live):
    s = config.reload_settings()
    assert s.paypal_mode == "sandbox" and s.payments_live and s.paypal_base == PP
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    assert not config.reload_settings().payments_live
    monkeypatch.setenv("PAYPAL_MODE", "live")  # refused without PAYPAL_ALLOW_LIVE=1
    s = config.reload_settings()
    assert s.paypal_mode == "sandbox" and s.paypal_base == PP
    monkeypatch.delenv("PAYPAL_CLIENT_ID")
    assert config.reload_settings().paypal_mode == "mock"


def test_invoicer_defaults_to_sandbox_business_email(monkeypatch, live):
    monkeypatch.setenv("PAYPAL_INVOICER_EMAIL", "")
    monkeypatch.setenv("PAYPAL_SANDBOX_BUSINESS_EMAIL", "sb-biz@business.example.com")
    assert config.reload_settings().paypal_invoicer_email == "sb-biz@business.example.com"


def test_checkout_link_then_return_url_captures_and_marks_paid(live):
    fake = FakePayPal(); live(fake)
    inv = _inv()
    c = TestClient(app)
    r = c.post(f"/api/invoices/{inv.id}/checkout")
    assert r.status_code == 200, r.text
    got = r.json()["invoice"]
    assert got["order_id"] == "ORD1" and got["checkout_link"].endswith("token=ORD1")
    body = next(b for m, p, b in fake.calls if p == "/v2/checkout/orders")
    ctx = body["payment_source"]["paypal"]["experience_context"]
    assert body["intent"] == "CAPTURE" and ctx["return_url"].endswith("/paypal/return")
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "250.00"}
    # not approved yet: polling leaves it unpaid and never captures
    assert c.get(f"/api/invoices/{inv.id}/checkout").json()["status"] == "SENT"
    assert not any(p.endswith("/capture") for _, p, _ in fake.calls)
    fake.approved = True
    r = c.get("/paypal/return?token=ORD1", follow_redirects=False)
    assert r.status_code == 303 and "checkout=paid" in r.headers["location"]
    done = store.get(inv.id)
    assert done.status == "PAID" and done.capture_id == "CAP123" and done.amount_due == 0
    pay = next(b for m, p, b in fake.calls if p.endswith("/payments"))
    assert pay["method"] == "PAYPAL" and pay["amount"]["value"] == "250.00" and "CAP123" in pay["note"]


def test_checkout_requires_sent_invoice(live):
    live(FakePayPal())
    inv = _inv(status="DRAFT")
    assert TestClient(app).post(f"/api/invoices/{inv.id}/checkout").status_code == 409


def test_sandbox_card_pay_runs_real_order_shape(live):
    fake = FakePayPal(); live(fake)
    inv = _inv()
    r = TestClient(app).post(f"/api/demo/{inv.id}/sandbox-card-pay")
    assert r.status_code == 200, r.text
    assert r.json()["invoice"]["status"] == "PAID" and r.json()["trace"][0]["mode"] == "live"
    body = next(b for m, p, b in fake.calls if p == "/v2/checkout/orders")
    assert body["payment_source"]["card"]["number"] == checkout.SANDBOX_TEST_CARD["number"]


def test_sandbox_card_pay_refused_in_live_mode(monkeypatch, live):
    monkeypatch.setenv("PAYPAL_MODE", "live"); monkeypatch.setenv("PAYPAL_ALLOW_LIVE", "1")
    config.reload_settings()
    live(FakePayPal())
    inv = _inv(provider="paypal-live")
    assert TestClient(app).post(f"/api/demo/{inv.id}/sandbox-card-pay").status_code == 400


def test_mock_invoice_checkout_is_mock():
    inv = _inv(provider="mock", inv_id="INV2-MOCK-AAAA-BBBB")
    c = TestClient(app)
    r = c.post(f"/api/invoices/{inv.id}/checkout").json()
    assert r["invoice"]["order_id"].startswith("MOCKORDER") and r["trace"][0]["mode"] == "mock"
    assert c.post(f"/api/demo/{inv.id}/sandbox-card-pay").json()["invoice"]["status"] == "PAID"


def test_base_url_prefers_public_then_render(monkeypatch):
    from starlette.requests import Request
    from app.checkout_routes import _base_url
    req = Request({"type": "http", "scheme": "http", "server": ("internal", 10000), "path": "/", "headers": []})
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert _base_url(req) == "http://internal:10000"
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://scope2cash.onrender.com")
    assert _base_url(req) == "https://scope2cash.onrender.com"
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.example.com/")
    assert _base_url(req) == "https://demo.example.com"
