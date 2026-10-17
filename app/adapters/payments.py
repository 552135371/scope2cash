"""Payments adapter. Live: PayPal Invoicing API v2 (sandbox by default). Mock: in-memory state machine."""
from __future__ import annotations

import time
import uuid
from typing import Dict, Protocol

import httpx

from ..config import settings
from ..models import Invoice


class PaymentsClient(Protocol):
    mode: str

    def create_draft(self, inv: Invoice, title: str, note: str) -> Invoice: ...
    def send(self, inv: Invoice) -> Invoice: ...
    def refresh(self, inv: Invoice) -> Invoice: ...
    def remind(self, inv: Invoice, subject: str, note: str) -> Invoice: ...


class PayPalInvoicing:
    mode = "live"

    def __init__(self) -> None:
        self.base = "https://api-m.sandbox.paypal.com" if settings.paypal_env != "live" else "https://api-m.paypal.com"
        self._token = ""
        self._exp = 0.0

    def _auth(self) -> Dict[str, str]:
        if time.time() > self._exp - 60:
            r = httpx.post(f"{self.base}/v1/oauth2/token", data={"grant_type": "client_credentials"},
                           auth=(settings.paypal_client_id, settings.paypal_client_secret), timeout=30)
            r.raise_for_status()
            j = r.json()
            self._token, self._exp = j["access_token"], time.time() + j.get("expires_in", 3000)
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def create_draft(self, inv, title, note):
        body = {
            "detail": {"currency_code": inv.currency, "invoice_number": inv.number, "note": note,
                       "payment_term": {"due_date": inv.due_date}},
            "primary_recipients": [{"billing_info": {"email_address": inv.client_email}}],
            "items": [{"name": f"{title} - {inv.milestone}"[:200], "quantity": "1",
                       "unit_amount": {"currency_code": inv.currency, "value": f"{inv.amount:.2f}"}}],
        }
        if settings.paypal_invoicer_email:
            body["invoicer"] = {"email_address": settings.paypal_invoicer_email}
        r = httpx.post(f"{self.base}/v2/invoicing/invoices", json=body, headers=self._auth(), timeout=30)
        r.raise_for_status()
        j = r.json()
        inv_id = j.get("id") or j.get("href", "").rstrip("/").split("/")[-1]
        return inv.model_copy(update={"id": inv_id, "status": "DRAFT",
                                      "provider": "paypal-sandbox" if settings.paypal_env != "live" else "paypal-live"})

    def send(self, inv):
        r = httpx.post(f"{self.base}/v2/invoicing/invoices/{inv.id}/send",
                       json={"send_to_invoicer": True}, headers=self._auth(), timeout=30)
        r.raise_for_status()
        return self.refresh(inv)

    def refresh(self, inv):
        r = httpx.get(f"{self.base}/v2/invoicing/invoices/{inv.id}", headers=self._auth(), timeout=30)
        r.raise_for_status()
        j = r.json()
        status = {"DRAFT": "DRAFT", "SENT": "SENT", "UNPAID": "SENT", "SCHEDULED": "SENT", "PAID": "PAID",
                  "MARKED_AS_PAID": "PAID", "CANCELLED": "CANCELLED"}.get(j.get("status", ""), "SENT")
        link = next((l["href"] for l in j.get("links", []) if l.get("rel") == "payer-view"), inv.payer_link)
        return inv.model_copy(update={"status": status, "payer_link": link})

    def remind(self, inv, subject, note):
        r = httpx.post(f"{self.base}/v2/invoicing/invoices/{inv.id}/remind",
                       json={"subject": subject, "note": note, "send_to_invoicer": True},
                       headers=self._auth(), timeout=30)
        r.raise_for_status()
        return self.refresh(inv)


class MockPayments:
    mode = "mock"

    def create_draft(self, inv, title, note):
        inv_id = "INV2-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()
        return inv.model_copy(update={"id": inv_id, "status": "DRAFT", "provider": "mock",
                                      "payer_link": f"https://www.sandbox.paypal.com/invoice/p/#{inv_id}"})

    def send(self, inv):
        return inv.model_copy(update={"status": "SENT"})

    def refresh(self, inv):
        return inv

    def remind(self, inv, subject, note):
        return inv


def get_payments() -> PaymentsClient:
    return PayPalInvoicing() if settings.payments_live else MockPayments()
