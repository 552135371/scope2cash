"""Routes for PayPal Orders v2 checkout links (see app/adapters/checkout.py).

  POST /api/invoices/{id}/checkout        create a "Pay with PayPal" order for a sent milestone -> buyer link
  GET  /api/invoices/{id}/checkout        refresh the order; if the buyer approved it, capture and mark the invoice paid
  GET  /paypal/return?token=<order id>    PayPal's return_url after approval: capture, record on invoice, back to the app
  GET  /paypal/cancel                     PayPal's cancel_url
  POST /api/demo/{id}/sandbox-card-pay    sandbox only: pay the milestone with a PayPal sandbox test card (real sandbox
                                          order + capture + invoice payment), so the demo shows a real PAID transition
Capturing only happens after PayPal reports the buyer's approval (or for the sandbox test card), and every step is
written to the audit log.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import config, store
from .adapters.checkout import checkout_for, order_capture, order_link
from .adapters.http import AdapterError
from .models import Invoice, TraceStep

router = APIRouter()
UNPAID = ("SENT", "OVERDUE", "PARTIALLY_PAID")


def _get(inv_id: str) -> Invoice:
    inv = store.get(inv_id)
    if not inv:
        raise HTTPException(404, "invoice not found")
    return inv


def _base_url(request: Request) -> str:
    return (config._get("PUBLIC_BASE_URL") or str(request.base_url)).rstrip("/")  # set when deployed behind a proxy


def _by_order(order_id: str) -> Optional[Invoice]:
    return next((i for i in store.all_invoices() if i.order_id and i.order_id == order_id), None)


def _complete(inv: Invoice, order: dict, via: str) -> Invoice:
    """Order captured -> record the payment on the PayPal invoice so the pipeline, PayPal and webhooks agree."""
    cap = order_capture(order)
    inv = inv.model_copy(update={"order_status": order.get("status", inv.order_status), "capture_id": cap.get("id", "")})
    if order.get("status") == "COMPLETED" and cap.get("status") == "COMPLETED":
        co = checkout_for(inv)
        try:
            inv = co.record_on_invoice(inv, cap)
        except AdapterError as e:  # the money is captured either way; show the invoice-side problem
            store.audit("checkout_record_failed", inv.id, error=e.message[:200])
        inv = inv.model_copy(update={"paid_at": inv.paid_at or store.now(), "last_event": f"CHECKOUT.ORDER.COMPLETED ({via})",
                                     "status": "PAID" if inv.status not in ("PAID", "REFUNDED") else inv.status,
                                     "amount_due": 0.0, "days_overdue": 0})
        store.audit("checkout_captured", inv.id, order_id=inv.order_id, capture_id=inv.capture_id, via=via,
                    amount=(cap.get("amount") or {}).get("value"))
    return store.save(inv)


@router.post("/api/invoices/{inv_id}/checkout")
def create_checkout(inv_id: str, request: Request):
    inv = _get(inv_id)
    if inv.status not in UNPAID:
        raise HTTPException(409, f"invoice is {inv.status}; send it first (a checkout link is for an unpaid, sent milestone)")
    t0 = time.time()
    base = _base_url(request)
    order = checkout_for(inv).create(inv, f"{base}/paypal/return", f"{base}/paypal/cancel")
    inv = store.save(inv.model_copy(update={"order_id": order.get("id", ""), "order_status": order.get("status", ""),
                                            "checkout_link": order_link(order)}))
    store.audit("checkout_created", inv.id, order_id=inv.order_id, provider=inv.provider)
    step = TraceStep(step="PayPal checkout link", model="paypal-orders-v2", tier="tool", ms=int((time.time() - t0) * 1000),
                     mode="mock" if inv.provider == "mock" else "live", note=f"{inv.number} · order {inv.order_id} · {inv.order_status}")
    return {"invoice": inv, "trace": [step]}


@router.get("/api/invoices/{inv_id}/checkout")
def refresh_checkout(inv_id: str):
    inv = _get(inv_id)
    if not inv.order_id:
        raise HTTPException(404, "no checkout order for this invoice")
    co = checkout_for(inv)
    order = co.get(inv.order_id)
    if order.get("status") == "APPROVED":  # buyer approved on PayPal but never came back through return_url
        order = co.capture(inv.order_id)
    if order.get("status") == "COMPLETED":
        return _complete(inv, order, "status-poll")
    return store.save(inv.model_copy(update={"order_status": order.get("status", inv.order_status)}))


@router.get("/paypal/return")
def paypal_return(token: str = ""):
    inv = _by_order(token)
    if not inv:
        return RedirectResponse("/?checkout=unknown#pipe", status_code=303)
    try:
        co = checkout_for(inv)
        order = co.get(token)
        if order.get("status") == "APPROVED":
            order = co.capture(token)
        inv = _complete(inv, order, "return-url")
    except AdapterError as e:
        store.audit("checkout_capture_failed", inv.id, order_id=token, error=e.message[:200])
        return RedirectResponse("/?checkout=failed#pipe", status_code=303)
    return RedirectResponse(f"/?checkout={'paid' if inv.status == 'PAID' else 'pending'}#pipe", status_code=303)


@router.get("/paypal/cancel")
def paypal_cancel(token: str = ""):
    inv = _by_order(token)
    if inv:
        store.audit("checkout_cancelled_by_buyer", inv.id, order_id=token)
    return RedirectResponse("/?checkout=cancelled#pipe", status_code=303)


@router.post("/api/demo/{inv_id}/sandbox-card-pay")
def sandbox_card_pay(inv_id: str):
    """Sandbox-only demo: the 'client' pays with a PayPal sandbox test card through real Orders v2."""
    s = config.get_settings()
    inv = _get(inv_id)
    if inv.provider == "paypal-live" or s.paypal_mode == "live":
        raise HTTPException(400, "sandbox test-card payments are disabled in live mode")
    if inv.status not in UNPAID:
        raise HTTPException(409, f"invoice is {inv.status}; only sent, unpaid invoices can be paid")
    t0 = time.time()
    order = checkout_for(inv).pay_with_test_card(inv)
    inv = inv.model_copy(update={"order_id": order.get("id", "")})
    inv = _complete(inv, order, "sandbox-test-card")
    step = TraceStep(step="Client pays (sandbox test card)", model="paypal-orders-v2", tier="tool",
                     ms=int((time.time() - t0) * 1000), mode="mock" if inv.provider == "mock" else "live",
                     note=f"{inv.number} · order {inv.order_id} · capture {inv.capture_id or '-'} · invoice {inv.status}")
    return {"invoice": inv, "trace": [step]}
