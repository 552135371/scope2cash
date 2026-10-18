"""Scope2Cash's own agent tools for the "get paid" half: explain cash flow and propose collaborator payouts.

These are NOT part of the PayPal Agent Toolkit (it has no Payouts tool); they sit next to its invoice tools in the
same function-calling loop and follow the same rule: reads run, writes only queue a proposal.

  get_cash_flow_summary         read   Facts from the stored pipeline: per currency what came in, what is open, what is
                                       late, what falls due in the next 7 days, who owes what, and which collaborator
                                       payouts are waiting, sent or not set up. No PayPal or model call.
  propose_collaborator_payout   write  "Settle Mia's share of the Lumen retainer": queues ONE PayPal Payouts proposal in
                                       the approval queue (grid column + agent panel). The model can never send it. On
                                       Approve, the guardrails are re-checked (still PAID, split unchanged since the
                                       proposal, not already paid out, sandbox recipients in the public demo, live money
                                       refused), the split is recorded on the milestone, and only then is the payout made
                                       by the same code path as a human-set split (app/payouts_flow.py).
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import config, fx, payouts_flow, pipeline_stats as ps, store
from .models import AgentAction, Invoice

READ = "get_cash_flow_summary"
WRITE = "propose_collaborator_payout"
SOURCE = "agent"  # params["source"] on a create_payout action the agent proposed (vs. one queued from a human split)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMAS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": READ,
        "description": ("Scope2Cash tool (not part of the PayPal Agent Toolkit). Read-only facts about the freelancer's "
                        "cash flow from the invoices Scope2Cash tracks: per currency, money collected, open, overdue and due "
                        "in the next 7 days; each client's balance; overdue invoices; collaborator payouts that are waiting "
                        "for approval, already sent, or could be proposed (paid milestones with no split); known "
                        "collaborators (label + PayPal email). Amounts are per currency; never add different currencies "
                        "together unless you say the figure is the approximate USD equivalent given in approx_usd."),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": WRITE,
        "description": ("Scope2Cash tool (not part of the PayPal Agent Toolkit). Propose paying a collaborator (designer, "
                        "developer, ...) their share of ONE milestone the client has already PAID, through PayPal Payouts. "
                        "This does NOT pay anyone: it queues a proposal the freelancer must approve. Give the invoice_id, the "
                        "collaborator's label and PayPal email, and either amount (in the invoice's currency) or percent of "
                        "the milestone. Refused if the milestone isn't PAID, was already paid out, already has a different "
                        "split set by the freelancer, or the share exceeds the milestone."),
        "parameters": {"type": "object", "properties": {
            "invoice_id": {"type": "string", "description": "id of a PAID invoice from get_cash_flow_summary or list_invoices"},
            "label": {"type": "string", "description": "who is paid, e.g. 'Designer (Mia)'"},
            "email": {"type": "string", "description": "the collaborator's PayPal email"},
            "amount": {"type": "number", "description": "share in the invoice's own currency"},
            "percent": {"type": "number", "description": "share as a percent of the milestone (0-100), instead of amount"},
            "reason": {"type": "string", "description": "one short sentence for the approval card"}},
            "required": ["invoice_id", "email"], "additionalProperties": False}}},
]


# ------------------------------------------------------------------ read: cash-flow facts
def _row(inv: Invoice, today: date) -> Dict[str, Any]:
    due = ps.amount_due(inv)
    return {"invoice_id": inv.id, "number": inv.number, "client": inv.client_name or inv.client_email, "milestone": inv.milestone,
            "currency": inv.currency, "amount": inv.amount, "due_amount": due, "due_date": inv.due_date[:10],
            "days_late": ps.days_late(inv, today), "status": inv.status}


def collaborators(invs: Optional[List[Invoice]] = None) -> List[Dict[str, str]]:
    """Collaborators the freelancer has split work with before (from milestone splits), most recent first."""
    seen: Dict[str, Dict[str, str]] = {}
    for inv in reversed(invs if invs is not None else store.all_invoices()):
        if inv.split_email and inv.split_email.lower() not in seen:
            seen[inv.split_email.lower()] = {"label": inv.split_label, "email": inv.split_email}
    return list(seen.values())


def cash_flow_summary(today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    payouts_flow.sweep()  # the same idempotent sweep the grid runs: paid milestones with a split have their payout queued
    invs = store.all_invoices()
    week_end = today + timedelta(days=7)
    per: Dict[str, Dict[str, float]] = {}
    overdue, due_soon = [], []
    for inv in invs:
        cur = (inv.currency or "USD").upper()
        t = per.setdefault(cur, {"collected": 0.0, "open": 0.0, "overdue": 0.0, "due_next_7_days": 0.0, "drafts": 0.0})
        b = ps.bucket(inv, today)
        if b == "collected":
            t["collected"] += inv.amount
        elif b == "drafts":
            t["drafts"] += inv.amount
        elif b in ("expected", "overdue"):
            due = ps.amount_due(inv)
            t["open"] += due
            if b == "overdue":
                t["overdue"] += due
                overdue.append(_row(inv, today))
            else:
                try:
                    d = date.fromisoformat(inv.due_date[:10])
                except ValueError:
                    d = None
                if d and today <= d <= week_end:
                    t["due_next_7_days"] += due
                    due_soon.append(_row(inv, today))
    per = {c: {k: round(v, 2) for k, v in t.items()} for c, t in per.items()}
    approx = {k: round(sum(ps.to_reporting(t[k], c, fx.REPORTING) for c, t in per.items()), 2)
              for k in ("collected", "open", "overdue", "due_next_7_days", "drafts")}
    pending = {a.invoice_id: a for a in store.actions("pending", limit=500) if a.tool == payouts_flow.TOOL}
    by_id = {i.id: i for i in invs}
    payouts_waiting = [{"action_id": a.id, "invoice_id": a.invoice_id, "number": a.invoice_number,
                        "to": a.params.get("label") or a.params.get("receiver"), "email": a.params.get("receiver"),
                        "amount": a.params.get("amount"), "currency": by_id[a.invoice_id].currency,
                        "proposed_by": "agent" if a.params.get("source") == SOURCE else "your split"}
                       for a in pending.values() if a.invoice_id in by_id]
    paid_out = [{"number": i.number, "to": i.split_label, "amount": i.split_amount, "currency": i.currency, "status": i.payout_status}
                for i in invs if i.payout_batch_id]
    waiting_on_client = [{"number": i.number, "client": i.client_name, "to": i.split_label, "amount": i.split_amount, "currency": i.currency,
                          "client_status": i.status} for i in invs if i.split_email and i.status != "PAID" and i.status not in ps.CLOSED]
    no_split = [_row(i, today) for i in invs if i.status == "PAID" and not i.split_email and not i.payout_batch_id
                and i.id not in pending]
    board = ps.client_scoreboard(invs, store.actions("pending", limit=500), today)
    return {
        "as_of": today.isoformat(),
        "currencies": sorted(per),
        "by_currency": per,
        "approx_usd": {**approx, "note": f"approximate USD equivalent at {fx.label()} (not PayPal's rate)"} if len(per) > 1 else None,
        "overdue": sorted(overdue, key=lambda r: -r["days_late"])[:10],
        "due_next_7_days": sorted(due_soon, key=lambda r: r["due_date"])[:10],
        "clients": [{k: c[k] for k in ("client", "currency", "owed", "overdue", "oldest_late", "next_due", "mood")} for c in board[:8]],
        "collaborator_payouts": {"waiting_for_your_approval": payouts_waiting, "paid_out": paid_out,
                                 "split_set_waiting_for_client_payment": waiting_on_client,
                                 "paid_milestones_without_a_split": no_split[:10]},
        "known_collaborators": collaborators(invs),
    }


def explain(summary: Dict[str, Any]) -> str:
    """Plain-language version of cash_flow_summary (the offline planner's answer; the live model writes its own)."""
    per = summary["by_currency"]
    if not per:
        return "There are no invoices yet, so there is no cash flow to explain."

    def each(key: str) -> str:
        parts = [fx.fmt(t[key], c) for c, t in per.items() if t[key]]
        return " + ".join(parts) if parts else "nothing"

    out = [f"Collected so far: {each('collected')}. Still open: {each('open')}, of which late: {each('overdue')}."]
    if summary["overdue"]:
        out.append("Late: " + "; ".join(f"{r['client']} {r['number']} {fx.fmt(r['due_amount'], r['currency'])} ({r['days_late']}d)"
                                         for r in summary["overdue"][:4]) + ".")
    soon = summary["due_next_7_days"]
    out.append(("Due in the next 7 days: " + "; ".join(f"{r['client']} {fx.fmt(r['due_amount'], r['currency'])} on {r['due_date']}"
                                                         for r in soon[:4]) + ".") if soon else "Nothing else falls due in the next 7 days.")
    cp = summary["collaborator_payouts"]
    if cp["waiting_for_your_approval"]:
        out.append("You owe collaborators: " + "; ".join(f"{p['to']} {fx.fmt(p['amount'], p['currency'], 2)} ({p['number']})"
                                                         for p in cp["waiting_for_your_approval"]) + ", waiting for your approval.")
    if summary.get("approx_usd"):
        out.append(f"Mixed currencies: about {fx.fmt(summary['approx_usd']['open'], 'USD')} open in total ({fx.label()}).")
    return " ".join(out)


# ------------------------------------------------------------------ write: payout proposals (approval queue only)
def _snapshot(inv: Invoice) -> Dict[str, Any]:
    return {"label": inv.split_label, "email": inv.split_email, "amount": round(inv.split_amount, 2)}


def guard_proposal(params: Dict[str, Any], at_approval: bool = False) -> str:
    """Empty string if the agent-proposed payout may be queued (or, at_approval, executed)."""
    s = config.get_settings()
    inv = store.get(str(params.get("invoice_id", "")))
    if not inv:
        return "payouts only run for invoices Scope2Cash created"
    if inv.status != "PAID":
        return f"{inv.number} is {inv.status}; a collaborator is paid only after the client has paid"
    if inv.payout_batch_id:
        return f"{inv.number} was already paid out (batch {inv.payout_batch_id})"
    amount = round(float(params.get("amount") or 0), 2)
    if amount <= 0:
        return "the share must be more than zero"
    if amount > inv.amount:
        return f"the share ({fx.fmt(amount, inv.currency, 2)}) can't exceed the milestone ({fx.fmt(inv.amount, inv.currency, 2)})"
    email = str(params.get("receiver") or "")
    if not _EMAIL.match(email):
        return "a valid PayPal email for the collaborator is required"
    if not s.recipient_allowed(email):
        return "this public demo only pays sandbox recipients (*example.com)"
    if inv.provider != "mock" and s.paypal_mode == "live":
        return "payouts are sandbox-only in this build"
    snap = params.get("split_before") or {"label": "", "email": "", "amount": 0.0}
    if at_approval:
        if _snapshot(inv) != {**snap, "amount": round(float(snap.get("amount") or 0), 2)}:
            return "the split changed after this payout was proposed"
        return ""
    if inv.split_email:  # the freelancer already decided a split for this milestone: the agent doesn't override it
        same = inv.split_email.lower() == email.lower() and round(inv.split_amount, 2) == amount
        return (f"{inv.number} already has your split ({inv.split_label} {fx.fmt(inv.split_amount, inv.currency, 2)}); "
                + ("its payout is handled by that split" if same else "change it in the grid instead"))
    for a in store.actions(limit=500):
        if a.tool == payouts_flow.TOOL and a.invoice_id == inv.id:
            if a.status == "executed":
                return f"{inv.number} was already paid out"
            if a.status == "rejected" and a.result not in ("split changed", "split removed"):
                return f"you rejected a payout for {inv.number} before; set a split in the grid if you want to pay it"
    return ""


def propose(args: Dict[str, Any], reason: str = "") -> Tuple[bool, str, Optional[AgentAction]]:
    """Validate a propose_collaborator_payout call and queue it. Returns (queued, message for the model, action)."""
    inv = store.get(str(args.get("invoice_id", "")))
    if not inv:
        return False, "refused by Scope2Cash guardrail: the agent may only act on invoices Scope2Cash created", None
    for a in store.actions("pending", limit=500):
        if a.tool == payouts_flow.TOOL and a.invoice_id == inv.id:
            return True, ('{"status": "ALREADY_WAITING_FOR_HUMAN_APPROVAL", "action_id": "%s", "summary": "%s"}'
                          % (a.id, a.summary.replace('"', "'"))), None
    try:
        amount = args.get("amount")
        pct = args.get("percent")
        if amount in (None, "", 0) and pct not in (None, ""):
            p = float(pct)
            if not 0 < p <= 100:
                return False, "refused: percent must be between 0 and 100", None
            amount = inv.amount * p / 100
        amount = round(float(amount or 0), 2)
    except (TypeError, ValueError):
        return False, "invalid arguments: amount/percent must be numbers", None
    email = str(args.get("email") or "").strip()
    label = (str(args.get("label") or "").strip() or email.split("@")[0] or "Collaborator")[:60]
    params = {"invoice_id": inv.id, "receiver": email, "amount": amount, "currency": inv.currency, "label": label,
              "source": SOURCE, "split_before": _snapshot(inv)}
    problem = guard_proposal(params)
    if problem:
        return False, f"refused by Scope2Cash guardrail: {problem}", None
    share = fx.fmt(amount, inv.currency, 2)
    a = AgentAction(id="agentpay_" + uuid.uuid4().hex[:10], tool=payouts_flow.TOOL, params=params, invoice_id=inv.id,
                    invoice_number=inv.number, created_at=store.now(),
                    summary=f"Pay {label} {share} of {inv.number} · PayPal Payouts (proposed by agent)",
                    reason=(reason.strip()[:200] + " · " if reason.strip() else "")
                           + f"{inv.client_name or inv.client_email} paid “{inv.milestone}” ({fx.fmt(inv.amount, inv.currency, 2)}); "
                             f"{share} to {email} after you approve")
    return True, ('{"status": "QUEUED_FOR_HUMAN_APPROVAL", "action_id": "%s", "note": "nothing is paid until the freelancer approves"}'
                  % a.id), a


def execute_approved(a: AgentAction) -> Invoice:
    """Called only from the human Approve click (paypal_agent.decide). Re-checks, records the split, then pays through
    the same code path as a human-set split. Raises ValueError with the guardrail message if it no longer holds."""
    problem = guard_proposal(a.params, at_approval=True)
    if problem:
        raise ValueError(problem)
    inv = store.get(a.invoice_id)
    assert inv is not None
    before = inv
    inv = store.save(inv.model_copy(update={"split_label": a.params["label"], "split_email": a.params["receiver"],
                                            "split_amount": round(float(a.params["amount"]), 2)}))
    store.audit("split_set", inv.id, label=inv.split_label, amount=inv.split_amount, by="approved agent proposal", action_id=a.id)
    problem = payouts_flow.guard(a.params)
    if problem:  # belt and braces: the standard payout guard must agree too
        store.save(before)
        raise ValueError(problem)
    try:
        return payouts_flow.execute(a)
    except Exception:
        store.save(before)  # a failed PayPal call leaves the milestone as it was (no half-recorded split)
        raise
