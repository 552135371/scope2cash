"""scripts/paypal_sandbox_smoke.py: the one-command go-live check. Runs without credentials: httpx.MockTransport
stands in for the PayPal sandbox and the deployed app, and the conftest blocks every real network call."""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("paypal_sandbox_smoke", ROOT / "scripts" / "paypal_sandbox_smoke.py")
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

APP = "https://scope2cash.example.onrender.com"
BUYER = "sb-buyer@personal.example.com"
INV_ID = "INV2-SMOK-E000-TEST-0001"


class FakeSandbox:
    def __init__(self, hooks=None, pay_after_gets=None, health=None, token_status=200):
        self.calls, self.status, self.gets = [], "DRAFT", 0
        self.hooks = hooks if hooks is not None else [{"id": "WH-1", "url": APP + "/api/paypal/webhook",
                                                       "event_types": [{"name": "INVOICING.INVOICE.PAID"}]}]
        self.pay_after_gets, self.token_status = pay_after_gets, token_status
        self.health = health or {"version": "0.3", "adapters": {"payments": {"mode": "live"}, "webhooks": {"mode": "live"},
                                                                 "llm": {"mode": "live", "provider_id": "deepseek"}}}
        self.due = (date.today() + timedelta(days=7)).isoformat()

    def inv(self):
        return {"id": INV_ID, "status": self.status, "detail": {"invoice_number": "0007", "currency_code": "USD",
                "payment_term": {"due_date": self.due}, "metadata": {"recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#" + INV_ID}},
                "amount": {"currency_code": "USD", "value": "1.00"}, "due_amount": {"currency_code": "USD", "value": "1.00"}}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        p = req.url.path
        if req.url.host != "api-m.sandbox.paypal.com":
            assert str(req.url) == APP + "/api/health", req.url
            return httpx.Response(200, json=self.health)
        if p == "/v1/oauth2/token":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client", "error_description": "Client Authentication failed"})
            return httpx.Response(200, json={"access_token": "A21AA-SMOKE", "expires_in": 32400})
        if p == "/v2/invoicing/generate-next-invoice-number":
            return httpx.Response(200, json={"invoice_number": "0007"})
        if p == "/v2/invoicing/invoices" and req.method == "POST":
            return httpx.Response(201, json=self.inv())
        if p.endswith("/send"):
            self.status = "UNPAID"
            return httpx.Response(202, json={"href": "https://www.sandbox.paypal.com/invoice/p/#" + INV_ID, "rel": "payer-view", "method": "GET"})
        if req.method == "GET" and p.startswith("/v2/invoicing/invoices/"):
            self.gets += 1
            if self.pay_after_gets and self.gets >= self.pay_after_gets:
                self.status = "PAID"
            return httpx.Response(200, json=self.inv())
        if p == "/v1/notifications/webhooks":
            return httpx.Response(200, json={"webhooks": self.hooks})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    def paths(self):
        return [(c.method, c.url.path) for c in self.calls]


def _run(argv, tmp_path):
    lines = []
    code = smoke.run(argv, out=lines.append, sleep=lambda s: None, report_path=tmp_path / "smoke.md")
    return code, "\n".join(lines)


def test_missing_credentials_fail_clearly_without_network(monkeypatch, tmp_path):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "SCOPE2CASH_FORCE_MOCK"):
        monkeypatch.setenv(k, "")
    code, out = _run(["--buyer", BUYER, "--no-report"], tmp_path)  # conftest transport raises on any request
    assert code == 2
    assert "missing PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET" in out and "Nothing was sent to PayPal" in out


def test_live_mode_force_mock_and_real_recipient_are_refused(live, monkeypatch, tmp_path):
    monkeypatch.setenv("PAYPAL_ENV", "")
    monkeypatch.setenv("PAYPAL_MODE", "live")
    monkeypatch.setenv("SCOPE2CASH_FORCE_MOCK", "1")
    code, out = _run(["--buyer", "someone@gmail.com", "--no-report"], tmp_path)
    assert code == 2
    assert "only runs against the sandbox" in out and "SCOPE2CASH_FORCE_MOCK" in out and "not a sandbox address" in out
    monkeypatch.setenv("PAYPAL_MODE", "")
    monkeypatch.setenv("PAYPAL_ENV", "live")  # the older variable is checked too
    code, out = _run(["--buyer", BUYER, "--no-report"], tmp_path)
    assert code == 2 and "only runs against the sandbox" in out


def test_full_go_live_flow_passes_and_never_prints_secrets(live, tmp_path):
    fake = FakeSandbox(pay_after_gets=4)
    live(fake)
    code, out = _run(["--buyer", BUYER, "--app-url", APP + "/", "--require-webhook", "--wait-paid", "60"], tmp_path)
    assert code == 0, out
    p = fake.paths()
    assert p[0] == ("POST", "/v1/oauth2/token")
    assert ("POST", "/v2/invoicing/invoices") in p and ("POST", f"/v2/invoicing/invoices/{INV_ID}/send") in p
    assert ("GET", "/v1/notifications/webhooks") in p and ("GET", "/api/health") in p
    create = next(c for c in fake.calls if c.url.path == "/v2/invoicing/invoices")
    body = json.loads(create.content)
    assert body["primary_recipients"][0]["billing_info"]["email_address"] == BUYER
    assert body["items"][0]["unit_amount"]["value"] == "1.00"
    for step in ("oauth", "draft", "send", "status", "webhook", "app", "paid"):
        assert any(line.startswith("[PASS]") and f" {step} " in line + " " for line in out.splitlines()), (step, out)
    assert "set PAYPAL_WEBHOOK_ID=WH-1" in out  # tells the operator which ID to paste
    assert "test-secret" not in out and "A21AA-SMOKE" not in out and "test-client" not in out
    report = (tmp_path / "smoke.md").read_text()
    assert "**PASS**" in report and "test-secret" not in report


def test_missing_webhook_warns_by_default_and_fails_when_required(live, tmp_path):
    live(FakeSandbox(hooks=[]))
    code, out = _run(["--buyer", BUYER, "--no-report"], tmp_path)
    assert code == 0 and "[WARN] webhook" in out
    live(FakeSandbox(hooks=[{"id": "WH-2", "url": APP + "/api/paypal/webhook", "event_types": [{"name": "INVOICING.INVOICE.UPDATED"}]}]))
    code, out = _run(["--buyer", BUYER, "--app-url", APP, "--require-webhook", "--no-report"], tmp_path)
    assert code == 1 and "lacks INVOICING.INVOICE.PAID" in out


def test_app_still_in_mock_mode_fails(live, tmp_path):
    live(FakeSandbox(health={"version": "0.3", "adapters": {"payments": {"mode": "mock"}, "webhooks": {"mode": "mock"}, "llm": {"mode": "live"}}}))
    code, out = _run(["--buyer", BUYER, "--app-url", APP, "--no-report"], tmp_path)
    assert code == 1 and "[FAIL] app" in out and "PAYPAL_CLIENT_ID/SECRET on the server" in out


def test_bad_credentials_stop_after_oauth(live, tmp_path):
    fake = FakeSandbox(token_status=401)
    live(fake)
    code, out = _run(["--buyer", BUYER, "--no-report"], tmp_path)
    assert code == 1 and "[FAIL] oauth" in out and "Sandbox* tab" in out
    assert all(path == "/v1/oauth2/token" for _, path in fake.paths())
