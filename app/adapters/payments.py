"""Payments adapter.

Live: PayPal Invoicing API v2 (sandbox by default), checked against PayPal's published OpenAPI spec
(paypal-rest-api-specifications/openapi/invoicing_v2.json, v2.6):
  POST /v1/oauth2/token                      client-credentials token (cached until expiry)
  POST /v2/invoicing/generate-next-invoice-number
  POST /v2/invoicing/invoices                create DRAFT (Prefer: return=representation -> full invoice)
  POST /v2/invoicing/invoices/{id}/send      200/202 -> {href, rel: "payer-view", method}
  GET  /v2/invoicing/invoices/{id}           status, detail.metadata.recipient_view_url, due_amount, payments
  POST /v2/invoicing/invoices/{id}/remind    204 No Content
  POST /v1/notifications/verify-webhook-signature   postback check of a webhook delivery -> {verification_status}
Note from the spec: custom `subject`/`note` on send/remind are not honoured (PayPal uses its own email text),
so Scope2Cash's personalised reminder is a separate draft the freelancer sends through their own channel.

Mock: a local state machine with clearly fake INV2-MOCK ids, used offline and as a visible fallback.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

import httpx

from .. import config
from ..models import Invoice
from . import http
from .http import AdapterError

PROVIDER = "PayPal"
STATUS_MAP = {
    "DRAFT": "DRAFT", "SENT": "SENT", "UNPAID": "SENT", "SCHEDULED": "SENT", "SHARED": "SENT", "PAYMENT_PENDING": "SENT",
    "PARTIALLY_PAID": "PARTIALLY_PAID", "PAID": "PAID", "MARKED_AS_PAID": "PAID", "PAID_EXTERNAL": "PAID",
    "CANCELLED": "CANCELLED", "AUTO_CANCELLED": "CANCELLED", "REFUNDED": "REFUNDED", "PARTIALLY_REFUNDED": "REFUNDED",
    "MARKED_AS_REFUNDED": "REFUNDED", "REFUNDED_EXTERNAL": "REFUNDED",
}

_token_lock = threading.Lock()
_token_cache: Dict[str, tuple] = {}  # client_id -> (token, expires_at)


def describe_error(resp: httpx.Response) -> str:
    """PayPal errors look like {name, message, details:[{issue, description}], debug_id}."""
    try:
        j = resp.json()
    except ValueError:
        return resp.text[:200]
    if not isinstance(j, dict):
        return str(j)[:200]
    if "error" in j and "error_description" in j:  # OAuth endpoint format
        return f"{j['error']}: {j['error_description']}"[:200]
    det = (j.get("details") or [{}])[0] or {}
    head = j.get("name", "")
    body = " ".join(x for x in (det.get("issue", ""), det.get("field", ""), det.get("description", "")) if x) or j.get("message", "")
    out = f"{head}: {body}" if head and body else (head or body)
    if j.get("debug_id"):
        out += f" (debug_id={j['debug_id']})"
    return out[:300]


def overdue_days(due: str, today: Optional[date] = None) -> int:
    try:
        return max(((today or date.today()) - date.fromisoformat(due)).days, 0)
    except ValueError:
        return 0


def build_invoice_body(inv: Invoice, title: str, note: str, deliverables: List[str], terms: List[str],
                       index: int, total: int, s: Optional[config.Settings] = None) -> Dict[str, Any]:
    """The create-draft payload. `amount` is computed by PayPal from `items`, so we don't send it (a mismatch
    would be rejected); instead we reconcile the returned amount against our quote."""
    s = s or config.get_settings()
    detail: Dict[str, Any] = {
        "currency_code": inv.currency,
        "reference": f"Milestone {index}/{total}: {inv.milestone}"[:120],
        "note": note[:4000],
        "terms_and_conditions": "\n".join(f"- {t}" for t in terms)[:4000],
        "memo": f"Created by Scope2Cash for {title}"[:500],
        "payment_term": {"due_date": inv.due_date},  # either term_type or due_date, not both
    }
    if inv.number and not inv.number.startswith("AUTO"):
        detail["invoice_number"] = inv.number[:25]
    body: Dict[str, Any] = {
        "detail": detail,
        "primary_recipients": [{"billing_info": {"email_address": inv.client_email}}],
        "items": [{
            "name": f"{inv.milestone}"[:200],
            "description": ("Deliverables: " + "; ".join(deliverables))[:1000] if deliverables else f"{title}"[:1000],
            "quantity": "1",
            "unit_amount": {"currency_code": inv.currency, "value": f"{inv.amount:.2f}"},
            "unit_of_measure": "AMOUNT",
        }],
        "configuration": {"allow_tip": False, "partial_payment": {"allow_partial_payment": False}},
    }
    invoicer: Dict[str, Any] = {}
    if s.paypal_invoicer_email:
        invoicer["email_address"] = s.paypal_invoicer_email  # must be on the merchant's PayPal profile
    if s.paypal_business_name:
        invoicer["business_name"] = s.paypal_business_name[:300]
    if invoicer:
        body["invoicer"] = invoicer
    return body


def apply_paypal_invoice(inv: Invoice, j: Dict[str, Any], today: Optional[date] = None) -> Invoice:
    """Map a PayPal invoice representation onto our Invoice."""
    raw = j.get("status", "")
    status = STATUS_MAP.get(raw, inv.status)
    detail = j.get("detail") or {}
    meta = detail.get("metadata") or {}
    link = meta.get("recipient_view_url") or next(
        (l.get("href") for l in j.get("links", []) if l.get("rel") in ("payer-view", "recipient-view")), "") or inv.payer_link
    due = (detail.get("payment_term") or {}).get("due_date") or inv.due_date
    days = overdue_days(due, today)
    if status in ("SENT", "PARTIALLY_PAID") and days > 0:
        status = "OVERDUE"
    amount_due = (j.get("due_amount") or {}).get("value")
    upd: Dict[str, Any] = {"status": status, "provider_status": raw or inv.provider_status, "payer_link": link,
                           "due_date": due, "days_overdue": days if status == "OVERDUE" else 0,
                           "number": detail.get("invoice_number") or inv.number}
    if amount_due is not None:
        upd["amount_due"] = float(amount_due)
    if j.get("id"):
        upd["id"] = j["id"]
    return inv.model_copy(update=upd)


class PayPalInvoicing:
    mode = "live"

    def __init__(self) -> None:
        self.s = config.get_settings()
        self.base = self.s.paypal_base
        self.warnings: List[str] = []

    # ---- auth
    def _token(self, force: bool = False) -> str:
        with _token_lock:
            tok, exp = _token_cache.get(self.s.paypal_client_id, ("", 0.0))
            if force or not tok or time.time() > exp - 120:
                r = http.request(PROVIDER, "POST", f"{self.base}/v1/oauth2/token", timeout_s=self.s.paypal_timeout_s,
                                 retries=2, data={"grant_type": "client_credentials"}, headers={"Accept": "application/json"},
                                 auth=(self.s.paypal_client_id, self.s.paypal_client_secret), describe_error=describe_error)
                j = r.json()
                tok, exp = j["access_token"], time.time() + float(j.get("expires_in", 3000))
                _token_cache[self.s.paypal_client_id] = (tok, exp)
            return tok

    def _call(self, method: str, path: str, *, json: Any = None, idempotency_key: str = "", prefer: str = "",
              content: Optional[bytes] = None) -> httpx.Response:
        def headers(tok: str) -> Dict[str, str]:
            h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": "application/json"}
            if idempotency_key:
                h["PayPal-Request-Id"] = idempotency_key  # same key across retries -> no duplicate invoices
            if prefer:
                h["Prefer"] = prefer
            return h

        body: Dict[str, Any] = {"content": content} if content is not None else {"json": json}
        try:
            return http.request(PROVIDER, method, f"{self.base}{path}", timeout_s=self.s.paypal_timeout_s, retries=2,
                                headers=headers(self._token()), describe_error=describe_error, **body)
        except AdapterError as e:
            if e.status != 401:
                raise
            return http.request(PROVIDER, method, f"{self.base}{path}", timeout_s=self.s.paypal_timeout_s, retries=1,
                                headers=headers(self._token(force=True)), describe_error=describe_error, **body)

    # ---- operations
    def next_invoice_number(self) -> str:
        r = self._call("POST", "/v2/invoicing/generate-next-invoice-number", json={})
        return r.json().get("invoice_number", "")

    def create_draft(self, inv: Invoice, title: str, note: str, deliverables: List[str] = (), terms: List[str] = (),
                     index: int = 1, total: int = 1) -> Invoice:
        try:
            number = self.next_invoice_number() or "AUTO"
        except AdapterError as e:
            self.warnings.append(f"next invoice number unavailable ({e.message[:80]}); PayPal will auto-number")
            number = "AUTO"
        inv = inv.model_copy(update={"number": number})
        body = build_invoice_body(inv, title, note, list(deliverables), list(terms), index, total, self.s)
        r = self._call("POST", "/v2/invoicing/invoices", json=body, idempotency_key=str(uuid.uuid4()),
                       prefer="return=representation")
        j = r.json() if r.content else {}
        if "detail" not in j:  # minimal response: {"rel":"self","href":".../invoices/INV2-...","method":"GET"}
            inv_id = j.get("id") or (j.get("href") or "").rstrip("/").split("/")[-1]
            j = self._call("GET", f"/v2/invoicing/invoices/{inv_id}").json() if inv_id else {}
        out = apply_paypal_invoice(inv.model_copy(update={"provider": self.s.paypal_provider}), j)
        returned = (j.get("amount") or {}).get("value")
        if returned is not None and abs(float(returned) - inv.amount) > 0.01:
            self.warnings.append(f"{out.number}: PayPal total {returned} differs from quote {inv.amount:.2f}")
        return out

    def send(self, inv: Invoice) -> Invoice:
        r = self._call("POST", f"/v2/invoicing/invoices/{inv.id}/send",
                       json={"send_to_recipient": True, "send_to_invoicer": True}, idempotency_key=str(uuid.uuid4()))
        j = r.json() if r.content else {}
        link = j.get("href", "") if j.get("rel") == "payer-view" else ""
        inv = inv.model_copy(update={"status": "SENT", "payer_link": link or inv.payer_link})
        try:
            return self.refresh(inv)
        except AdapterError as e:
            self.warnings.append(f"sent, but status refresh failed ({e.message[:80]})")
            return inv

    def refresh(self, inv: Invoice) -> Invoice:
        return apply_paypal_invoice(inv, self._call("GET", f"/v2/invoicing/invoices/{inv.id}").json())

    def remind(self, inv: Invoice) -> Invoice:
        self._call("POST", f"/v2/invoicing/invoices/{inv.id}/remind",
                   json={"send_to_recipient": True, "send_to_invoicer": True}, idempotency_key=str(uuid.uuid4()))
        try:
            return self.refresh(inv)
        except AdapterError:
            return inv


    def cancel(self, inv: Invoice, note: str = "") -> Invoice:
        body: Dict[str, Any] = {"send_to_recipient": True, "send_to_invoicer": True}
        if note:
            body["note"] = note[:4000]
        self._call("POST", f"/v2/invoicing/invoices/{inv.id}/cancel", json=body, idempotency_key=str(uuid.uuid4()))
        try:
            return self.refresh(inv)
        except AdapterError:
            return inv.model_copy(update={"status": "CANCELLED", "provider_status": "CANCELLED"})

    # ---- webhooks
    def verify_webhook_signature(self, raw_body: bytes, headers: Dict[str, str], webhook_id: str) -> str:
        """Postback verification (POST /v1/notifications/verify-webhook-signature). PayPal's guide warns that
        re-serialising the event can break verification, so the raw delivery bytes are spliced into the request
        verbatim as `webhook_event`. Returns "SUCCESS" or "FAILURE"."""
        h = {k.lower(): v for k, v in headers.items()}
        fields = {"auth_algo": h.get("paypal-auth-algo", ""), "cert_url": h.get("paypal-cert-url", ""),
                  "transmission_id": h.get("paypal-transmission-id", ""), "transmission_sig": h.get("paypal-transmission-sig", ""),
                  "transmission_time": h.get("paypal-transmission-time", ""), "webhook_id": webhook_id}
        missing = [k for k, v in fields.items() if not v]
        if missing:
            return "FAILURE"
        import json as _json
        head = _json.dumps(fields)[:-1]  # drop the closing brace, then append the raw event
        payload = head.encode() + b', "webhook_event": ' + raw_body.strip() + b"}"
        r = self._call("POST", "/v1/notifications/verify-webhook-signature", content=payload)
        return str((r.json() if r.content else {}).get("verification_status", "FAILURE"))


def to_paypal_invoice(inv: Invoice) -> Dict[str, Any]:
    """Render a stored invoice in PayPal Invoicing v2 shape (used by the mock toolkit client and mock webhooks)."""
    raw = {"OVERDUE": "UNPAID"}.get(inv.status, inv.status)  # PayPal has no OVERDUE status: past-due invoices stay UNPAID/SENT
    due = inv.amount_due if inv.amount_due is not None else (0.0 if inv.status in ("PAID", "CANCELLED", "REFUNDED") else inv.amount)
    return {
        "id": inv.id, "status": raw,
        "detail": {"invoice_number": inv.number, "currency_code": inv.currency, "reference": inv.milestone,
                   "memo": f"Created by Scope2Cash for {inv.project}" if inv.project else "Created by Scope2Cash",
                   "payment_term": {"due_date": inv.due_date},
                   "metadata": {"recipient_view_url": inv.payer_link} if inv.payer_link else {}},
        "primary_recipients": [{"billing_info": {"email_address": inv.client_email,
                                                 **({"business_name": inv.client_name} if inv.client_name else {})}}],
        "amount": {"currency_code": inv.currency, "value": f"{inv.amount:.2f}"},
        "due_amount": {"currency_code": inv.currency, "value": f"{due:.2f}"},
        "links": [{"href": f"https://api-m.sandbox.paypal.com/v2/invoicing/invoices/{inv.id}", "rel": "self", "method": "GET"}],
        # Not a PayPal field: local context for the mock (PayPal derives "overdue" from due_date; demo invoices simulate it).
        "x_scope2cash": {"days_overdue": inv.days_overdue, "client": inv.client_name, "project": inv.project, "mock": True},
    }


class MockPayments:
    mode = "mock"

    def __init__(self) -> None:
        self.warnings: List[str] = []

    def create_draft(self, inv, title, note, deliverables=(), terms=(), index=1, total=1):
        inv_id = "INV2-MOCK-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()
        return inv.model_copy(update={"id": inv_id, "status": "DRAFT", "provider": "mock", "provider_status": "DRAFT",
                                      "payer_link": f"https://www.sandbox.paypal.com/invoice/p/#{inv_id}"})

    def send(self, inv):
        return inv.model_copy(update={"status": "SENT", "provider_status": "SENT"})

    def refresh(self, inv):
        return inv

    def remind(self, inv):
        return inv

    def cancel(self, inv, note=""):
        return inv.model_copy(update={"status": "CANCELLED", "provider_status": "CANCELLED", "days_overdue": 0})


def get_payments():
    return PayPalInvoicing() if config.get_settings().payments_live else MockPayments()


def client_for(inv: Invoice):
    """Route an existing invoice to the backend that created it (mock drafts stay mock even in live mode)."""
    if inv.provider == "mock":
        return MockPayments()
    if not config.get_settings().payments_live:
        raise AdapterError(PROVIDER, "this invoice lives in PayPal but PayPal keys are not configured")
    return PayPalInvoicing()
