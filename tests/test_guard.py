"""Public-demo protection: rate limit, daily live budget, sandbox-only recipients."""
from fastapi.testclient import TestClient

from app import config, guard
from app.main import app

BRIEF = "We need a simple iOS app for our yoga studio: class schedule, booking, push reminders. Budget $4,000."


def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setenv("SCOPE2CASH_RATE_LIMIT", "2")
    config.reload_settings()
    c = TestClient(app)
    assert c.post("/api/scope", json={"requirement": BRIEF}).status_code == 200
    assert c.post("/api/scope", json={"requirement": BRIEF}).status_code == 200
    r = c.post("/api/scope", json={"requirement": BRIEF})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert c.get("/api/health").status_code == 200  # reads are never limited


def test_public_demo_defaults_to_30_per_hour(monkeypatch):
    monkeypatch.setenv("SCOPE2CASH_PUBLIC_DEMO", "1")
    assert config.reload_settings().effective_rate_limit == 30


def test_daily_live_cap_switches_to_offline_with_visible_warning(live, monkeypatch):
    monkeypatch.setenv("SCOPE2CASH_DAILY_LIVE_CAP", "1")
    config.reload_settings()
    assert guard.take_live_run()  # spend today's only live run
    # No HTTP handler installed: any network call would fail the test, so this proves the request ran offline.
    r = TestClient(app).post("/api/scope", json={"requirement": BRIEF})
    assert r.status_code == 200
    trace = r.json()["trace"]
    assert trace[0]["step"] == "Daily live budget" and "budget" in trace[0]["warning"]
    assert all(t["mode"] in ("mock", "fallback") for t in trace)
    assert config.get_settings().llm_live  # the switch is per request, not global


def test_public_demo_only_invoices_sandbox_addresses(monkeypatch):
    monkeypatch.setenv("SCOPE2CASH_PUBLIC_DEMO", "1")
    config.reload_settings()
    c = TestClient(app)
    scope = c.post("/api/scope", json={"requirement": BRIEF}).json()["scope"]
    pricing = c.post("/api/pricing", json={"scope": scope}).json()["pricing"]
    quote = c.post("/api/quote", json={"scope": scope, "pricing": pricing}).json()["quote"]
    bad = c.post("/api/invoices", json={"scope": scope, "quote": quote, "client_email": "someone@gmail.com"})
    assert bad.status_code == 400 and "sandbox" in bad.json()["detail"]
    ok = c.post("/api/invoices", json={"scope": scope, "quote": quote, "client_email": "sb-buyer@personal.example.com"})
    assert ok.status_code == 200
    h = c.get("/api/health").json()
    assert h["public_demo"] and h["notices"] and not h["warnings"]
