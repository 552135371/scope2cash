import os
os.environ["SCOPE2CASH_FORCE_MOCK"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

BRIEF = ("Hi! I'm Sarah from Bloom Pilates Studio. We need an iOS app where members sign up, book classes, "
         "get reminder notifications, buy a monthly subscription, plus an admin dashboard and an AI assistant for FAQs.")


def test_end_to_end():
    c = TestClient(app)
    assert c.get("/api/health").json()["adapters"]["llm"]["mode"] == "mock"
    scope = c.post("/api/scope", json={"requirement": BRIEF}).json()["scope"]
    assert scope["project_type"] == "ios" and len(scope["milestones"]) >= 3
    pricing = c.post("/api/pricing", json={"scope": scope}).json()["pricing"]
    assert pricing["low"] < pricing["median"] < pricing["high"]
    quote = c.post("/api/quote", json={"scope": scope, "pricing": pricing, "position": "market"}).json()["quote"]
    assert quote["total"] == sum(li["amount"] for li in quote["line_items"])
    invs = c.post("/api/invoices", json={"scope": scope, "quote": quote, "client_email": "a@b.co"}).json()
    assert all(i["status"] == "DRAFT" for i in invs)
    sent = c.post(f"/api/invoices/{invs[0]['id']}/send").json()
    assert sent["status"] == "SENT"
    od = c.post(f"/api/demo/{invs[0]['id']}/overdue").json()
    assert od["status"] == "OVERDUE"
    rem = c.post(f"/api/invoices/{invs[0]['id']}/reminder").json()
    assert invs[0]["number"] in rem["subject"] and rem["tone"] == "firm"
    paid = c.post(f"/api/demo/{invs[0]['id']}/pay").json()
    assert paid["status"] == "PAID"
