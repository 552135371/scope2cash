"""Hosted-demo resilience: free hosts sleep and wipe SQLite on restart, so a judge who opens the URL fresh must
still get a populated pipeline and a fast path to the PayPal steps."""
import json
import re
from datetime import date

from fastapi.testclient import TestClient

from app import config, guard, sample_project, store
from app.demo_data import ROWS, ensure_samples
from app.main import app


def _boot(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.reload_settings()
    return TestClient(app)


def test_autoseed_on_boot_in_public_demo_is_idempotent(monkeypatch):
    c = _boot(monkeypatch, SCOPE2CASH_PUBLIC_DEMO="1")
    with c:  # runs the lifespan (= a fresh boot)
        rows = c.get("/api/invoices").json()
    assert len(rows) == len(ROWS) and all(r["sample"] for r in rows)
    with c:  # a second boot against the same store does not duplicate
        assert len(c.get("/api/invoices").json()) == len(ROWS)
    assert any(e["event"] == "demo_autoseed" for e in store.audit_log())


def test_autoseed_off_locally_and_opt_out(monkeypatch):
    with _boot(monkeypatch) as c:
        assert c.get("/api/invoices").json() == []
    with _boot(monkeypatch, SCOPE2CASH_PUBLIC_DEMO="1", SCOPE2CASH_AUTOSEED="0") as c:
        assert c.get("/api/invoices").json() == []


def test_ensure_samples_keeps_real_invoices():
    assert ensure_samples() == len(ROWS)
    assert ensure_samples() == 0
    store.delete_samples()
    assert ensure_samples() == len(ROWS)
    assert len(store.all_invoices()) == len(ROWS)


def test_instant_sample_project_is_replayed_and_repriced_from_today(monkeypatch):
    # conftest blocks every network call, so this also proves no LLM/search request is made
    c = _boot(monkeypatch, SCOPE2CASH_PUBLIC_DEMO="1", SCOPE2CASH_DAILY_LIVE_CAP="1")
    r = c.post("/api/demo/sample-project").json()
    q, sc = r["quote"], r["scope"]
    assert sc["client_name"] == "Bloom Pilates Studio" and "Bloom Pilates" in r["brief"]
    assert len(q["line_items"]) == len(sc["milestones"]) >= 3
    assert q["total"] == sum(li["amount"] for li in q["line_items"])
    assert all(li["due_date"] > date.today().isoformat() for li in q["line_items"])
    assert q["cover_message"] and len(q["negotiation_tips"]) >= 2 and q["terms"]
    assert {t["mode"] for t in r["trace"]} == {"replay"}
    assert all("no model call" in t["note"] for t in r["trace"][:-1])
    assert guard.live_runs_today() == 0  # does not spend the public demo's daily live budget


def test_instant_sample_project_then_real_payment_steps():
    c = TestClient(app)
    r = c.post("/api/demo/sample-project").json()
    invs = c.post("/api/invoices", json={"scope": r["scope"], "quote": r["quote"],
                                         "client_email": "sarah@bloompilates.example.com"}).json()["invoices"]
    assert len(invs) == len(r["quote"]["line_items"]) and all(i["status"] == "DRAFT" for i in invs)
    assert c.post(f"/api/invoices/{invs[0]['id']}/send").json()["status"] == "SENT"
    assert c.post(f"/api/demo/{invs[0]['id']}/pay").json()["status"] == "PAID"
    ev = c.get("/api/webhooks/events").json()
    assert ev and ev[0]["event_type"] == "INVOICING.INVOICE.PAID"


def test_recorded_fixture_is_clean():
    raw = sample_project.FIXTURE.read_text()
    d = json.loads(raw)
    assert d["llm"]["provider"] and d["recorded_trace"]
    emails = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", raw)
    assert all(e.endswith("example.com") for e in emails), emails
    assert not re.search(r"(sk-[A-Za-z0-9]{16,}|tvly-[A-Za-z0-9]{8,}|A21AA[\w-]{20,})", raw)


def test_restart_messages_for_judges(monkeypatch):
    c = _boot(monkeypatch, SCOPE2CASH_PUBLIC_DEMO="1")
    h = c.get("/api/health").json()
    assert any("Free hosting" in n for n in h["notices"]) and h["booted_at"]
    r = c.post("/api/invoices/INV2-GONE-0000/send")
    assert r.status_code == 404 and "restarted" in r.json()["detail"]


def test_agent_list_is_one_page_of_known_invoices(live):
    """A busy (shared) PayPal sandbox must not make the collections agent page through foreign invoices."""
    import httpx
    from app import paypal_agent
    from app.models import Invoice
    mine = store.save(Invoice(id="INV2-MINE-0001", number="S2C-1", milestone="Build", amount=500, currency="USD",
                              due_date="2026-01-01", client_email="a@x.example.com", status="SENT", provider="paypal-sandbox",
                              client_name="Bloom", project="App", created_at=store.now()))
    ensure_samples()
    seen = []

    def handler(req: httpx.Request):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21AA-test", "expires_in": 32400})
        if req.url.path == "/v2/invoicing/invoices":
            seen.append(dict(req.url.params))
            items = [{"id": mine.id, "status": "UNPAID", "detail": {"invoice_number": "S2C-1"}}] + \
                    [{"id": f"INV2-FOREIGN-{n:04d}", "status": "UNPAID", "detail": {}} for n in range(60)]
            return httpx.Response(200, json={"total_items": 61, "total_pages": 4, "items": items})
        raise AssertionError(req.url)

    live(handler)
    cl = paypal_agent.HybridToolkitClient()
    page1 = cl.get("/v2/invoicing/invoices?page=1&page_size=20")
    ids = [it["id"] for it in page1["items"]]
    assert mine.id in ids and not any("FOREIGN" in i for i in ids)
    assert len(ids) == 1 + len(ROWS) and page1["total_pages"] == 1 and page1["total_items"] == len(ids)
    assert page1["items"][0]["x_scope2cash"]["client"] == "Bloom"
    assert cl.get("/v2/invoicing/invoices?page=2")["items"] == []
    assert seen == [{"page": "1", "page_size": "100", "total_required": "true"}]
