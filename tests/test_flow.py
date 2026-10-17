from fastapi.testclient import TestClient

from app.main import app

BRIEF = ("Hi! I'm Sarah from Bloom Pilates Studio. We need an iOS app where members sign up, book classes, "
         "get reminder notifications, buy a monthly subscription, plus an admin dashboard and an AI assistant for FAQs.")


def test_end_to_end_mock():
    c = TestClient(app)
    h = c.get("/api/health").json()
    assert h["adapters"]["llm"]["mode"] == "mock" and h["adapters"]["payments"]["mode"] == "mock"
    assert c.get("/healthz").json() == {"ok": True}
    r = c.post("/api/scope", json={"requirement": BRIEF}).json()
    scope = r["scope"]
    assert scope["project_type"] == "ios" and len(scope["milestones"]) >= 3
    assert scope["client_name"] == "Bloom Pilates Studio"
    assert r["trace"][0]["tier"] == "fast" and r["trace"][0]["mode"] == "mock"
    pr = c.post("/api/pricing", json={"scope": scope}).json()
    pricing = pr["pricing"]
    assert pricing["low"] < pricing["median"] < pricing["high"]
    assert pricing["grounded"] and pricing["mock"]
    assert [t["tier"] for t in pr["trace"]] == ["tool", "main"]
    qr = c.post("/api/quote", json={"scope": scope, "pricing": pricing, "position": "market"}).json()
    quote = qr["quote"]
    assert quote["total"] == sum(li["amount"] for li in quote["line_items"])
    assert {t["tier"] for t in qr["trace"]} == {"main", "deep"}
    resp = c.post("/api/invoices", json={"scope": scope, "quote": quote, "client_email": "a@b.co"}).json()
    invs = resp["invoices"]
    assert all(i["status"] == "DRAFT" and i["provider"] == "mock" for i in invs)
    assert c.get("/api/invoices").json()[0]["id"] == invs[0]["id"]  # persisted, ordered
    sent = c.post(f"/api/invoices/{invs[0]['id']}/send").json()
    assert sent["status"] == "SENT" and sent["approved_at"]
    assert c.post(f"/api/invoices/{invs[0]['id']}/send").status_code == 409  # can't send twice
    od = c.post(f"/api/demo/{invs[0]['id']}/overdue").json()
    assert od["status"] == "OVERDUE"
    rem = c.post(f"/api/invoices/{invs[0]['id']}/reminder").json()
    assert invs[0]["number"] in rem["subject"] and rem["tone"] == "firm" and rem["provider_note"]
    sr = c.post(f"/api/invoices/{invs[0]['id']}/remind").json()
    assert sr["invoice"]["reminded_at"]
    paid = c.post(f"/api/demo/{invs[0]['id']}/pay").json()
    assert paid["status"] == "PAID"
    events = [e["event"] for e in c.get("/api/audit").json()]
    assert {"drafts_created", "send_approved", "sent", "remind_approved", "demo_pay"} <= set(events)


def test_validation_errors():
    c = TestClient(app)
    assert c.post("/api/scope", json={"requirement": "too short"}).status_code == 422
    assert c.post("/api/invoices/nope/send").status_code == 404
