"""Gap #8: quotes and PayPal invoices in USD, EUR, GBP, CAD, AUD or SGD; mixed-currency roll-ups are grouped or
converted with a dated, sourced rate, never silently summed. USD stays the default. No real network."""
import json
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent, fx, pipeline_stats as ps, store
from app.adapters import checkout, payments
from app.main import app
from app.models import Invoice, Milestone, PricingReference, Quote, Scope

PP = "https://api-m.sandbox.paypal.com"


def _scope(**kw):
    base = dict(project_title="Brand site", client_name="Atelier Nord", summary="s", project_type="web",
                milestones=[Milestone(title="Design", deliverables=["Mockups"], est_hours=10),
                            Milestone(title="Build", deliverables=["Site"], est_hours=20)])
    base.update(kw)
    return Scope(**base)


def _pricing():
    return PricingReference(currency="USD", low=1500, median=2250, high=3000, hourly_low=50, hourly_high=100, rationale="r")


def test_supported_currencies_match_paypal_two_decimal_codes():
    assert fx.SUPPORTED == ("USD", "EUR", "GBP", "CAD", "AUD", "SGD")
    assert fx.normalize("eur") == "EUR"
    for bad in ("JPY", "HUF", "XYZ", ""):  # zero-decimal or unknown codes are refused, not mangled
        if bad:
            with pytest.raises(ValueError):
                fx.normalize(bad)
    assert fx.convert(100, "EUR", "USD") == round(100 * 1.1253, 2) and fx.convert(50, "USD", "USD") == 50
    m = fx.meta()
    assert m["date"] == "2026-10-16" and "European Central Bank" in m["source"] and m["url"].startswith("https://www.ecb.europa.eu/")
    assert "PayPal" in m["note"] and m["usd_per_unit"]["USD"] == 1
    assert fx.fmt(1200, "EUR") == "€1,200" and fx.fmt(75, "GBP", 2) == "£75.00" and fx.fmt(10, "CAD") == "CA$10"


def test_default_quote_is_still_usd_with_no_conversion():
    c = TestClient(app)
    q = c.post("/api/quote", json={"scope": _scope().model_dump(), "pricing": _pricing().model_dump()}).json()["quote"]
    assert q["currency"] == "USD" and q["fx_note"] == "" and q["hourly_rate"] == 75
    assert "$" in q["cover_message"]


def test_eur_quote_converts_the_usd_market_range_and_says_so():
    c = TestClient(app)
    q = c.post("/api/quote", json={"scope": _scope().model_dump(), "pricing": _pricing().model_dump(), "currency": "EUR"}).json()["quote"]
    k = fx.rate("USD", "EUR")
    assert q["currency"] == "EUR" and q["hourly_rate"] == round(75 * k)
    assert q["total"] == sum(li["amount"] for li in q["line_items"])
    assert "ECB reference rates of 2026-10-16" in q["fx_note"] and "not PayPal's rate" in q["fx_note"]
    assert "€" in q["cover_message"] and "$" not in q["cover_message"]
    # an explicit rate is taken as already being in the quote currency
    q2 = c.post("/api/quote", json={"scope": _scope().model_dump(), "pricing": _pricing().model_dump(), "currency": "GBP",
                                    "hourly_rate": 60}).json()["quote"]
    assert q2["currency"] == "GBP" and q2["hourly_rate"] == 60 and q2["total"] == 60 * 30
    bad = c.post("/api/quote", json={"scope": _scope().model_dump(), "pricing": _pricing().model_dump(), "currency": "JPY"})
    assert bad.status_code == 422


def test_budget_currency_is_detected_from_the_brief_and_converted_honestly():
    sc, _ = agent.build_scope("Hi, I'm Lena from Atelier Nord in Berlin. We need a bilingual marketing website with a contact "
                              "form and a small blog. Our budget is €4,000 for the whole project.")
    assert sc.client_budget == 4000 and sc.budget_currency == "EUR"
    _, _, total, note, _ = agent.price_items(sc, _pricing(), 80, "market", "USD")
    assert "stated as €4,000" in note and "ECB reference rates" in note  # USD quote vs EUR budget: converted, labelled
    _, _, _, note_eur, _ = agent.price_items(sc, _pricing(), 80, "market", "EUR")
    assert "€4,000" in note_eur and "converted" not in note_eur  # same currency: compared directly


def _fake_paypal(seen):
    def handler(req: httpx.Request):
        seen.append(req)
        p = req.url.path
        if p == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if p == "/v2/invoicing/generate-next-invoice-number":
            return httpx.Response(200, json={"invoice_number": f"00{len(seen)}"})
        if p == "/v2/invoicing/invoices" and req.method == "POST":
            b = json.loads(req.content)
            cur, val = b["items"][0]["unit_amount"]["currency_code"], b["items"][0]["unit_amount"]["value"]
            return httpx.Response(201, json={"id": f"INV2-CUR-{len(seen):04d}", "status": "DRAFT", "detail": {**b["detail"]},
                                             "amount": {"currency_code": cur, "value": val}, "due_amount": {"currency_code": cur, "value": val}})
        if p == "/v2/checkout/orders" and req.method == "POST":
            return httpx.Response(200, json={"id": "ORD-GBP", "status": "PAYER_ACTION_REQUIRED",
                                             "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORD-GBP"}]})
        raise AssertionError(f"unexpected {req.method} {req.url}")
    return handler


def test_invoice_drafts_carry_the_quote_currency_to_paypal(live):
    seen = []
    live(_fake_paypal(seen))
    c = TestClient(app)
    due = (date.today() + timedelta(days=14)).isoformat()
    quote = Quote(currency="EUR", hourly_rate=70, total=2100,
                  line_items=[{"milestone": "Design", "hours": 10, "amount": 700, "due_date": due, "percent": 33.3},
                              {"milestone": "Build", "hours": 20, "amount": 1400, "due_date": due, "percent": 66.7}],
                  terms=["t"], cover_message="m")
    r = c.post("/api/invoices", json={"scope": _scope().model_dump(), "quote": quote.model_dump(), "client_email": "lena@ateliernord.example.com"}).json()
    assert [i["currency"] for i in r["invoices"]] == ["EUR", "EUR"] and not r["trace"][0]["warning"]
    creates = [json.loads(x.content) for x in seen if x.method == "POST" and x.url.path == "/v2/invoicing/invoices"]
    assert [b["detail"]["currency_code"] for b in creates] == ["EUR", "EUR"]
    assert [b["items"][0]["unit_amount"] for b in creates] == [{"currency_code": "EUR", "value": "700.00"},
                                                               {"currency_code": "EUR", "value": "1400.00"}]


def test_checkout_order_and_mock_drafts_keep_the_currency(live):
    seen = []
    live(_fake_paypal(seen))
    inv = store.save(Invoice(id="INV2-GBP-CHK-0001", number="0201", milestone="Workflow build", amount=450.0, currency="GBP",
                             due_date=date.today().isoformat(), client_email="office@lumendental.example.com", status="SENT",
                             provider="paypal-sandbox"))
    TestClient(app).post(f"/api/invoices/{inv.id}/checkout")
    order = next(json.loads(x.content) for x in seen if x.url.path == "/v2/checkout/orders")
    assert order["purchase_units"][0]["amount"] == {"currency_code": "GBP", "value": "450.00"}
    mock = payments.MockPayments().create_draft(inv.model_copy(update={"currency": "AUD"}), "t", "n")
    assert mock.currency == "AUD" and payments.to_paypal_invoice(mock)["amount"]["currency_code"] == "AUD"


def test_scoreboard_keeps_currencies_apart_and_ranks_in_usd():
    today = date(2026, 10, 14)

    def inv(n, client, cur, amount, status="OVERDUE", off=-5):
        return Invoice(id=f"I{n}", number=f"N{n}", milestone="m", amount=amount, currency=cur, client_name=client,
                       due_date=(today + timedelta(days=off)).isoformat(), client_email=f"{n}@example.com", status=status)
    rows = [inv(1, "Atelier Nord", "EUR", 900), inv(2, "Atelier Nord", "USD", 300), inv(3, "Big Co", "USD", 1000, "SENT", 5)]
    board = ps.client_scoreboard(rows, today=today)
    keys = [c["key"] for c in board]
    assert keys == ["Atelier Nord · EUR", "Atelier Nord · USD", "Big Co"]  # €900 late outranks $300 late
    eur = board[0]
    assert eur["client"] == "Atelier Nord" and eur["currency"] == "EUR" and eur["overdue"] == 900
    assert eur["overdue_usd"] == fx.convert(900, "EUR")


def test_fx_endpoint_and_sample_pipeline_are_multi_currency():
    c = TestClient(app)
    j = c.get("/api/fx").json()
    assert j["default"] == "USD" and [x["code"] for x in j["supported"]] == list(fx.SUPPORTED)
    rows = c.post("/api/demo/seed").json()
    assert {r["client_name"]: r["currency"] for r in rows} == {"Northwind Coffee Roasters": "USD", "Kite & Key Travel": "EUR",
                                                                "Lumen Dental Clinic": "GBP", "Harbor Yoga": "USD"}
    board = {b["client"]: b for b in c.get("/api/pipeline/clients").json()}
    assert board["Kite & Key Travel"]["currency"] == "EUR" and board["Kite & Key Travel"]["owed"] == 900
