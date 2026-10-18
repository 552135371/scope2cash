"""Collaborator payouts: when a milestone is paid, its collaborator share is queued as a PayPal Payouts proposal that
the freelancer approves (the same approval queue as the collections agent's reminders and cancellations).

Trigger: an invoice reaching PAID (verified PayPal webhook, Orders v2 capture, status refresh, or a mock event). The
trigger is deterministic code, not the model: money only moves on a rule a human set (the split) plus a human click.
"""
from __future__ import annotations

from typing import List, Optional

from . import config, fx, store
from .adapters.payouts import payouts_for
from .models import AgentAction, Invoice

TOOL = "create_payout"
CLOSED = ("CANCELLED", "REFUNDED")


def action_id(inv: Invoice) -> str:
    return f"payout-{inv.id}"


def set_split(inv: Invoice, label: str, email: str, amount: Optional[float], percent: Optional[float]) -> Invoice:
    """Validate and store the collaborator share. amount/percent of 0 (or both empty) clears it. Raises ValueError."""
    if inv.payout_batch_id:
        raise ValueError(f"{inv.number} was already paid out ({inv.payout_status or 'sent'}); the split can't change now")
    if inv.status in CLOSED:
        raise ValueError(f"{inv.number} is {inv.status}")
    if amount is None and percent is not None:
        if not 0 <= percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        amount = round(inv.amount * percent / 100, 2)
    amount = round(float(amount or 0), 2)
    prior = store.get_action(action_id(inv))  # a queued, rejected or failed payout for the old split is superseded
    if prior and prior.status == "executed":
        raise ValueError(f"{inv.number} was already paid out")

    def supersede(why: str) -> None:
        if prior:
            store.save_action(prior.model_copy(update={"status": "rejected", "decided_at": store.now(), "result": why}))
        # A payout the collections agent proposed for this milestone is superseded too: the human just decided the split.
        for a in store.actions("pending", limit=500):
            if a.tool == TOOL and a.invoice_id == inv.id and a.id != action_id(inv):
                store.save_action(a.model_copy(update={"status": "rejected", "decided_at": store.now(), "result": why}))

    if amount <= 0:
        supersede("split removed")
        inv = inv.model_copy(update={"split_label": "", "split_email": "", "split_amount": 0.0})
        store.audit("split_cleared", inv.id)
        return store.save(inv)
    email = email.strip()
    if "@" not in email or " " in email:
        raise ValueError("enter the collaborator's PayPal email")
    if amount > inv.amount:
        raise ValueError(f"the share ({fx.fmt(amount, inv.currency, 2)}) can't exceed the milestone ({fx.fmt(inv.amount, inv.currency, 2)})")
    if not config.get_settings().recipient_allowed(email):
        raise ValueError("this public demo only pays sandbox recipients (*example.com)")
    supersede("split changed")  # a new split is a new decision: queue_if_paid may propose again
    inv = inv.model_copy(update={"split_label": label.strip()[:60] or "Collaborator", "split_email": email, "split_amount": amount})
    store.audit("split_set", inv.id, label=inv.split_label, amount=amount)
    store.save(inv)
    queue_if_paid(inv)
    return inv


def guard(params: dict) -> str:
    """Empty string if the payout may run. Checked when queued and again at approval."""
    inv = store.get(str(params.get("invoice_id", "")))
    if not inv:
        return "payouts only run for invoices Scope2Cash created"
    if inv.status != "PAID":
        return f"{inv.number} is {inv.status}; a collaborator is paid only after the client has paid"
    if inv.payout_batch_id:
        return f"{inv.number} was already paid out (batch {inv.payout_batch_id})"
    if not inv.split_email or inv.split_amount <= 0:
        return f"{inv.number} has no collaborator split"
    if (params.get("receiver"), round(float(params.get("amount") or 0), 2)) != (inv.split_email, round(inv.split_amount, 2)):
        return "the split changed after this payout was queued"
    if inv.split_amount > inv.amount:
        return "the share exceeds the milestone amount"
    if not config.get_settings().recipient_allowed(inv.split_email):
        return "this public demo only pays sandbox recipients"
    if inv.provider != "mock" and config.get_settings().paypal_mode == "live":
        return "payouts are sandbox-only in this build"
    return ""


def queue_if_paid(inv: Optional[Invoice]) -> Optional[AgentAction]:
    """Idempotent: one payout proposal per paid milestone with a split (a rejected one is not re-queued)."""
    if not inv or inv.status != "PAID" or not inv.split_email or inv.split_amount <= 0 or inv.payout_batch_id:
        return None
    existing = store.get_action(action_id(inv))
    if existing and (existing.status != "rejected" or existing.result not in ("split changed", "split removed")):
        return None
    params = {"invoice_id": inv.id, "receiver": inv.split_email, "amount": round(inv.split_amount, 2),
              "currency": inv.currency, "label": inv.split_label}
    if guard(params):
        return None
    a = AgentAction(id=action_id(inv), tool=TOOL, params=params, invoice_id=inv.id, invoice_number=inv.number,
                    summary=f"Pay {inv.split_label} {fx.fmt(inv.split_amount, inv.currency, 2)} of {inv.number} · PayPal Payouts",
                    reason=f"{inv.client_name or inv.client_email} paid “{inv.milestone}”; your split sends "
                           f"{fx.fmt(inv.split_amount, inv.currency, 2)} of {fx.fmt(inv.amount, inv.currency, 2)} to {inv.split_email}",
                    created_at=store.now())
    store.save_action(a)
    store.audit("payout_queued", inv.id, amount=inv.split_amount, provider=inv.provider)
    return a


def sweep() -> List[AgentAction]:
    return [a for a in (queue_if_paid(i) for i in store.all_invoices()) if a]


def execute(a: AgentAction) -> Invoice:
    inv = store.get(a.invoice_id)
    assert inv is not None
    inv = payouts_for(inv).create(inv)
    store.save(inv)
    store.audit("payout_created", inv.id, batch=inv.payout_batch_id, status=inv.payout_status, provider=inv.provider)
    return inv


def refresh(inv: Invoice) -> Invoice:
    inv = payouts_for(inv).refresh(inv)
    return store.save(inv)
