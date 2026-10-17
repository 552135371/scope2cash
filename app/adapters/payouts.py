"""PayPal Payouts adapter: pay a collaborator their share of a milestone once the client has paid.

Live (sandbox): PayPal Payouts API v1, same OAuth token and transport as the Invoicing adapter.
  POST /v1/payments/payouts                     one-item batch; sender_batch_id + PayPal-Request-Id derived from the
                                                invoice id, so a retried or double-clicked approval can't pay twice
                                                -> 201 {batch_header: {payout_batch_id, batch_status: PENDING}}
  GET  /v1/payments/payouts/{payout_batch_id}   batch_status + items[0].transaction_status (SUCCESS, UNCLAIMED, ...)
Payouts only run after a human approves the queued proposal (see app/payouts_flow.py). Live money is refused here:
this build is sandbox-only for payouts.

Mock: clearly fake MOCK-PAYOUT ids, status SUCCESS, nothing leaves the machine.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

from .. import config
from ..models import Invoice
from .http import AdapterError
from .payments import PROVIDER, PayPalInvoicing


def batch_id_for(inv: Invoice) -> str:
    """Deterministic per milestone: PayPal rejects a reused sender_batch_id, which is the last line against double pay."""
    return f"s2c-{inv.id}"[:127]


def build_payout_body(inv: Invoice) -> Dict[str, Any]:
    who = inv.split_label or "collaborator"
    sid = batch_id_for(inv)
    return {
        "sender_batch_header": {
            "sender_batch_id": sid,
            "recipient_type": "EMAIL",
            "email_subject": "You have a payout for your share of a project",
            "email_message": f"Your share of \"{inv.milestone}\" ({inv.number}), paid via Scope2Cash.",
        },
        "items": [{
            "recipient_type": "EMAIL",
            "amount": {"value": f"{inv.split_amount:.2f}", "currency": inv.currency},
            "receiver": inv.split_email,
            "note": f"{who}: share of {inv.milestone} ({inv.number})"[:4000],
            "sender_item_id": f"{sid}-1"[:63],
        }],
    }


def apply_batch(inv: Invoice, j: Dict[str, Any]) -> Invoice:
    head = j.get("batch_header") or {}
    item = (j.get("items") or [{}])[0] or {}
    status = str(item.get("transaction_status") or head.get("batch_status") or "")
    return inv.model_copy(update={"payout_batch_id": str(head.get("payout_batch_id") or inv.payout_batch_id),
                                  "payout_status": status or inv.payout_status,
                                  "payout_item_id": str(item.get("payout_item_id") or inv.payout_item_id)})


class PayPalPayouts(PayPalInvoicing):
    mode = "live"

    def create(self, inv: Invoice) -> Invoice:
        if self.s.paypal_mode == "live":
            raise AdapterError(PROVIDER, "payouts are sandbox-only in this build")
        r = self._call("POST", "/v1/payments/payouts", json=build_payout_body(inv), idempotency_key=batch_id_for(inv))
        return apply_batch(inv, r.json())

    def refresh(self, inv: Invoice) -> Invoice:
        if not inv.payout_batch_id:
            return inv
        r = self._call("GET", f"/v1/payments/payouts/{inv.payout_batch_id}")
        return apply_batch(inv, r.json())


class MockPayouts:
    mode = "mock"

    def create(self, inv: Invoice) -> Invoice:
        return inv.model_copy(update={"payout_batch_id": "MOCK-PAYOUT-" + uuid.uuid4().hex[:10].upper(),
                                      "payout_status": "SUCCESS", "payout_item_id": "MOCK-ITEM-" + uuid.uuid4().hex[:8].upper()})

    def refresh(self, inv: Invoice) -> Invoice:
        return inv


def payouts_for(inv: Invoice):
    """Same routing as invoices: mock invoices pay out in mock, PayPal invoices through the PayPal Payouts API."""
    if inv.provider == "mock":
        return MockPayouts()
    if not config.get_settings().payments_live:
        raise AdapterError(PROVIDER, "this invoice lives in PayPal but PayPal keys are not configured")
    return PayPalPayouts()
