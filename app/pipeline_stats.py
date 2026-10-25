"""Read-only roll-ups for the Pipeline dashboard: a weekly cash-flow series (AG Charts) and a per-client
"who still owes what" scoreboard (second AG Grid). Pure functions over stored invoices: no PayPal or LLM calls,
so the dashboard can re-query them on every grid filter change for free.

Currencies: amounts in different currencies are never added up silently. If every invoice in view shares one currency,
the series are in that currency. If they are mixed, the series are converted to USD at the fx snapshot (app/fx.py,
dated ECB reference rates), the response says so (`mixed`, `currency`, `fx`) and keeps the exact per-currency
amounts next to every converted number. The scoreboard keeps one row per client and currency, in that currency."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from . import fx
from .models import AgentAction, Invoice

OPEN = ("SENT", "OVERDUE", "PARTIALLY_PAID")
CLOSED = ("CANCELLED", "REFUNDED")
SERIES = ("collected", "expected", "overdue", "drafts")
MAX_WEEKS = 16  # keep the chart readable; rows outside the window are clamped into its first/last week


def amount_due(inv: Invoice) -> float:
    if inv.status in ("PAID",) + CLOSED:
        return 0.0
    return float(inv.amount_due if inv.amount_due is not None else inv.amount)


def days_late(inv: Invoice, today: date) -> int:
    """Same rule as the grid's 'Days late' column: open invoices only, the larger of PayPal's count and the calendar."""
    if inv.status not in OPEN:
        return 0
    try:
        cal = (today - date.fromisoformat(inv.due_date[:10])).days
    except ValueError:
        cal = 0
    return max(inv.days_overdue or 0, cal, 0)


def _day(s: str, fallback: str) -> date:
    for v in (s, fallback):
        try:
            return date.fromisoformat((v or "")[:10])
        except ValueError:
            continue
    return date.today()


def week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


def bucket(inv: Invoice, today: date) -> Optional[str]:
    """Which cash-flow series an invoice belongs to, or None if it carries no cash (cancelled/refunded)."""
    if inv.status == "PAID":
        return "collected"
    if inv.status == "DRAFT":
        return "drafts"
    if inv.status in OPEN:
        return "overdue" if days_late(inv, today) > 0 else "expected"
    return None


def cash_date(inv: Invoice) -> date:
    """Collected money is dated by when PayPal said it was paid; everything else by its due date."""
    return _day(inv.paid_at, inv.due_date) if inv.status == "PAID" else _day(inv.due_date, inv.created_at)


def reporting_currency(invoices: Iterable[Invoice]) -> Tuple[str, bool]:
    """(currency to report in, mixed?): the shared currency if there is one, else USD with conversion."""
    curs = {(i.currency or "USD").upper() for i in invoices}
    return (curs.pop(), False) if len(curs) == 1 else (fx.REPORTING, len(curs) > 1)


def to_reporting(amount: float, currency: str, reporting: str) -> float:
    cur = (currency or "USD").upper()
    if cur == reporting:
        return float(amount)
    try:
        return fx.convert(amount, cur, reporting)
    except (ValueError, KeyError):  # a currency without a snapshot rate is reported natively only, never guessed
        return 0.0


def cashflow(invoices: Iterable[Invoice], today: Optional[date] = None) -> Dict:
    """Weekly stacked series: collected (paid, by paid date), expected (open, not late, by due date), overdue (open
    and late, by due date) and drafts (not sent yet, by due date). Each week lists the invoice ids behind each
    series so the UI can filter the grid to exactly the rows behind a clicked bar, and the exact per-currency
    amounts behind each (possibly converted) bar."""
    today = today or date.today()
    rows = [(inv, bucket(inv, today)) for inv in invoices]
    rows = [(inv, b) for inv, b in rows if b]
    reporting, mixed = reporting_currency(inv for inv, _ in rows) if rows else (fx.REPORTING, False)
    base = {"as_of": today.isoformat(), "currency": reporting, "mixed": mixed, "fx": fx.meta() if mixed else None}
    if not rows:
        return {**base, "weeks": [], "totals": {k: 0.0 for k in SERIES}, "totals_by_currency": {}, "clamped": 0}
    weeks = sorted({week_of(cash_date(inv)) for inv, _ in rows} | {week_of(today)})
    first, last = weeks[0], weeks[-1]
    clamped = 0
    if (last - first).days // 7 + 1 > MAX_WEEKS:  # centre the window on today, then clamp
        first = max(first, week_of(today) - timedelta(weeks=MAX_WEEKS // 2))
        last = first + timedelta(weeks=MAX_WEEKS - 1)
    out: Dict[date, Dict] = {}
    w = first
    while w <= last:
        out[w] = {"week": w.isoformat(), "label": w.strftime("%b %d").replace(" 0", " "),
                  "current": w == week_of(today), "clamped": False, **{k: 0.0 for k in SERIES}, "ids": {k: [] for k in SERIES},
                  "native": {k: {} for k in SERIES}}
        w += timedelta(weeks=1)
    totals = {k: 0.0 for k in SERIES}
    by_cur: Dict[str, Dict[str, float]] = {}
    for inv, b in rows:
        wk = week_of(cash_date(inv))
        if wk < first or wk > last:
            clamped += 1
            edge = out[first] if wk < first else out[last]
            edge["label"] = ("≤ " + edge["label"].lstrip("≤ ")) if wk < first else (edge["label"].rstrip(" +") + " +")
            edge["clamped"] = True
            wk = min(max(wk, first), last)
        native = float(inv.amount) if b in ("collected", "drafts") else amount_due(inv)
        cur = (inv.currency or "USD").upper()
        value = to_reporting(native, cur, reporting)
        out[wk][b] = round(out[wk][b] + value, 2)
        out[wk]["ids"][b].append(inv.id)
        out[wk]["native"][b][cur] = round(out[wk]["native"][b].get(cur, 0.0) + native, 2)
        totals[b] = round(totals[b] + value, 2)
        tc = by_cur.setdefault(cur, {k: 0.0 for k in SERIES})
        tc[b] = round(tc[b] + native, 2)
    return {**base, "weeks": list(out.values()), "totals": totals, "totals_by_currency": by_cur, "clamped": clamped}


def mood(owed: float, oldest_late: int) -> str:
    """Friendly-scoreboard label (Nudge-bot tone: heads-ups, not demands)."""
    if owed <= 0:
        return "all square"
    if oldest_late <= 0:
        return "on track"
    if oldest_late <= 7:
        return "gentle nudge"
    if oldest_late <= 14:
        return "friendly follow-up"
    return "time for a call"


def client_scoreboard(invoices: Iterable[Invoice], pending: Iterable[AgentAction] = (), today: Optional[date] = None) -> List[Dict]:
    """One row per client (and per currency, if a client is billed in more than one): billed, collected, still owed,
    overdue, oldest lateness, next due date, drafts waiting for approval and agent proposals waiting for approval,
    all in that row's own currency. Sorted so the clients who need attention come first (compared in USD at the fx
    snapshot, so a late €900 and a late $900 rank sensibly)."""
    today = today or date.today()
    invs = list(invoices)
    by_inv = {i.id: i for i in invs}
    curs_of: Dict[str, set] = {}
    for inv in invs:
        if inv.status not in CLOSED:
            curs_of.setdefault(inv.client_name or inv.client_email, set()).add((inv.currency or "USD").upper())

    def key_of(inv: Invoice) -> str:
        name, cur = inv.client_name or inv.client_email, (inv.currency or "USD").upper()
        return f"{name} · {cur}" if len(curs_of.get(name, ())) > 1 else name

    waiting: Dict[str, int] = {}
    for a in pending:
        inv = by_inv.get(a.invoice_id)
        if a.status == "pending" and inv and inv.status in OPEN:
            waiting[key_of(inv)] = waiting.get(key_of(inv), 0) + 1
    clients: Dict[str, Dict] = {}
    for inv in invs:
        if inv.status in CLOSED:
            continue
        key = key_of(inv)
        c = clients.setdefault(key, {"key": key, "client": inv.client_name or inv.client_email, "currency": (inv.currency or "USD").upper(),
                                     "email": inv.client_email, "invoices": 0, "billed": 0.0, "collected": 0.0,
                                     "owed": 0.0, "overdue": 0.0, "drafts": 0.0, "oldest_late": 0, "next_due": "",
                                     "sample": True, "ids": []})
        c["invoices"] += 1
        c["ids"].append(inv.id)
        c["sample"] = c["sample"] and inv.sample
        if inv.status == "DRAFT":
            c["drafts"] += inv.amount
            continue
        c["billed"] += inv.amount
        due = amount_due(inv)
        c["collected"] += inv.amount - due
        if inv.status in OPEN:
            c["owed"] += due
            late = days_late(inv, today)
            if late > 0:
                c["overdue"] += due
                c["oldest_late"] = max(c["oldest_late"], late)
            elif not c["next_due"] or inv.due_date < c["next_due"]:
                c["next_due"] = inv.due_date[:10]
    out = []
    for key, c in clients.items():
        for k in ("billed", "collected", "owed", "overdue", "drafts"):
            c[k] = round(c[k], 2)
        c["owed_usd"] = round(to_reporting(c["owed"], c["currency"], fx.REPORTING), 2)
        c["overdue_usd"] = round(to_reporting(c["overdue"], c["currency"], fx.REPORTING), 2)
        c["paid_pct"] = round(100 * c["collected"] / c["billed"]) if c["billed"] else 0
        c["pending_nudges"] = waiting.get(key, 0)
        c["mood"] = mood(c["owed"], c["oldest_late"])
        out.append(c)
    out.sort(key=lambda c: (-c["overdue_usd"], -c["oldest_late"], -c["owed_usd"], c["client"].lower()))
    return out
