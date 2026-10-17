import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import agent, config, store
from .adapters import llm as llm_mod
from .adapters.http import AdapterError
from .adapters.payments import client_for, get_payments, MockPayments
from .models import (Invoice, InvoiceDraftRequest, InvoiceDraftResponse, PricingRequest, PricingResponse, QuoteRequest,
                     QuoteResponse, ReminderResponse, ReminderSendResponse, ScopeRequest, ScopeResponse, TraceStep)

VERSION = "0.2.0"
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Validate configured Nemotron model IDs against GET /v1/models without blocking boot.
    if config.get_settings().llm_live:
        threading.Thread(target=llm_mod.check_models, daemon=True).start()
    yield


app = FastAPI(title="Scope2Cash", version=VERSION, lifespan=lifespan)


@app.exception_handler(AdapterError)
async def adapter_error(_req: Request, e: AdapterError):
    return JSONResponse(status_code=502, content={"detail": str(e), "provider": e.provider})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/health")
def health():
    s = config.get_settings()
    chk = llm_mod.MODEL_CHECK
    warnings = []
    if chk.checked and not chk.ok:
        warnings.append("Model check: " + (chk.error or "missing " + ", ".join(chk.missing)))
    warnings += [f"Model check: {n}" for n in chk.notes]
    models = {t: (chk.resolved.get(t) or m) for t, m in s.models().items()}
    return {"ok": True, "version": VERSION, "warnings": warnings, "adapters": {
        "llm": {"mode": "live" if s.llm_live else "mock", "provider": "Nebius Token Factory", "models": models,
                "model_check": chk.as_dict()},
        "search": {"mode": "live" if s.search_live else "mock", "provider": "Tavily"},
        "payments": {"mode": "live" if s.payments_live else "mock", "provider": f"PayPal Invoicing v2 ({s.paypal_env})"},
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


def _create_all(pay, req: InvoiceDraftRequest):
    out, n_total = [], len(req.quote.line_items)
    for n, li in enumerate(req.quote.line_items, 1):
        inv = Invoice(id="pending", number=f"S2C-{date.today():%y%m%d}-{n:02d}", milestone=li.milestone, amount=li.amount,
                      currency=req.quote.currency, due_date=li.due_date, client_email=req.client_email)
        note = (f"Milestone {n} of {n_total} for {req.scope.project_title}. Thank you for your business!")
        out.append(pay.create_draft(inv, req.scope.project_title, note, li.deliverables, req.quote.terms, n, n_total))
    return out


@app.post("/api/invoices", response_model=InvoiceDraftResponse)
def create_invoices(req: InvoiceDraftRequest):
    s = config.get_settings()
    pay = get_payments()

    t0 = time.time()
    mode, warning = pay.mode, ""
    try:
        invoices = _create_all(pay, req)
    except AdapterError as e:
        if not s.fallback_to_mock:
            raise
        # Drafts are never sent automatically, so a labelled local draft is a safe fallback for the demo.
        pay, mode = MockPayments(), "fallback"
        warning = f"PayPal draft creation failed ({e.message[:260]}); created local MOCK drafts instead. Nothing was sent."
        invoices = _create_all(pay, req)
    store.clear()
    for i, inv in enumerate(invoices, 1):
        store.save(inv, seq=i)
    store.audit("drafts_created", provider=invoices[0].provider if invoices else "", count=len(invoices),
                total=sum(i.amount for i in invoices), mode=mode)
    extra = "; ".join(getattr(pay, "warnings", []))
    step = TraceStep(step="Create PayPal invoice drafts", model="paypal-invoicing-v2", mode=mode, ms=int((time.time() - t0) * 1000),  # type: ignore[arg-type]
                     tier="tool", note=f"{len(invoices)} drafts · awaiting your approval", warning=" ".join(x for x in (warning, extra) if x))
    return InvoiceDraftResponse(invoices=invoices, trace=[step])


@app.get("/api/invoices", response_model=list[Invoice])
def list_invoices():
    out = []
    for inv in store.all_invoices():
        if inv.provider != "mock" and inv.status in ("SENT", "OVERDUE", "PARTIALLY_PAID"):
            try:
                inv = store.save(client_for(inv).refresh(inv))
            except AdapterError:
                pass  # keep the last known status; the UI still works
        out.append(inv)
    return out


def _get(inv_id: str) -> Invoice:
    inv = store.get(inv_id)
    if not inv:
        raise HTTPException(404, "invoice not found")
    return inv


@app.post("/api/invoices/{inv_id}/send", response_model=Invoice)
def send_invoice(inv_id: str):
    """Human-approved action: only called when the freelancer clicks 'Approve & send'."""
    inv = _get(inv_id)
    if inv.status != "DRAFT":
        raise HTTPException(409, f"invoice is {inv.status}, not DRAFT")
    store.audit("send_approved", inv.id, amount=inv.amount, provider=inv.provider)
    sent = client_for(inv).send(inv).model_copy(update={"approved_at": store.now()})
    store.audit("sent", inv.id, provider_status=sent.provider_status, payer_link=sent.payer_link)
    return store.save(sent)


@app.post("/api/invoices/{inv_id}/reminder", response_model=ReminderResponse)
def draft_reminder(inv_id: str):
    inv = _get(inv_id)
    data, trace = agent.build_reminder(inv)
    note = ("PayPal's reminder email uses PayPal's own wording (custom subject/note are not honoured by the API), "
            "so send this personal note through your usual channel as well.")
    return ReminderResponse(invoice_id=inv.id, subject=str(data["subject"]), body=str(data["body"]),
                            tone=str(data.get("tone", "")), trace=trace, provider_note=note)


@app.post("/api/invoices/{inv_id}/remind", response_model=ReminderSendResponse)
def send_reminder(inv_id: str):
    """Human-approved action: asks PayPal to send its official payment reminder for this invoice."""

    inv = _get(inv_id)
    if inv.status not in ("SENT", "OVERDUE", "PARTIALLY_PAID"):
        raise HTTPException(409, f"invoice is {inv.status}; only sent, unpaid invoices can be reminded")
    t0 = time.time()
    store.audit("remind_approved", inv.id, provider=inv.provider)
    out = client_for(inv).remind(inv).model_copy(update={"reminded_at": store.now()})
    store.save(out)
    step = TraceStep(step="PayPal reminder sent", model="paypal-invoicing-v2", tier="tool", ms=int((time.time() - t0) * 1000),
                     mode="mock" if inv.provider == "mock" else "live", note=f"{inv.number} · approved by you")
    return ReminderSendResponse(invoice=out, trace=[step])


@app.get("/api/samples")
def samples():
    from .evals import load_briefs
    return [{"id": c["id"], "title": c["title"], "brief": c["brief"]} for c in load_briefs()]


@app.get("/api/audit")
def audit_log():
    return store.audit_log()


# ---- demo helpers (mock invoices only) ----
@app.post("/api/demo/{inv_id}/{event}", response_model=Invoice)
def demo_event(inv_id: str, event: str):
    inv = _get(inv_id)
    if inv.provider != "mock":
        raise HTTPException(400, "demo events only apply to mock invoices; pay a sandbox invoice via its payer link")
    if event == "pay":
        inv = inv.model_copy(update={"status": "PAID", "provider_status": "PAID", "days_overdue": 0})
    elif event == "overdue":
        inv = inv.model_copy(update={"status": "OVERDUE", "provider_status": "UNPAID", "days_overdue": 6})
    else:
        raise HTTPException(400, "unknown event")
    store.audit(f"demo_{event}", inv.id)
    return store.save(inv)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
