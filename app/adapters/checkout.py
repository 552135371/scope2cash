"""PayPal Orders v2 checkout: a one-click "Pay with PayPal" link per milestone, as an alternative to the invoice's
payer page. Rides on the same OAuth/retry/idempotency transport as the Invoicing adapter (PayPalInvoicing._call).

  POST /v2/checkout/orders                    intent CAPTURE; experience_context return/cancel URLs -> payer-action link
  GET  /v2/checkout/orders/{id}               CREATED / PAYER_ACTION_REQUIRED / APPROVED / COMPLETED
  POST /v2/checkout/orders/{id}/capture       after the buyer approves -> capture id
  POST /v2/invoicing/invoices/{id}/payments   record that capture on the milestone's invoice, so both views say PAID

Sandbox-only extra: `pay_with_test_card` creates and captures an order with a PayPal sandbox test card in one call,
so a demo can show a real (sandbox) payment end to end without a buyer login. It refuses to run outside sandbox.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, Optional

from .. import config
from ..models import Invoice
from .http import AdapterError
from .payments import PayPalInvoicing

# Public PayPal sandbox test card (developer.paypal.com "Credit card generator"); never valid outside sandbox.
SANDBOX_TEST_CARD = {
    "number": "4111111111111111", "expiry": "2030-12", "security_code": "123", "name": "Sandbox Test Buyer",
    "billing_address": {"address_line_1": "1 Main St", "admin_area_2": "San Jose", "admin_area_1": "CA",
                        "postal_code": "95131", "country_code": "US"},
}


def order_link(order: Dict[str, Any]) -> str:
    return next((l.get("href", "") for l in order.get("links", []) if l.get("rel") in ("payer-action", "approve")), "")


def order_capture(order: Dict[str, Any]) -> Dict[str, Any]:
    """First capture in an order representation ({} if none)."""
    for pu in order.get("purchase_units") or []:
        caps = (pu.get("payments") or {}).get("captures") or []
        if caps:
            return caps[0]
    return {}


def order_body(inv: Invoice, return_url: str, cancel_url: str, card: Optional[Dict[str, Any]] = None,
               s: Optional[config.Settings] = None) -> Dict[str, Any]:
    s = s or config.get_settings()
    unit: Dict[str, Any] = {
        "reference_id": inv.id[:256], "custom_id": inv.id[:127], "description": inv.milestone[:127],
        "amount": {"currency_code": inv.currency, "value": f"{inv.amount:.2f}"},
    }
    if card is not None:
        source: Dict[str, Any] = {"card": card}
    else:
        source = {"paypal": {"experience_context": {
            "return_url": return_url, "cancel_url": cancel_url, "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING", "brand_name": (s.paypal_business_name or "Scope2Cash")[:127]}}}
    return {"intent": "CAPTURE", "purchase_units": [unit], "payment_source": source}


class Checkout:
    """Live Orders v2 client (sandbox unless PAYPAL_MODE=live + PAYPAL_ALLOW_LIVE=1)."""
    mode = "live"

    def __init__(self, pp: Optional[PayPalInvoicing] = None) -> None:
        self.pp = pp or PayPalInvoicing()

    def create(self, inv: Invoice, return_url: str, cancel_url: str, card: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.pp._call("POST", "/v2/checkout/orders", json=order_body(inv, return_url, cancel_url, card, self.pp.s),
                             idempotency_key=str(uuid.uuid4()), prefer="return=representation").json()

    def get(self, order_id: str) -> Dict[str, Any]:
        return self.pp._call("GET", f"/v2/checkout/orders/{order_id}").json()

    def capture(self, order_id: str) -> Dict[str, Any]:
        r = self.pp._call("POST", f"/v2/checkout/orders/{order_id}/capture", json={},
                          idempotency_key=f"capture-{order_id}", prefer="return=representation")
        return r.json() if r.content else {}

    def record_on_invoice(self, inv: Invoice, capture: Dict[str, Any]) -> Invoice:
        """Mark the milestone's PayPal invoice as paid with the Orders capture as reference (invoice must be sent)."""
        amt = (capture.get("amount") or {}).get("value") or f"{inv.amount:.2f}"
        body = {"method": "PAYPAL", "payment_date": date.today().isoformat(),
                "amount": {"currency_code": inv.currency, "value": str(amt)},
                "note": f"Paid via PayPal Checkout (capture {capture.get('id', '')})"[:2000]}
        self.pp._call("POST", f"/v2/invoicing/invoices/{inv.id}/payments", json=body,
                      idempotency_key=f"pay-{capture.get('id') or uuid.uuid4()}")
        return self.pp.refresh(inv)

    def pay_with_test_card(self, inv: Invoice) -> Dict[str, Any]:
        if self.pp.s.paypal_env != "sandbox" or "sandbox" not in self.pp.base:
            raise AdapterError("PayPal", "test-card payments are sandbox-only", status=400)
        return self.create(inv, "", "", card=SANDBOX_TEST_CARD)


class MockCheckout:
    mode = "mock"

    def create(self, inv, return_url, cancel_url, card=None):
        oid = "MOCKORDER" + uuid.uuid4().hex[:8].upper()
        if card is not None:
            return {"id": oid, "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [
                {"id": "MOCKCAP" + uuid.uuid4().hex[:8].upper(), "status": "COMPLETED",
                 "amount": {"currency_code": inv.currency, "value": f"{inv.amount:.2f}"}}]}}]}
        return {"id": oid, "status": "PAYER_ACTION_REQUIRED",
                "links": [{"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]}

    def get(self, order_id):
        return {"id": order_id, "status": "PAYER_ACTION_REQUIRED"}

    def capture(self, order_id):
        raise AdapterError("PayPal (mock)", "UNPROCESSABLE_ENTITY: ORDER_NOT_APPROVED (mock orders cannot be approved)", status=422)

    def record_on_invoice(self, inv, capture):
        return inv.model_copy(update={"status": "PAID", "provider_status": "MARKED_AS_PAID", "amount_due": 0.0})

    def pay_with_test_card(self, inv):
        return self.create(inv, "", "", card={})


def checkout_for(inv: Invoice):
    """Mock invoices get mock orders; PayPal-hosted invoices get real Orders v2 calls."""
    if inv.provider == "mock":
        return MockCheckout()
    if not config.get_settings().payments_live:
        raise AdapterError("PayPal", "this invoice lives in PayPal but PayPal keys are not configured")
    return Checkout()
