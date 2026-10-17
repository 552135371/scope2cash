from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import agent, store
from .adapters.payments import get_payments
from .config import settings
from .models import (Invoice, InvoiceDraftRequest, PricingRequest, PricingResponse, QuoteRequest, QuoteResponse,
                     ReminderResponse, ScopeRequest, ScopeResponse)

app = FastAPI(title="Scope2Cash", version="0.1.0")
STATIC = Path(__file__).parent / "static"


@app.get("/api/health")
def health():
    return {"ok": True, "adapters": {
        "llm": {"mode": "live" if settings.llm_live else "mock", "provider": "Nebius Token Factory",
                "models": {"fast": settings.model_fast, "main": settings.model_main, "deep": settings.model_deep}},
        "search": {"mode": "live" if settings.search_live else "mock", "provider": "Tavily"},
        "payments": {"mode": "live" if settings.payments_live else "mock", "provider": f"PayPal Invoicing ({settings.paypal_env})"},
    }}


@app.post("/api/scope", response_model=ScopeResponse)
def scope(req: ScopeRequest):
    s, trace = agent.build_scope(req.requirement)
    return ScopeResponse(scope=s, trace=trace)


@app.post("/api/pricing", response_model=PricingResponse)
def pricing(req: PricingRequest):
    p, trace = agent.build_pricing(req.scope)
    return PricingResponse(pricing=p, trace=trace)


@app.post("/api/quote", response_model=QuoteResponse)
def quote(req: QuoteRequest):
    q, trace = agent.build_quote(req.scope, req.pricing, req.hourly_rate, req.position)
    return QuoteResponse(quote=q, trace=trace)


@app.post("/api/invoices", response_model=list[Invoice])
def create_invoices(req: InvoiceDraftRequest):
    pay = get_payments()
    store.clear()
    out = []
    for n, li in enumerate(req.quote.line_items, 1):
        inv = Invoice(id="pending", number=f"S2C-{date.today():%y%m%d}-{n:02d}", milestone=li.milestone, amount=li.amount,
                      currency=req.quote.currency, due_date=li.due_date, client_email=req.client_email)
        note = f"Milestone {n} of {len(req.quote.line_items)} for {req.scope.project_title}."
        out.append(store.save(pay.create_draft(inv, req.scope.project_title, note)))
    return out


@app.get("/api/invoices", response_model=list[Invoice])
def list_invoices():
    pay = get_payments()
    return [store.save(pay.refresh(i)) if i.status in ("SENT", "OVERDUE") else i for i in store.all_invoices()]


def _get(inv_id: str) -> Invoice:
    inv = store.get(inv_id)
    if not inv:
        raise HTTPException(404, "invoice not found")
    return inv


@app.post("/api/invoices/{inv_id}/send", response_model=Invoice)
def send_invoice(inv_id: str):
    """Human-approved action: only called when the freelancer clicks 'Approve & send'."""
    return store.save(get_payments().send(_get(inv_id)))


@app.post("/api/invoices/{inv_id}/reminder", response_model=ReminderResponse)
def draft_reminder(inv_id: str):
    inv = _get(inv_id)
    data, trace = agent.build_reminder(inv)
    return ReminderResponse(invoice_id=inv.id, subject=data["subject"], body=data["body"], tone=data.get("tone", ""), trace=trace)


# ---- demo helpers (mock payments only) ----
@app.post("/api/demo/{inv_id}/{event}", response_model=Invoice)
def demo_event(inv_id: str, event: str):
    if settings.payments_live:
        raise HTTPException(400, "demo events are disabled in live PayPal mode; pay via the sandbox payer link")
    inv = _get(inv_id)
    if event == "pay":
        inv = inv.model_copy(update={"status": "PAID"})
    elif event == "overdue":
        inv = inv.model_copy(update={"status": "OVERDUE", "days_overdue": 6})
    else:
        raise HTTPException(400, "unknown event")
    return store.save(inv)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
