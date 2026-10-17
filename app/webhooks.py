"""PayPal webhook listener for invoice events.

Live path (PayPal keys + PAYPAL_WEBHOOK_ID configured): every delivery is verified with PayPal's postback API,
POST /v1/notifications/verify-webhook-signature, using the PAYPAL-* transmission headers and the raw body
(https://developer.paypal.com/api/rest/webhooks/rest/). Only `verification_status == "SUCCESS"` is processed.
After a verified event we re-read the invoice with GET /v2/invoicing/invoices/{id}, because PayPal delivers at
least once and not necessarily in order (https://developer.paypal.com/api/invoicing/webhooks), so the API, not
the event, is the source of truth.

Mock path (offline demo and tests): deliveries are signed locally with an HMAC over the same fields PayPal signs
(transmission_id|transmission_time|webhook_id|sha256(body)). Mock-verified events may only touch mock invoices,
so a forged mock event can never change a real PayPal invoice. The demo "paid" / "cancelled" buttons go through
this exact pipeline instead of flipping a status directly.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from . import config, payouts_flow, store
from .adapters.http import AdapterError
from .adapters.payments import STATUS_MAP, PayPalInvoicing, apply_paypal_invoice, to_paypal_invoice
from .models import Invoice, WebhookResult

MOCK_ALGO = "S2C-MOCK-HMACSHA256"
MOCK_WEBHOOK_ID = "S2CMOCKWEBHOOK"
REPLAY_WINDOW = timedelta(minutes=10)
TERMINAL = ("PAID", "CANCELLED", "REFUNDED")
# event type -> the status it implies when the resource doesn't carry a usable one
EVENT_STATUS = {"INVOICING.INVOICE.PAID": "PAID", "INVOICING.INVOICE.CANCELLED": "CANCELLED",
                "INVOICING.INVOICE.REFUNDED": "REFUNDED", "INVOICING.INVOICE.SENT": "SENT"}
HANDLED = set(EVENT_STATUS) | {"INVOICING.INVOICE.UPDATED", "INVOICING.INVOICE.CREATED", "INVOICING.INVOICE.SCHEDULED"}


class WebhookRejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------ verification
def _mock_sig(secret: str, transmission_id: str, ts: str, webhook_id: str, raw: bytes) -> str:
    msg = f"{transmission_id}|{ts}|{webhook_id}|{hashlib.sha256(raw).hexdigest()}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def sign_mock(raw: bytes, transmission_time: Optional[str] = None) -> Dict[str, str]:
    """Headers for a locally simulated delivery (tests and the demo buttons)."""
    s = config.get_settings()
    tid = str(uuid.uuid4())
    ts = transmission_time or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"PAYPAL-TRANSMISSION-ID": tid, "PAYPAL-TRANSMISSION-TIME": ts, "PAYPAL-AUTH-ALGO": MOCK_ALGO,
            "PAYPAL-CERT-URL": "https://localhost/mock-cert", "Content-Type": "application/json",
            "PAYPAL-TRANSMISSION-SIG": _mock_sig(s.mock_webhook_secret, tid, ts, MOCK_WEBHOOK_ID, raw)}


def verify(raw: bytes, headers: Dict[str, str]) -> str:
    """Return the verifier that accepted this delivery ("paypal-api" | "mock-hmac") or raise WebhookRejected."""
    s = config.get_settings()
    h = {k.lower(): v for k, v in headers.items()}
    if h.get("paypal-auth-algo") == MOCK_ALGO:
        return _verify_mock(raw, h)  # allowed in any mode, but such events can only touch mock invoices
    if not s.payments_live:
        raise WebhookRejected(400, "PayPal keys are not configured, so PayPal-signed deliveries cannot be verified")
    if not s.paypal_webhook_id:
        raise WebhookRejected(503, "PAYPAL_WEBHOOK_ID is not configured, so deliveries cannot be verified")
    try:
        status = PayPalInvoicing().verify_webhook_signature(raw, h, s.paypal_webhook_id)
    except AdapterError as e:
        # 5xx makes PayPal retry later (up to 25 times over 3 days) instead of dropping the event.
        raise WebhookRejected(503, f"verification call failed: {e.message[:200]}")
    if status != "SUCCESS":
        raise WebhookRejected(400, f"PayPal signature verification returned {status}")
    return "paypal-api"


def _verify_mock(raw: bytes, h: Dict[str, str]) -> str:
    s = config.get_settings()
    tid, ts, sig = h.get("paypal-transmission-id", ""), h.get("paypal-transmission-time", ""), h.get("paypal-transmission-sig", "")
    try:
        sent = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise WebhookRejected(400, "bad PAYPAL-TRANSMISSION-TIME")
    if abs(datetime.now(timezone.utc) - sent) > REPLAY_WINDOW:
        raise WebhookRejected(400, "stale transmission (replay window is 10 minutes)")
    if not hmac.compare_digest(sig, _mock_sig(s.mock_webhook_secret, tid, ts, MOCK_WEBHOOK_ID, raw)):
        raise WebhookRejected(400, "bad mock signature")
    return "mock-hmac"


# ------------------------------------------------------------------ processing
def _resource_invoice(event: Dict[str, Any]) -> Dict[str, Any]:
    res = event.get("resource") or {}
    inner = res.get("invoice") if isinstance(res, dict) else None  # some invoicing events nest the invoice
    return inner if isinstance(inner, dict) else (res if isinstance(res, dict) else {})


def _apply(inv: Invoice, event_type: str, res: Dict[str, Any]) -> Tuple[Invoice, str]:
    raw = str(res.get("status") or "")
    new = STATUS_MAP.get(raw) or EVENT_STATUS.get(event_type) or inv.status
    if event_type == "INVOICING.INVOICE.PAID" and new not in ("PAID", "PARTIALLY_PAID"):
        new = "PAID"  # the PAID event also covers "payment made and pending"; the GET refresh refines it when live
    if inv.status in TERMINAL and new not in TERMINAL:
        return inv, f"ignored stale {event_type} (invoice already {inv.status})"
    if inv.status == "OVERDUE" and new == "SENT":
        return inv, "kept OVERDUE"
    upd: Dict[str, Any] = {"status": new, "provider_status": raw or new, "last_event": event_type}
    if new in TERMINAL:
        upd["days_overdue"] = 0
    if new == "PAID":
        upd["paid_at"] = store.now()
        upd["amount_due"] = 0.0
    due = (res.get("due_amount") or {}).get("value")
    if due is not None and new != "PAID":
        upd["amount_due"] = float(due)
    return inv.model_copy(update=upd), f"{inv.status} -> {new}"


def process(raw: bytes, headers: Dict[str, str]) -> WebhookResult:
    verified_by = verify(raw, headers)
    try:
        event = json.loads(raw)
    except ValueError:
        raise WebhookRejected(400, "body is not JSON")
    event_id, event_type = str(event.get("id") or ""), str(event.get("event_type") or "")
    if not event_id or not event_type:
        raise WebhookRejected(400, "missing event id or event_type")
    res = _resource_invoice(event)
    inv_id = str(res.get("id") or "")
    base = dict(event_id=event_id, event_type=event_type, invoice_id=inv_id, verified_by=verified_by)
    if not store.webhook_claim(event_id, event_type, inv_id, verified_by):
        return WebhookResult(status="duplicate", detail="already processed", **base)
    result = _process(event_type, res, inv_id, verified_by, base)
    store.webhook_result(event_id, f"{result.status}: {result.detail}"[:300])
    return result


def _process(event_type: str, res: Dict[str, Any], inv_id: str, verified_by: str, base: Dict[str, Any]) -> WebhookResult:
    if event_type not in HANDLED:
        return WebhookResult(status="ignored", detail="event type not used by Scope2Cash", **base)
    inv = store.get(inv_id) if inv_id else None
    if not inv:
        return WebhookResult(status="ignored", detail="invoice not created by Scope2Cash", **base)
    if verified_by == "mock-hmac" and inv.provider != "mock":
        return WebhookResult(status="ignored", detail="mock-signed events cannot change PayPal invoices", **base)
    updated, how = _apply(inv, event_type, res)
    if verified_by == "paypal-api":
        try:  # authoritative state: events can arrive late, twice, or out of order
            refreshed = apply_paypal_invoice(updated, PayPalInvoicing()._call("GET", f"/v2/invoicing/invoices/{inv.id}").json())
            if refreshed.status != updated.status:
                how += f"; API says {refreshed.status}"
            updated = refreshed.model_copy(update={"last_event": event_type, "paid_at": updated.paid_at})
        except AdapterError as e:
            how += f"; refresh failed ({e.message[:80]}), kept event status"
    store.save(updated)
    store.audit("webhook", inv.id, event_type=event_type, verified_by=verified_by, change=how)
    if updated.status == "PAID" and payouts_flow.queue_if_paid(updated):
        how += "; collaborator payout queued for approval"
    return WebhookResult(status="processed", invoice_status=updated.status, detail=how, **base)


# ------------------------------------------------------------------ mock deliveries
def mock_event(inv: Invoice, event_type: str) -> bytes:
    """A PayPal-shaped invoicing event for a mock invoice (resource = the invoice, as in PayPal's docs)."""
    status = EVENT_STATUS.get(event_type, inv.status)
    res = to_paypal_invoice(inv.model_copy(update={"status": status}))
    if status == "PAID":
        res["payments"] = {"transactions": [{"payment_id": "MOCKTXN" + uuid.uuid4().hex[:10].upper(), "method": "PAYPAL",
                                             "amount": res["amount"], "payment_date": datetime.now(timezone.utc).date().isoformat()}],
                           "paid_amount": res["amount"]}
        res["due_amount"] = {"currency_code": inv.currency, "value": "0.00"}
    ev = {"id": "WH-MOCK-" + uuid.uuid4().hex[:16].upper(), "event_version": "1.0", "resource_version": "2.0",
          "create_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "resource_type": "invoices",
          "event_type": event_type, "summary": f"{event_type.split('.')[-1].title()} invoice {inv.number}", "resource": res,
          "links": [{"href": f"https://api-m.sandbox.paypal.com/v1/notifications/webhooks-events/MOCK", "rel": "self", "method": "GET"}]}
    return json.dumps(ev).encode()


def deliver_mock(inv: Invoice, event_type: str) -> WebhookResult:
    raw = mock_event(inv, event_type)
    return process(raw, sign_mock(raw))
