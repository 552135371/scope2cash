"""Voice skills for a freelancer's money questions. Pure functions over the Scope2Cash store.

Every skill returns a dict with:
  speech  - one or two short sentences written to be spoken by Alexa (no Markdown, no tables)
  card    - a small display card for screen devices (title, rows, footer)
  data    - structured data for agents
The only write is `propose_reminder`, and it never sends anything: it queues the same human-approval proposal the
dashboard's collections agent uses (`agent_actions`, status "pending"). Nothing reaches a client until the freelancer
taps Approve in Scope2Cash, which re-checks every guardrail at approval time.
"""
from __future__ import annotations

import difflib
import re
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app import paypal_agent, pipeline_stats, store
from app.models import AgentAction, Invoice

OPEN = pipeline_stats.OPEN
REMIND_TOOL = "send_invoice_reminder"


# ------------------------------------------------------------------ helpers
def money(x: float, currency: str = "USD") -> str:
    sym = "$" if currency == "USD" else ""
    s = f"{sym}{x:,.0f}" if float(x).is_integer() else f"{sym}{x:,.2f}"
    return s if sym else f"{s} {currency}"


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def join_names(names: List[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def client_names(invoices: Optional[List[Invoice]] = None) -> List[str]:
    invs = invoices if invoices is not None else store.all_invoices()
    seen: List[str] = []
    for i in invs:
        n = i.client_name or i.client_email
        if n and n not in seen:
            seen.append(n)
    return seen


def match_client(query: str, invoices: Optional[List[Invoice]] = None) -> Optional[str]:
    """Resolve a spoken client name ('harbor', 'the yoga studio', 'Lumen') to a stored client name."""
    q = _norm(query)
    if not q:
        return None
    names = client_names(invoices)
    normed = {n: _norm(n) for n in names}
    for n, nn in normed.items():  # exact / substring
        if q == nn or q in nn or (len(nn) > 3 and nn in q):
            return n
    stop = {"the", "and", "my", "client", "studio", "inc", "llc", "co"}
    qt = {t for t in q.split() if t not in stop and len(t) > 2}
    best, score = None, 0.0
    for n, nn in normed.items():
        nt = {t for t in nn.split() if t not in stop}
        overlap = len(qt & nt)
        fuzzy = max((difflib.SequenceMatcher(None, a, b).ratio() for a in qt for b in nt), default=0.0)
        s = overlap + (fuzzy if fuzzy >= 0.8 else 0.0)
        if s > score:
            best, score = n, s
    return best if score >= 0.8 else None


def _card(title: str, rows: List[Dict[str, str]], footer: str = "") -> Dict[str, Any]:
    return {"title": title, "rows": rows, "footer": footer}


def _pending_reminders() -> Dict[str, AgentAction]:
    return {a.invoice_id: a for a in store.actions("pending", limit=500) if a.tool == REMIND_TOOL}


# ------------------------------------------------------------------ read skills
def who_owes_me(client: str = "", today: Optional[date] = None) -> Dict[str, Any]:
    """Who still owes money, most urgent first. Optionally narrowed to one client."""
    today = today or date.today()
    invs = store.all_invoices()
    board = pipeline_stats.client_scoreboard(invs, store.actions("pending", limit=500), today)
    owing = [c for c in board if c["owed"] > 0]
    if client:
        name = match_client(client, invs)
        if not name:
            return {"speech": f"I couldn't find a client called {client}. Your clients are {join_names(client_names(invs)) or 'not set up yet'}.",
                    "card": _card("Client not found", []), "data": {"clients": [], "query": client}}
        owing = [c for c in board if c["client"] == name]
        if not owing or owing[0]["owed"] <= 0:
            return {"speech": f"{name} is all square. They don't owe you anything right now.",
                    "card": _card(name, [{"label": "Owed", "value": "$0"}], "All square"), "data": {"clients": owing}}
    if not owing:
        return {"speech": "Good news: nobody owes you money right now.", "card": _card("Who owes me", [], "All square"),
                "data": {"clients": [], "total_owed": 0.0, "total_overdue": 0.0}}
    total = round(sum(c["owed"] for c in owing), 2)
    overdue = round(sum(c["overdue"] for c in owing), 2)
    late = [c for c in owing if c["overdue"] > 0]
    parts = []
    for c in owing[:3]:
        bit = f"{c['client']} owes {money(c['owed'])}"
        if c["oldest_late"] > 0:
            bit += f", {_days(c['oldest_late'])} late"
        parts.append(bit)
    if len(owing) == 1:
        speech = parts[0] + "."
    else:
        speech = f"{len(owing)} clients owe you {money(total)} in total. " + "; ".join(parts)
        speech += (f"; plus {len(owing) - 3} more." if len(owing) > 3 else ".")
    if late and not client:
        worst = late[0]
        speech += f" {worst['client']} has the biggest overdue balance. Want me to queue a reminder for your approval?"
    elif late:
        speech += " Want me to queue a reminder for your approval?"
    rows = [{"label": c["client"], "value": money(c["owed"]),
             "note": (f"{_days(c['oldest_late'])} late" if c["oldest_late"] else (f"due {c['next_due']}" if c["next_due"] else "")),
             "mood": c["mood"]} for c in owing]
    return {"speech": speech,
            "card": _card("Who still owes me", rows, f"Total {money(total)} · overdue {money(overdue)}"),
            "data": {"clients": owing, "total_owed": total, "total_overdue": overdue}}


def cashflow_this_week(today: Optional[date] = None) -> Dict[str, Any]:
    """Money collected this week, money due this week, and what is already overdue."""
    today = today or date.today()
    invs = store.all_invoices()
    start = pipeline_stats.week_of(today)
    end = start + timedelta(days=6)
    collected = due = overdue = 0.0
    due_names: List[str] = []
    for inv in invs:
        b = pipeline_stats.bucket(inv, today)
        d = pipeline_stats.cash_date(inv)
        if b == "collected" and start <= d <= end:
            collected += inv.amount
        elif b == "expected" and start <= d <= end:
            due += pipeline_stats.amount_due(inv)
            due_names.append(inv.client_name or inv.client_email)
        elif b == "overdue":
            overdue += pipeline_stats.amount_due(inv)
    collected, due, overdue = round(collected, 2), round(due, 2), round(overdue, 2)
    speech = (f"This week you've collected {money(collected)}." if collected else "Nothing has come in yet this week.")
    speech += (f" {money(due)} more is due by Sunday, from {join_names(sorted(set(due_names)))}." if due
               else " Nothing else is due this week.")
    if overdue:
        speech += f" And {money(overdue)} is already overdue."
    rows = [{"label": "Collected this week", "value": money(collected)},
            {"label": "Due by Sunday", "value": money(due)},
            {"label": "Already overdue", "value": money(overdue)}]
    return {"speech": speech, "card": _card(f"Week of {start.strftime('%b %d').replace(' 0', ' ')}", rows),
            "data": {"week_start": start.isoformat(), "collected": collected, "due_this_week": due, "overdue": overdue}}


def pending_approvals() -> Dict[str, Any]:
    """Proposals (reminders, payouts) waiting for the freelancer's approval in the dashboard."""
    acts = store.actions("pending", limit=500)
    open_ids = {i.id for i in store.all_invoices()}
    acts = [a for a in acts if a.invoice_id in open_ids]
    if not acts:
        return {"speech": "Nothing is waiting for your approval.", "card": _card("Waiting for approval", []), "data": {"actions": []}}
    n = len(acts)
    speech = (f"{n} {'thing is' if n == 1 else 'things are'} waiting for your approval: "
              + "; ".join(a.summary.split(" · ")[0] + " for " + (a.summary.split(" · ")[1] if " · " in a.summary else a.invoice_number)
                          for a in acts[:3]) + ". Approve them in Scope2Cash; I never send anything on my own.")
    rows = [{"label": a.summary, "value": a.status, "id": a.id} for a in acts]
    return {"speech": speech, "card": _card("Waiting for your approval", rows, "Open Scope2Cash › Pipeline to approve"),
            "data": {"actions": [a.model_dump() for a in acts]}}


# ------------------------------------------------------------------ the one write: queue (never send) a reminder
def _reminder_note(inv: Invoice, days: int, sender: str) -> str:
    due = pipeline_stats.amount_due(inv)
    if days <= 0:
        lead = f"a quick heads-up that invoice {inv.number} ({money(due, inv.currency)}) is due on {inv.due_date[:10]}."
    elif days <= 14:
        lead = f"a friendly reminder that invoice {inv.number} ({money(due, inv.currency)}) is now {_days(days)} past due."
    else:
        lead = f"following up on invoice {inv.number} ({money(due, inv.currency)}), which is {_days(days)} past due."
    return f"Hi {inv.client_name or 'there'}, {lead} You can pay securely here: {inv.payer_link}. Thanks! {sender}"


def propose_reminder(client: str = "", invoice_number: str = "", sender: str = "", today: Optional[date] = None) -> Dict[str, Any]:
    """Queue PayPal payment reminders for a client's unpaid invoices. QUEUES ONLY: the freelancer approves in the
    dashboard before anything is sent. Idempotent: an invoice that already has a pending reminder is not queued twice."""
    from app import config
    today = today or date.today()
    sender = sender or config.get_settings().freelancer_name
    invs = store.all_invoices()
    targets: List[Invoice] = []
    name = ""
    if invoice_number:
        targets = [i for i in invs if i.number.lower() == invoice_number.strip().lower() or i.id == invoice_number.strip()]
        if not targets:
            return {"speech": f"I couldn't find invoice {invoice_number}.", "card": _card("Invoice not found", []),
                    "data": {"queued": [], "skipped": []}}
        name = targets[0].client_name or targets[0].client_email
    elif client:
        name = match_client(client, invs) or ""
        if not name:
            return {"speech": f"I couldn't find a client called {client}.", "card": _card("Client not found", []),
                    "data": {"queued": [], "skipped": []}}
        targets = [i for i in invs if (i.client_name or i.client_email) == name]
    else:  # no target: the overdue invoices (most late first)
        targets = [i for i in invs if pipeline_stats.days_late(i, today) > 0]
    targets = [i for i in targets if i.status in OPEN]
    targets.sort(key=lambda i: -pipeline_stats.days_late(i, today))
    if not targets:
        who = name or "anyone"
        return {"speech": f"There's nothing unpaid to chase for {who}.", "card": _card("No reminder needed", []),
                "data": {"queued": [], "skipped": []}}
    existing = _pending_reminders()
    queued: List[AgentAction] = []
    already: List[AgentAction] = []
    skipped: List[Dict[str, str]] = []
    for inv in targets[:5]:
        if inv.id in existing:
            already.append(existing[inv.id])
            continue
        days = pipeline_stats.days_late(inv, today)
        params: Dict[str, Any] = {"invoice_id": inv.id, "note": _reminder_note(inv, days, sender)[:1000]}
        problem = paypal_agent.guard_action(REMIND_TOOL, params)
        if problem:
            skipped.append({"invoice": inv.number, "reason": problem})
            continue
        summary = (f"Send PayPal reminder {inv.number} · {inv.client_name or inv.client_email} · {money(pipeline_stats.amount_due(inv))}"
                   + (f" · {_days(days)} late" if days else ""))
        a = AgentAction(id="act_" + uuid.uuid4().hex[:10], tool=REMIND_TOOL, params=params, invoice_id=inv.id,
                        invoice_number=inv.number, summary=summary, reason="Asked by voice (Alexa+ add-on)",
                        created_at=store.now())
        store.save_action(a)
        store.audit("voice_proposed", inv.id, tool=REMIND_TOOL, action_id=a.id)
        queued.append(a)
    all_q = queued + already
    if all_q:
        total = sum(pipeline_stats.amount_due(store.get(a.invoice_id) or targets[0]) for a in all_q)
        who = name or join_names(sorted({(store.get(a.invoice_id).client_name if store.get(a.invoice_id) else "") or a.invoice_number for a in all_q}))
        n = len(all_q)
        speech = (f"Done. I've queued {'a reminder' if n == 1 else f'{n} reminders'} for {who}, {money(total)} in total. "
                  if queued else f"A reminder for {who} is already waiting for you. ")
        speech += "Nothing goes out until you approve it in Scope2Cash."
    else:
        speech = "I couldn't queue that reminder: " + (skipped[0]["reason"] if skipped else "no eligible invoice") + "."
    rows = [{"label": a.summary, "value": "waiting for approval", "id": a.id} for a in all_q]
    rows += [{"label": s["invoice"], "value": "skipped", "note": s["reason"]} for s in skipped]
    return {"speech": speech, "card": _card("Reminders queued (not sent)", rows, "Approve or reject in Scope2Cash › Pipeline"),
            "data": {"queued": [a.model_dump() for a in queued], "already_pending": [a.model_dump() for a in already],
                     "skipped": skipped, "client": name, "sent": False}}
