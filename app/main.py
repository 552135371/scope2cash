import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import agent, config, guard, paypal_agent, pipeline_stats, store, webhooks
from .checkout_routes import router as checkout_router
from .adapters import llm as llm_mod
from .adapters.http import AdapterError
from .adapters.payments import client_for, get_payments, MockPayments
from .models import (AgentAction, AgentRequest, AgentResponse, CashflowRequest, Invoice, InvoiceDraftRequest, InvoiceDraftResponse,
                     PricingRequest, PricingResponse, QuoteRequest, QuoteResponse, ReminderResponse, ReminderSendResponse,
                     SampleProjectResponse, ScopeRequest, ScopeResponse, TraceStep, WebhookResult)

VERSION = "0.3.1"
BOOTED_AT = store.now()  # lets the UI notice a restart (= the free host wiped its demo data)
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Validate configured model IDs against the provider's GET /models without blocking boot.
    s = config.get_settings()
    if s.llm_live:
        threading.Thread(target=llm_mod.check_models, daemon=True).start()
    # Free hosts wipe the SQLite file on every restart: put the labelled sample clients back so a judge who opens
    # the URL fresh still sees a populated pipeline. Idempotent (only when no sample rows exist).
    if s.autoseed:
        from .demo_data import ensure_samples
        n = ensure_samples()
        if n:
            store.audit("demo_autoseed", count=n)
    yield


app = FastAPI(title="Scope2Cash", version=VERSION, lifespan=lifespan)
app.include_router(checkout_router)  # PayPal Orders v2 checkout links (app/checkout_routes.py)


AI_PATHS = ("/api/scope", "/api/pricing", "/api/quote", "/api/invoices", "/api/agent/collections")


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    p = request.url.path
    if request.method == "POST" and (p in AI_PATHS or p.endswith("/reminder")):
        wait = guard.rate_limited(guard.client_ip(request))
        if wait:
            return JSONResponse(status_code=429, headers={"Retry-After": str(wait)},
                                content={"detail": f"Demo rate limit reached; try again in {wait // 60 + 1} min."})
    return await call_next(request)


def _budget_step() -> list:
    """Spend one live run, or switch this request to the offline heuristics with a visible trace warning."""
    s = config.get_settings()
    if not (s.llm_live or s.search_live) or guard.take_live_run():
        return []
    config.OFFLINE_THIS_REQUEST.set(True)
    return [TraceStep(step="Daily live budget", model="guard", mode="fallback", ms=0, tier="tool",  # type: ignore[arg-type]
                      note=f"cap {s.daily_live_cap} runs/day",
                      warning="Today's live LLM/Tavily budget for this public demo is spent; "
                              "this run used the offline heuristics. Run locally with your own keys for live results.")]


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
    notices = []
    if s.public_demo:
        notices.append("Public demo: invoices only go to sandbox addresses (" + s.recipient_domains + ")"
                        + (f"; live runs today {guard.live_runs_today()}/{s.daily_live_cap}" if s.daily_live_cap else ""))
        notices.append("Free hosting: this demo sleeps when idle and resets its local data on wake-up "
                       "(sample clients reload automatically). Short on time? Click \u201c\u26a1 Instant sample project\u201d.")
    return {"ok": True, "version": VERSION, "warnings": warnings, "notices": notices, "demo_client_email": s.demo_client_email,
            "public_demo": s.public_demo, "booted_at": BOOTED_AT, "adapters": {
        "llm": {"mode": "live" if s.llm_live else "mock", "provider": s.llm_label, "provider_id": s.llm_provider, "models": models,
                "model_check": chk.as_dict()},
        "search": {"mode": "live" if s.search_live else "mock", "provider": "Tavily"},
        "payments": {"mode": "live" if s.payments_live else "mock", "provider": f"PayPal Invoicing v2 + Orders v2 ({s.paypal_mode if s.payments_live else 'offline'})"},
        "webhooks": {"mode": "live" if (s.payments_live and s.paypal_webhook_id) else "mock",
                     "provider": "PayPal webhooks (verify-webhook-signature)" if s.payments_live else "local HMAC-signed simulator",
                     "path": "/api/paypal/webhook"},
        "agent_toolkit": {"mode": "live" if s.payments_live else "mock", "provider": paypal_agent.toolkit_label(),
                          "available": paypal_agent.available(),
                          "tools": sorted(paypal_agent.READ_TOOLS | paypal_agent.WRITE_TOOLS) if paypal_agent.available() else []},
    }}


@app.post("/api/scope", response_model=ScopeResponse)
def scope(req: ScopeRequest):
    pre = _budget_step()
    s, trace = agent.build_scope(req.requirement)
    return ScopeResponse(scope=s, trace=pre + trace)


@app.post("/api/pricing", response_model=PricingResponse)
def pricing(req: PricingRequest):
    pre = _budget_step()
    p, trace = agent.build_pricing(req.scope)
    return PricingResponse(pricing=p, trace=pre + trace)


@app.post("/api/quote", response_model=QuoteResponse)
def quote(req: QuoteRequest):
    pre = _budget_step()
    q, trace = agent.build_quote(req.scope, req.pricing, req.hourly_rate, req.position)
    return QuoteResponse(quote=q, trace=pre + trace)


def _create_all(pay, req: InvoiceDraftRequest):
    out, n_total = [], len(req.quote.line_items)
    # The pipeline keeps every batch, so continue today's sequence (live PayPal numbers come from generate-next-invoice-number).
    prefix = f"S2C-{date.today():%y%m%d}-"
    used = sum(1 for i in store.all_invoices() if i.number.startswith(prefix))
    for n, li in enumerate(req.quote.line_items, 1):
        inv = Invoice(id="pending", number=f"{prefix}{used + n:02d}", milestone=li.milestone, amount=li.amount,
                      currency=req.quote.currency, due_date=li.due_date, client_email=req.client_email,
                      client_name=req.scope.client_name, project=req.scope.project_title, created_at=store.now())
        note = (f"Milestone {n} of {n_total} for {req.scope.project_title}. Thank you for your business!")
        out.append(pay.create_draft(inv, req.scope.project_title, note, li.deliverables, req.quote.terms, n, n_total))
    return out


@app.post("/api/invoices", response_model=InvoiceDraftResponse)
def create_invoices(req: InvoiceDraftRequest):
    s = config.get_settings()
    if not s.recipient_allowed(req.client_email):
        raise HTTPException(400, "This public demo only invoices sandbox addresses (" + s.recipient_domains +
                            "), e.g. a PayPal sandbox buyer like sb-xxxx@personal.example.com.")
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
    # v0.3: invoices accumulate into a cross-client pipeline (AG Grid view) instead of replacing the last project.
    for inv in invoices:
        store.save(inv, seq=store.next_seq())
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
        raise HTTPException(404, "invoice not found. If you are on the hosted demo, the free server probably restarted "
                                 "and reset its local data: create the drafts again (\u26a1 Instant sample project is one click). "
                                 "Invoices already sent still exist in the PayPal sandbox.")
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


# ---- PayPal webhooks ----
@app.post("/api/paypal/webhook", response_model=WebhookResult)
async def paypal_webhook(request: Request):
    """Listener URL to subscribe in the PayPal Developer Dashboard (events: INVOICING.INVOICE.PAID / CANCELLED /
    REFUNDED / UPDATED). Verified with PayPal's verify-webhook-signature API before anything changes."""
    raw = await request.body()
    if len(raw) > 512_000:
        raise HTTPException(413, "payload too large")
    try:
        return webhooks.process(raw, dict(request.headers))
    except webhooks.WebhookRejected as e:
        store.audit("webhook_rejected", reason=e.message[:200])
        raise HTTPException(e.status, e.message)


@app.get("/api/webhooks/events")
def webhook_events():
    return store.webhook_events()


# ---- pipeline dashboard roll-ups (read-only, stored data only: no PayPal or LLM calls) ----
@app.get("/api/pipeline/clients")
def pipeline_clients():
    """'Who still owes what' scoreboard: one row per client, attention-first."""
    return pipeline_stats.client_scoreboard(store.all_invoices(), store.actions("pending", limit=500))


@app.post("/api/pipeline/cashflow")
def pipeline_cashflow(req: CashflowRequest):
    """Weekly collected / expected / overdue / drafts series for the rows the grid currently shows."""
    invs = store.all_invoices()
    if req.invoice_ids is not None:
        wanted = set(req.invoice_ids[:2000])
        invs = [i for i in invs if i.id in wanted]
    return pipeline_stats.cashflow(invs)


# ---- collections agent (PayPal Agent Toolkit) ----
@app.post("/api/agent/collections", response_model=AgentResponse)
def collections_agent(req: AgentRequest):
    pre = _budget_step()
    try:
        out = paypal_agent.run_agent(req.instruction)
    except paypal_agent.AgentUnavailable as e:
        raise HTTPException(503, str(e))
    out.trace = pre + out.trace
    return out


@app.get("/api/agent/actions", response_model=list[AgentAction])
def agent_actions(status: str = ""):
    return store.actions(status)


def _decide(action_id: str, approve: bool):
    try:
        a, inv = paypal_agent.decide(action_id, approve)
    except KeyError:
        raise HTTPException(404, "action not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"action": a, "invoice": inv}


@app.post("/api/agent/actions/{action_id}/approve")
def approve_action(action_id: str):
    """Human-approved: executes the queued PayPal Agent Toolkit call."""
    return _decide(action_id, True)


@app.post("/api/agent/actions/{action_id}/reject")
def reject_action(action_id: str):
    return _decide(action_id, False)


# ---- demo helpers (mock invoices only) ----
DEMO_EVENTS = {"pay": "INVOICING.INVOICE.PAID", "cancel": "INVOICING.INVOICE.CANCELLED"}


@app.post("/api/demo/{inv_id}/{event}", response_model=Invoice)
def demo_event(inv_id: str, event: str):
    inv = _get(inv_id)
    if inv.provider != "mock":
        raise HTTPException(400, "demo events only apply to mock invoices; pay a sandbox invoice via its payer link")
    if event in DEMO_EVENTS:
        # Simulate the client paying/cancelling: a PayPal-shaped, signed webhook goes through the real listener pipeline.
        res = webhooks.deliver_mock(inv, DEMO_EVENTS[event])
        store.audit(f"demo_{event}", inv.id, webhook=res.event_id, result=res.status)
        return _get(inv_id)
    if event == "overdue":  # PayPal has no "overdue" event: it's the due date passing, so this one is simulated directly
        inv = inv.model_copy(update={"status": "OVERDUE", "provider_status": "UNPAID", "days_overdue": 6})
    else:
        raise HTTPException(400, "unknown event")
    store.audit(f"demo_{event}", inv.id)
    return store.save(inv)


@app.post("/api/demo/sample-project", response_model=SampleProjectResponse)
def sample_project():
    """One click to step 4: a scope, price reference and proposal recorded from a real DeepSeek run, re-priced from
    today. No LLM call, so it is instant and does not use the daily live budget. The PayPal steps after it are real."""
    from . import sample_project as sp
    brief, sc, pr, q, trace = sp.load()
    store.audit("demo_sample_project", total=q.total)
    return SampleProjectResponse(brief=brief, scope=sc, pricing=pr, quote=q, trace=trace)


@app.post("/api/demo/seed", response_model=list[Invoice])
def seed_pipeline():
    """Add clearly labelled SAMPLE invoices (fictional clients, mock provider) so the pipeline view has history."""
    from .demo_data import sample_invoices
    store.delete_samples()
    out = [store.save(inv, seq=store.next_seq()) for inv in sample_invoices()]
    store.audit("demo_seed", count=len(out))
    return out


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
