"""Pipeline dashboard roll-ups behind the AG Charts cash-flow chart and the 'who still owes what' scoreboard."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import fx, pipeline_stats as ps, store
from app.demo_data import sample_invoices
from app.main import app
from app.models import AgentAction, Invoice

TODAY = date(2026, 10, 14)  # a Wednesday; its week starts Mon 2026-10-12


def inv(n, status, due_offset, amount=100.0, client="Acme", amount_due=None, paid_at="", days_overdue=0):
    return Invoice(id=f"I{n}", number=f"N{n}", milestone="m", amount=amount, currency="USD",
                   due_date=(TODAY + timedelta(days=due_offset)).isoformat(), client_email=f"{client.lower()}@example.com",
                   status=status, amount_due=amount_due, paid_at=paid_at, client_name=client, days_overdue=days_overdue)


def test_cashflow_buckets_by_week_and_lists_ids():
    rows = [inv(1, "PAID", -20, 600, paid_at="2026-10-13T09:00:00+00:00"),  # paid this week, due 3 weeks ago
            inv(2, "SENT", 6, 900),                                         # not late -> expected, next week
            inv(3, "OVERDUE", -9, 1800),                                    # late -> overdue, by due week
            inv(4, "PARTIALLY_PAID", -1, 2400, amount_due=1200),            # late, only the balance counts
            inv(5, "DRAFT", 20, 1400),
            inv(6, "CANCELLED", 0, 999)]                                    # carries no cash
    cf = ps.cashflow(rows, TODAY)
    weeks = {w["week"]: w for w in cf["weeks"]}
    assert cf["totals"] == {"collected": 600, "expected": 900, "overdue": 3000, "drafts": 1400}
    assert weeks["2026-10-12"]["current"] and weeks["2026-10-12"]["collected"] == 600
    assert weeks["2026-10-12"]["ids"]["overdue"] == ["I4"] and weeks["2026-10-12"]["overdue"] == 1200
    assert weeks["2026-10-05"]["ids"]["overdue"] == ["I3"]
    assert weeks["2026-10-19"]["expected"] == 900 and weeks["2026-11-02"]["drafts"] == 1400
    # contiguous Monday-start weeks, gaps filled with zeros, labels without leading zeros
    keys = [date.fromisoformat(w["week"]) for w in cf["weeks"]]
    assert all(k.weekday() == 0 for k in keys) and all((b - a).days == 7 for a, b in zip(keys, keys[1:]))
    assert weeks["2026-10-05"]["label"] == "Oct 5"
    assert all("I6" not in sum(w["ids"].values(), []) for w in cf["weeks"])


def test_sent_invoice_past_due_counts_as_overdue_even_before_paypal_flags_it():
    cf = ps.cashflow([inv(1, "SENT", -3, 500)], TODAY)
    assert cf["totals"]["overdue"] == 500 and cf["totals"]["expected"] == 0


def test_cashflow_window_is_capped_and_clamps_outliers():
    rows = [inv(1, "SENT", 7 * 40, 100), inv(2, "PAID", -7 * 40, 100, paid_at=(TODAY - timedelta(weeks=40)).isoformat()),
            inv(3, "SENT", 7, 100)]
    cf = ps.cashflow(rows, TODAY)
    assert len(cf["weeks"]) == ps.MAX_WEEKS and cf["clamped"] == 2
    assert sum(w["collected"] + w["expected"] for w in cf["weeks"]) == 300  # nothing dropped
    assert any(w["current"] for w in cf["weeks"])
    assert cf["weeks"][0]["label"].startswith("≤ ") and cf["weeks"][-1]["label"].endswith(" +")  # edges say so
    assert cf["weeks"][0]["clamped"] and cf["weeks"][-1]["clamped"] and not any(w["clamped"] for w in cf["weeks"][1:-1])


def test_cashflow_empty():
    assert ps.cashflow([], TODAY)["weeks"] == []


def test_scoreboard_puts_who_needs_a_nudge_first():
    rows = sample_invoices()
    by_num = {i.number: i for i in rows}
    pending = [AgentAction(id="a1", tool="send_invoice_reminder", params={}, invoice_id=by_num["S2C-SAMPLE-07"].id),
               AgentAction(id="a2", tool="send_invoice_reminder", params={}, invoice_id=by_num["S2C-SAMPLE-01"].id)]  # PAID: stale
    board = ps.client_scoreboard(rows, pending)
    assert [c["client"] for c in board] == ["Northwind Coffee Roasters", "Harbor Yoga", "Lumen Dental Clinic", "Kite & Key Travel"]
    harbor = next(c for c in board if c["client"] == "Harbor Yoga")
    assert harbor["billed"] == 3600 and harbor["collected"] == 1200 and harbor["owed"] == 2400 and harbor["overdue"] == 1200
    assert harbor["oldest_late"] == 17 and harbor["mood"] == "time for a call" and harbor["paid_pct"] == 33
    assert harbor["pending_nudges"] == 1
    assert next(c for c in board if c["client"] == "Northwind Coffee Roasters")["pending_nudges"] == 0  # stale action ignored
    kite = board[-1]
    assert kite["owed"] == 900 and kite["overdue"] == 0 and kite["drafts"] == 1400 and kite["mood"] == "on track"
    assert kite["next_due"] == (date.today() + timedelta(days=6)).isoformat()
    lumen = next(c for c in board if c["client"] == "Lumen Dental Clinic")
    assert lumen["mood"] == "gentle nudge" and all(c["sample"] for c in board)


def test_mood_labels_stay_friendly():
    assert [ps.mood(0, 0), ps.mood(10, 0), ps.mood(10, 3), ps.mood(10, 10), ps.mood(10, 30)] == \
        ["all square", "on track", "gentle nudge", "friendly follow-up", "time for a call"]


def test_dashboard_endpoints_are_read_only_and_follow_the_grid_filter():
    c = TestClient(app)
    rows = c.post("/api/demo/seed").json()
    audit_before = len(store.audit_log())
    board = c.get("/api/pipeline/clients").json()
    assert len(board) == 4 and board[0]["client"] == "Northwind Coffee Roasters"
    allcf = c.post("/api/pipeline/cashflow", json={}).json()
    # The samples bill in USD, EUR (Kite & Key) and GBP (Lumen): exact per-currency totals, never summed as if one currency...
    assert allcf["mixed"] is True and allcf["currency"] == "USD" and allcf["fx"]["date"] == fx.RATES_DATE
    assert allcf["totals_by_currency"] == {
        "USD": {"collected": 600, "expected": 1200, "overdue": 3000, "drafts": 0},
        "EUR": {"collected": 0, "expected": 900, "overdue": 0, "drafts": 1400},
        "GBP": {"collected": 300, "expected": 0, "overdue": 450, "drafts": 0}}
    # ...and the chart's stacked bars are the USD equivalent at the dated ECB snapshot
    assert allcf["totals"] == {"collected": round(600 + fx.convert(300, "GBP"), 2), "expected": round(1200 + fx.convert(900, "EUR"), 2),
                               "overdue": round(3000 + fx.convert(450, "GBP"), 2), "drafts": fx.convert(1400, "EUR")}
    harbor = [r["id"] for r in rows if r["client_name"] == "Harbor Yoga"]
    sub = c.post("/api/pipeline/cashflow", json={"invoice_ids": harbor}).json()
    assert sub["totals"] == {"collected": 0, "expected": 1200, "overdue": 1200, "drafts": 0}
    assert sub["mixed"] is False and sub["currency"] == "USD" and sub["fx"] is None  # one currency: no conversion at all
    kite = [r["id"] for r in rows if r["client_name"] == "Kite & Key Travel"]
    eur = c.post("/api/pipeline/cashflow", json={"invoice_ids": kite}).json()
    assert eur["currency"] == "EUR" and not eur["mixed"] and eur["totals"]["expected"] == 900  # all-EUR view stays in EUR
    assert c.post("/api/pipeline/cashflow", json={"invoice_ids": []}).json()["weeks"] == []
    assert len(store.audit_log()) == audit_before  # roll-ups never write, send or call PayPal
    assert [i.status for i in store.all_invoices()] == [r["status"] for r in rows]


def test_dashboard_ships_only_pinned_community_builds_and_keeps_approval_clicks():
    c = TestClient(app)
    html = c.get("/").text
    assert "ag-charts-community@14.2.0/dist/umd/ag-charts-community.min.js" in html
    for paid in ("ag-grid-enterprise", "ag-charts-enterprise", "ag-studio", "LicenseManager"):
        assert paid not in html, paid
    for el in ('id="cashchart"', 'id="board"', 'id="views"', 'id="csv"', 'id="active-filters"'):
        assert el in html, el
    js = c.get("/static/pipeline.js").text
    assert "enterprise" not in js.lower() and "setLicenseKey" not in js
    assert "AgCharts.create" in js and "exportDataAsCsv" in js and "/api/pipeline/cashflow" in js
    # Agent proposals in the grid only ever call the same approve/reject endpoints as the panel (one click per action).
    assert "/api/agent/actions/${pid}/${verb}" in js and "send_invoice_reminder" not in js
