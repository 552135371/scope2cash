"""Read-only roll-ups for the Pipeline dashboard: a weekly cash-flow series (AG Charts) and a per-client
"who still owes what" scoreboard (second AG Grid). Pure functions over stored invoices: no PayPal or LLM calls,
so the dashboard can re-query them on every grid filter change for free."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional

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


def cashflow(invoices: Iterable[Invoice], today: Optional[date] = None) -> Dict:
    """Weekly stacked series: collected (paid, by paid date), expected (open, not late, by due date), overdue (open
    and late, by due date) and drafts (not sent yet, by due date). Each week lists the invoice ids behind each
    series so the UI can filter the grid to exactly the rows behind a clicked bar."""
    today = today or date.today()
    rows = [(inv, bucket(inv, today)) for inv in invoices]
    rows = [(inv, b) for inv, b in rows if b]
    if not rows:
        return {"as_of": today.isoformat(), "weeks": [], "totals": {k: 0.0 for k in SERIES}, "clamped": 0}
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
                  "current": w == week_of(today), "clamped": False, **{k: 0.0 for k in SERIES}, "ids": {k: [] for k in SERIES}}
        w += timedelta(weeks=1)
    totals = {k: 0.0 for k in SERIES}
    for inv, b in rows:
        wk = week_of(cash_date(inv))
        if wk < first or wk > last:
            clamped += 1
            edge = out[first] if wk < first else out[last]
            edge["label"] = ("≤ " + edge["label"].lstrip("≤ ")) if wk < first else (edge["label"].rstrip(" +") + " +")
            edge["clamped"] = True
            wk = min(max(wk, first), last)
        value = float(inv.amount) if b in ("collected", "drafts") else amount_due(inv)
        out[wk][b] = round(out[wk][b] + value, 2)
        out[wk]["ids"][b].append(inv.id)
        totals[b] = round(totals[b] + value, 2)
    return {"as_of": today.isoformat(), "weeks": list(out.values()), "totals": totals, "clamped": clamped}


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
    """One row per client: billed, collected, still owed, overdue, oldest lateness, next due date, drafts waiting
    for approval and agent proposals waiting for approval. Sorted so the clients who need attention come first."""
    today = today or date.today()
    invs = list(invoices)
    by_inv = {i.id: i for i in invs}
    waiting: Dict[str, int] = {}
    for a in pending:
        inv = by_inv.get(a.invoice_id)
        if a.status == "pending" and inv and inv.status in OPEN:
            key = inv.client_name or inv.client_email
            waiting[key] = waiting.get(key, 0) + 1
    clients: Dict[str, Dict] = {}
    for inv in invs:
        if inv.status in CLOSED:
            continue
        key = inv.client_name or inv.client_email
        c = clients.setdefault(key, {"client": key, "email": inv.client_email, "invoices": 0, "billed": 0.0, "collected": 0.0,
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
        c["paid_pct"] = round(100 * c["collected"] / c["billed"]) if c["billed"] else 0
        c["pending_nudges"] = waiting.get(key, 0)
        c["mood"] = mood(c["owed"], c["oldest_late"])
        out.append(c)
    out.sort(key=lambda c: (-c["overdue"], -c["oldest_late"], -c["owed"], c["client"].lower()))
    return out
