"""Sample pipeline for the demo: fictional clients, example.com addresses, mock provider, flagged `sample`."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import List

from .models import Invoice
from . import store

# (client, email, project, milestone, amount, due offset in days, status, amount_due)
ROWS = [
    ("Northwind Coffee Roasters", "ap@northwind-coffee.example.com", "Shopify theme refresh", "Discovery & audit", 600, -34, "PAID", 0),
    ("Northwind Coffee Roasters", "ap@northwind-coffee.example.com", "Shopify theme refresh", "Theme build & QA", 1800, -9, "OVERDUE", None),
    ("Kite & Key Travel", "ops@kitekey.example.com", "Booking FAQ chatbot", "Knowledge base & prompts", 900, 6, "SENT", None),
    ("Kite & Key Travel", "ops@kitekey.example.com", "Booking FAQ chatbot", "Website widget & handover", 1400, 20, "DRAFT", None),
    ("Lumen Dental Clinic", "office@lumendental.example.com", "n8n patient-intake automation", "Workflow build", 450, -2, "OVERDUE", None),
    ("Lumen Dental Clinic", "office@lumendental.example.com", "Automation care plan", "October retainer", 300, -12, "PAID", 0),
    ("Harbor Yoga", "hello@harboryoga.example.com", "iOS class-booking app", "UX & prototype", 1200, -17, "OVERDUE", None),
    ("Harbor Yoga", "hello@harboryoga.example.com", "iOS class-booking app", "Core build", 2400, 14, "PARTIALLY_PAID", 1200),
]


def sample_invoices() -> List[Invoice]:
    today = date.today()
    out = []
    for n, (client, email, project, milestone, amount, offset, status, due) in enumerate(ROWS, 1):
        inv_id = "INV2-MOCK-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()
        due_date = today + timedelta(days=offset)
        out.append(Invoice(
            id=inv_id, number=f"S2C-SAMPLE-{n:02d}", milestone=milestone, amount=float(amount), currency="USD",
            due_date=due_date.isoformat(), client_email=email, status=status, provider="mock",  # type: ignore[arg-type]
            provider_status={"OVERDUE": "UNPAID"}.get(status, status), days_overdue=max(-offset, 0) if status == "OVERDUE" else 0,
            payer_link=f"https://www.sandbox.paypal.com/invoice/p/#{inv_id}", amount_due=None if due is None else float(due),
            client_name=client, project=project, created_at=store.now(), sample=True,
            paid_at=(due_date - timedelta(days=1)).isoformat() if status == "PAID" else "",
            approved_at=store.now() if status != "DRAFT" else ""))
    return out
