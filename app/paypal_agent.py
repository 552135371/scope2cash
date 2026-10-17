"""Collections agent built on the official PayPal Agent Toolkit (Python package `paypal-agent-toolkit`).

What we use from the toolkit, unchanged: its tool catalog (`paypal_agent_toolkit.shared.tools.tools`), the
action-based allow-list (`Configuration` + `is_tool_allowed`), each tool's description and pydantic argument
schema (sent to Nemotron as OpenAI-style function definitions, exactly like the toolkit's own
`get_openai_chat_tools()`), and each tool's `execute` handler (argument validation, payload building, endpoint).

What we supply: the transport object the handlers call (`post/get/put/delete`). Live, it rides on Scope2Cash's
PayPal adapter (cached OAuth token, timeouts, retries, PayPal-Request-Id); offline, it is a local mock backed by
the demo store. We import only the framework-agnostic `shared` core: the package's OpenAI-Agents / LangChain /
CrewAI adapters hard-pin old framework versions (crewai-tools==0.13.2 has no Python 3.13 build), so the package
is installed with `--no-deps` (see requirements-paypal-toolkit.txt).

Guardrails: read tools (list/get) run immediately; write tools (send reminder, cancel) are never executed by the
model. They become proposals that the freelancer approves or rejects in the UI, and the guardrails are checked
again at approval time.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date
from importlib import metadata
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import config, store
from .adapters import llm as llm_mod
from .adapters.http import AdapterError
from .adapters.payments import PayPalInvoicing, overdue_days, to_paypal_invoice
from .models import AgentAction, AgentResponse, Invoice, ToolCallRecord, TraceStep

try:
    from paypal_agent_toolkit.shared.configuration import Configuration, is_tool_allowed
    from paypal_agent_toolkit.shared.tools import tools as _TOOLKIT_TOOLS
    TOOLKIT_ERROR = ""
    try:
        TOOLKIT_VERSION = metadata.version("paypal-agent-toolkit")
    except metadata.PackageNotFoundError:  # pragma: no cover
        TOOLKIT_VERSION = "unknown"
except Exception as e:  # pragma: no cover - exercised only when the package is missing
    _TOOLKIT_TOOLS, Configuration, is_tool_allowed = [], None, None
    TOOLKIT_VERSION, TOOLKIT_ERROR = "", f"{e.__class__.__name__}: {e}"

ACTIONS = {"invoices": {"list": True, "get": True, "sendReminder": True, "cancel": True}}
READ_TOOLS = {"list_invoices", "get_invoice"}
WRITE_TOOLS = {"send_invoice_reminder", "cancel_sent_invoice"}
UNPAID = ("SENT", "OVERDUE", "PARTIALLY_PAID")
MAX_PROPOSALS = 10


class AgentUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(_TOOLKIT_TOOLS)


def toolkit_label() -> str:
    return f"paypal-agent-toolkit {TOOLKIT_VERSION}" if available() else "paypal-agent-toolkit (not installed)"


def selected_tools() -> Dict[str, Dict[str, Any]]:
    if not available():
        raise AgentUnavailable("PayPal Agent Toolkit is not installed: pip install --no-deps -r requirements-paypal-toolkit.txt"
                               + (f" ({TOOLKIT_ERROR})" if TOOLKIT_ERROR else ""))
    conf = Configuration(actions=ACTIONS)
    return {t["method"]: t for t in _TOOLKIT_TOOLS if is_tool_allowed(t, conf)}


def tool_schemas() -> List[Dict[str, Any]]:
    """Same shape as PayPalToolkit.get_openai_chat_tools(), plus a note that write tools only queue a proposal."""
    out = []
    for name, t in selected_tools().items():
        desc = t["description"].strip()
        if name in WRITE_TOOLS:
            desc += "\n\nIn Scope2Cash this call is NOT executed immediately: it queues a proposal the freelancer must approve."
        out.append({"type": "function", "function": {"name": name, "description": desc,
                                                       "parameters": t["args_schema"].model_json_schema()}})
    return out


# ------------------------------------------------------------------ transports for the toolkit's handlers
_PATH_ID = re.compile(r"^/v2/invoicing/invoices/([^/?]+)(?:/([a-z-]+))?$")


class LiveToolkitClient:
    """The post/get/put/delete interface the toolkit's handlers expect, on top of our PayPal adapter."""
    mode = "live"

    def __init__(self) -> None:
        self.pp = PayPalInvoicing()

    @staticmethod
    def _clean(uri: str, payload: Any) -> Any:
        # Hardening: PayPal's notification bodies have no invoice_id field (it's in the path) and nulls are noise.
        if isinstance(payload, dict):
            payload = {k: v for k, v in payload.items() if v is not None}
            if re.search(r"/invoices/[^/]+/(remind|cancel|send)$", uri):
                payload.pop("invoice_id", None)
        return payload

    def _json(self, r) -> Any:
        return r.json() if r.content else {}

    def post(self, uri: str, payload: Any) -> Any:
        return self._json(self.pp._call("POST", uri, json=self._clean(uri, payload), idempotency_key=str(uuid.uuid4())))

    def get(self, uri: str) -> Any:
        return self._json(self.pp._call("GET", uri))

    def put(self, uri: str, payload: Any, headers: Optional[Dict[str, str]] = None) -> Any:
        return self._json(self.pp._call("PUT", uri, json=self._clean(uri, payload), prefer=(headers or {}).get("Prefer", "")))

    def delete(self, uri: str) -> Any:
        return self._json(self.pp._call("DELETE", uri))


class MockToolkitClient:
    """Offline stand-in for PayPal that answers the toolkit's requests from the local store."""
    mode = "mock"

    def get(self, uri: str) -> Any:
        path = urlparse(uri).path
        if path == "/v2/invoicing/invoices":
            items = [to_paypal_invoice(i) for i in store.all_invoices()]
            return {"total_items": len(items), "total_pages": 1, "items": items}
        m = _PATH_ID.match(path)
        if m and not m.group(2):
            inv = store.get(m.group(1))
            if not inv:
                raise AdapterError("PayPal (mock)", f"RESOURCE_NOT_FOUND: invoice {m.group(1)}", status=404)
            return to_paypal_invoice(inv)
        raise AdapterError("PayPal (mock)", f"unsupported GET {path}", status=400)

    def post(self, uri: str, payload: Any) -> Any:
        m = _PATH_ID.match(urlparse(uri).path)
        inv = store.get(m.group(1)) if m else None
        if not m or not inv:
            raise AdapterError("PayPal (mock)", f"unsupported POST {uri}", status=400)
        if m.group(2) == "remind":
            store.save(inv.model_copy(update={"reminded_at": store.now()}))
            return {}
        if m.group(2) == "cancel":
            store.save(inv.model_copy(update={"status": "CANCELLED", "provider_status": "CANCELLED", "days_overdue": 0}))
            return {}
        raise AdapterError("PayPal (mock)", f"unsupported POST {uri}", status=400)

    def put(self, uri, payload, headers=None):  # pragma: no cover - no allowed tool uses it
        raise AdapterError("PayPal (mock)", "PUT not supported", status=400)

    def delete(self, uri):  # pragma: no cover
        raise AdapterError("PayPal (mock)", "DELETE not supported", status=400)


def reader_client():
    return LiveToolkitClient() if config.get_settings().payments_live else MockToolkitClient()


def client_for_invoice(inv: Invoice):
    if inv.provider == "mock":
        return MockToolkitClient()
    if not config.get_settings().payments_live:
        raise AdapterError("PayPal", "this invoice lives in PayPal but PayPal keys are not configured")
    return LiveToolkitClient()


def run_tool(client, name: str, args: Dict[str, Any]) -> str:
    """Execute one toolkit tool through its own handler (which validates `args` with the toolkit's schema)."""
    out = selected_tools()[name]["execute"](client, dict(args))
    return out if isinstance(out, str) else json.dumps(out)


# ------------------------------------------------------------------ context helpers
def _days_overdue(inv: Invoice) -> int:
    return max(inv.days_overdue, overdue_days(inv.due_date)) if inv.status in UNPAID else 0


def compact_list(raw: str) -> str:
    """Shrink list_invoices output to what the model needs (keeps prompts small and cheap)."""
    try:
        j = json.loads(raw)
    except ValueError:
        return raw[:6000]
    rows = []
    for it in (j.get("items") or [])[:100]:
        d = it.get("detail") or {}
        rec = ((it.get("primary_recipients") or [{}])[0].get("billing_info") or {})
        x = it.get("x_scope2cash") or {}
        rows.append({"id": it.get("id"), "status": it.get("status"), "number": d.get("invoice_number"),
                     "due_date": (d.get("payment_term") or {}).get("due_date"), "amount": (it.get("amount") or {}).get("value"),
                     "due_amount": (it.get("due_amount") or {}).get("value"), "currency": d.get("currency_code"),
                     "recipient": rec.get("email_address"), "client": x.get("client") or rec.get("business_name"),
                     "milestone": d.get("reference"), "days_overdue": x.get("days_overdue")})
    return json.dumps({"total_items": j.get("total_items", len(rows)), "items": rows})


def _summarize_result(name: str, raw: str) -> str:
    try:
        j = json.loads(raw)
    except ValueError:
        return raw[:120]
    if name == "list_invoices":
        return f"{len(j.get('items') or [])} invoices"
    if name == "get_invoice":
        return f"{(j.get('detail') or {}).get('invoice_number', j.get('id', ''))}: {j.get('status', '')}"
    return raw[:120]


# ------------------------------------------------------------------ proposals (human-in-the-loop)
class Run:
    def __init__(self) -> None:
        self.calls: List[ToolCallRecord] = []
        self.trace: List[TraceStep] = []
        self.proposals: List[AgentAction] = []

    def record(self, name: str, args: Dict[str, Any], mode: str, ms: int, summary: str) -> None:
        kind = "write" if name in WRITE_TOOLS else "read"
        self.calls.append(ToolCallRecord(tool=name, args=args, kind=kind, mode=mode, ms=ms, summary=summary))  # type: ignore[arg-type]
        self.trace.append(TraceStep(step=f"PayPal Agent Toolkit · {name}", model="paypal-agent-toolkit", tier="tool",
                                    mode=mode, ms=ms, note=summary))  # type: ignore[arg-type]

    def propose(self, name: str, args: Dict[str, Any], reason: str = "") -> Tuple[bool, str]:
        """Validate a write-tool call and queue it for approval. Returns (queued, message for the model)."""
        tool = selected_tools()[name]
        try:
            params = tool["args_schema"](**args).model_dump(exclude_none=True)
        except Exception as e:  # pydantic ValidationError
            return False, f"invalid arguments: {str(e)[:200]}"
        problem = guard_action(name, params)
        if problem:
            return False, f"refused by Scope2Cash guardrail: {problem}"
        inv = store.get(params["invoice_id"])
        assert inv is not None
        for p in self.proposals:
            if p.tool == name and p.invoice_id == inv.id:
                return True, json.dumps({"status": "QUEUED_FOR_HUMAN_APPROVAL", "action_id": p.id, "note": "already queued"})
        if len(self.proposals) >= MAX_PROPOSALS:
            return False, "refused: too many proposals in one run"
        verb = "Send PayPal reminder" if name == "send_invoice_reminder" else "Cancel invoice"
        days = _days_overdue(inv)
        summary = (f"{verb} {inv.number} · {inv.client_name or inv.client_email} · ${inv.amount:,.0f}"
                   + (f" · {days} days late" if days else ""))
        a = AgentAction(id="act_" + uuid.uuid4().hex[:10], tool=name, params=params, invoice_id=inv.id, invoice_number=inv.number,
                        summary=summary, reason=reason[:300], created_at=store.now())
        self.proposals.append(a)
        return True, json.dumps({"status": "QUEUED_FOR_HUMAN_APPROVAL", "action_id": a.id})


def guard_action(name: str, params: Dict[str, Any]) -> str:
    """Empty string if allowed. Checked when the agent proposes and again when the human approves."""
    if name not in WRITE_TOOLS:
        return f"{name} is not an approvable action"
    inv = store.get(str(params.get("invoice_id", "")))
    if not inv:
        return "the agent may only act on invoices Scope2Cash created"
    if inv.status not in UNPAID:
        return f"invoice {inv.number} is {inv.status}; only sent, unpaid invoices can be reminded or cancelled"
    if not config.get_settings().recipient_allowed(inv.client_email):
        return "this public demo only acts on sandbox recipients"
    return ""


# ------------------------------------------------------------------ the agent
SYSTEM = """You are the collections agent inside Scope2Cash, working for an independent freelancer named {name}.
You manage their PayPal invoices ONLY through the PayPal Agent Toolkit tools provided.
Today is {today}. An invoice is overdue when its status is SENT, UNPAID or PARTIALLY_PAID and its due_date is before
today, or when days_overdue > 0.
Rules:
1. Start with list_invoices. Use get_invoice to double-check an invoice before proposing an action on it.
2. send_invoice_reminder and cancel_sent_invoice are never executed by you: calling them queues a proposal that the
   freelancer approves. Propose only what the user asked for, at most one action per invoice. Never cancel unless the
   user explicitly asks to cancel. Prefer a gentle reminder for invoices 1-3 days late.
3. Only use invoice ids returned by the tools. Never invent amounts.
4. Finish with a short plain-text answer (max 120 words): who owes what, what you queued for approval and why.
   Never say a reminder was sent; say it is waiting for approval."""


def run_agent(instruction: str) -> AgentResponse:
    selected_tools()  # raises AgentUnavailable early
    s = config.get_settings()
    run = Run()
    t0 = time.time()
    if s.llm_live:
        try:
            answer, model, tokens = _llm_loop(instruction, run)
            run.trace.insert(0, TraceStep(step="Collections agent", model=model, tier="main", mode="live",
                                          ms=int((time.time() - t0) * 1000), tokens=tokens,
                                          note=f"{len(run.calls)} toolkit calls · {len(run.proposals)} proposals"))
            return _finish(answer, run)
        except AdapterError as e:
            if not s.fallback_to_mock:
                raise
            warning = f"Live Nemotron tool-calling failed ({e.message[:160]}); used the offline planner instead."
            run = Run()
            answer = _mock_plan(instruction, run)
            run.trace.insert(0, TraceStep(step="Collections agent", model=s.model_main, tier="main", mode="fallback",
                                          ms=int((time.time() - t0) * 1000), warning=warning,
                                          note=f"{len(run.calls)} toolkit calls · {len(run.proposals)} proposals"))
            return _finish(answer, run)
    answer = _mock_plan(instruction, run)
    run.trace.insert(0, TraceStep(step="Collections agent", model=s.model_main, tier="main", mode="mock",
                                  ms=int((time.time() - t0) * 1000),
                                  note=f"{len(run.calls)} toolkit calls · {len(run.proposals)} proposals"))
    return _finish(answer, run)


def _finish(answer: str, run: Run) -> AgentResponse:
    for p in run.proposals:
        store.save_action(p)
        store.audit("agent_proposed", p.invoice_id, tool=p.tool, action_id=p.id)
    return AgentResponse(answer=answer.strip(), tool_calls=run.calls, proposals=run.proposals, trace=run.trace,
                         toolkit=toolkit_label())


def _llm_loop(instruction: str, run: Run) -> Tuple[str, str, int]:
    s = config.get_settings()
    llm = llm_mod.NebiusLLM()
    model = llm.model_for("main")
    tools = tool_schemas()
    client = reader_client()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM.format(name=s.freelancer_name, today=date.today().isoformat())},
        {"role": "user", "content": instruction}]
    tokens = 0
    for _ in range(max(1, s.agent_max_steps)):
        j = llm.chat_tools("main", messages, tools)
        tokens += int((j.get("usage") or {}).get("total_tokens") or 0)
        msg = ((j.get("choices") or [{}])[0].get("message")) or {}
        calls = msg.get("tool_calls") or []
        if not calls:
            text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
            return text or _default_answer(run), model, tokens
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}") or {}
            except ValueError:
                args = {}
            content = _dispatch(client, name, args, run, reason=msg.get("content") or "")
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": name, "content": content[:8000]})
    return _default_answer(run), model, tokens


def _dispatch(client, name: str, args: Dict[str, Any], run: Run, reason: str = "") -> str:
    t0 = time.time()
    if name in READ_TOOLS:
        try:
            raw = run_tool(client, name, args)
        except AdapterError:
            raise
        except Exception as e:  # validation errors go back to the model so it can correct itself
            run.record(name, args, client.mode, int((time.time() - t0) * 1000), f"error: {str(e)[:80]}")
            return json.dumps({"error": str(e)[:300]})
        run.record(name, args, client.mode, int((time.time() - t0) * 1000), _summarize_result(name, raw))
        return compact_list(raw) if name == "list_invoices" else raw
    if name in WRITE_TOOLS:
        ok, out = run.propose(name, args, reason)
        run.record(name, args, client.mode, int((time.time() - t0) * 1000), "queued for approval" if ok else out[:100])
        return out
    return json.dumps({"error": f"unknown tool {name}"})


def _default_answer(run: Run) -> str:
    if run.proposals:
        return "Queued for your approval: " + "; ".join(p.summary for p in run.proposals) + "."
    return "No action needed right now."


# ------------------------------------------------------------------ offline planner (mock / fallback)
_REMIND = re.compile(r"remind|nudge|chase|follow[- ]?up|dun|ping|reminder|collect", re.I)
_CANCEL = re.compile(r"\bcancel|\bvoid\b|write[- ]?off", re.I)
_DAYS = re.compile(r"(\d+)\s*\+?\s*(?:days?|d\b)", re.I)


def _mock_plan(instruction: str, run: Run) -> str:
    """Deterministic planner that drives the same toolkit tools the LLM would (used offline and as fallback)."""
    s = config.get_settings()
    client = MockToolkitClient() if not s.payments_live else LiveToolkitClient()
    listing = json.loads(compact_list(_dispatch_raw(client, "list_invoices", {"page": 1, "page_size": 100}, run)))
    known = {i.id: i for i in store.all_invoices()}
    items = [it for it in listing["items"] if it["id"] in known]
    text = instruction.lower()
    by_number = [it for it in items if (it.get("number") or "").lower() in text]

    def _client_named(it: Dict[str, Any]) -> bool:
        name = (known[it["id"]].client_name or "").lower()
        return bool(name) and (name in text or name.split()[0] in re.findall(r"[a-z0-9&']+", text))
    named = by_number or [it for it in items if _client_named(it)]  # an explicit invoice number beats a client name
    m = _DAYS.search(text)
    min_days = int(m.group(1)) if m else 1
    unpaid = [it for it in items if known[it["id"]].status in UNPAID]
    overdue = [it for it in unpaid if _days_overdue(known[it["id"]]) >= 1]
    owed = sum(known[it["id"]].amount_due if known[it["id"]].amount_due is not None else known[it["id"]].amount for it in unpaid)
    targets: List[Dict[str, Any]] = []
    tool = ""
    ambiguous = False
    if _CANCEL.search(text):
        tool, targets = "cancel_sent_invoice", [it for it in named if it in unpaid]
        if not by_number and len(targets) > 1:  # "cancel Harbor" with two open invoices: ask, don't guess
            targets, ambiguous = [], True
    elif _REMIND.search(text):
        pool = [it for it in named if it in unpaid] if named else overdue
        # A named client/invoice is reminded even if not yet late, unless the request also gives a day threshold.
        tool, targets = "send_invoice_reminder", [it for it in pool if (named and not m) or _days_overdue(known[it["id"]]) >= min_days]
    for it in targets:
        inv = known[it["id"]]
        _dispatch_raw(client if inv.provider != "mock" else MockToolkitClient(), "get_invoice", {"invoice_id": inv.id}, run)
        days = _days_overdue(inv)
        if tool == "send_invoice_reminder":
            tone = "Friendly nudge" if days <= 3 else ("Following up" if days <= 14 else "Final reminder")
            args = {"invoice_id": inv.id, "subject": f"{tone}: invoice {inv.number}",
                    "note": f"Hi! A quick reminder that invoice {inv.number} for \"{inv.milestone}\" "
                            f"(${inv.amount:,.2f}) was due {inv.due_date}. Thank you! {s.freelancer_name}"}
            reason = f"{days} days past due" if days else "client named in your request"
        else:
            args = {"invoice_id": inv.id, "note": "Cancelled at the freelancer's request.", "send_to_recipient": True}
            reason = "you asked to cancel it"
        ok, out = run.propose(tool, args, reason)
        run.record(tool, args, "mock" if inv.provider == "mock" else "live", 0, "queued for approval" if ok else out[:100])
    late = ", ".join(f"{known[i['id']].client_name or i['recipient']} {i['number']} (${float(i['amount']):,.0f}, "
                     f"{_days_overdue(known[i['id']])}d late)" for i in overdue[:5])
    parts = [f"{len(unpaid)} unpaid invoice{'s' if len(unpaid) != 1 else ''} worth ${owed:,.0f}"
             + (f"; overdue: {late}." if overdue else "; nothing is overdue.")]
    if run.proposals:
        parts.append("Queued for your approval: " + "; ".join(p.summary for p in run.proposals) + ".")
    elif tool == "cancel_sent_invoice":
        parts.append(("That client has several open invoices. " if ambiguous else "")
                     + "Tell me which invoice to cancel (by number or client); I never cancel on a guess.")
    elif tool:
        parts.append(f"No invoice is {min_days}+ days late, so I didn't queue any reminders.")
    parts.append("Nothing is sent until you approve it.")
    return " ".join(parts)


def _dispatch_raw(client, name: str, args: Dict[str, Any], run: Run) -> str:
    t0 = time.time()
    raw = run_tool(client, name, args)
    run.record(name, args, client.mode, int((time.time() - t0) * 1000), _summarize_result(name, raw))
    return raw


# ------------------------------------------------------------------ approval
def decide(action_id: str, approve: bool) -> Tuple[AgentAction, Optional[Invoice]]:
    a = store.get_action(action_id)
    if not a:
        raise KeyError(action_id)
    if a.status != "pending":
        raise ValueError(f"action is already {a.status}")
    if not approve:
        a = a.model_copy(update={"status": "rejected", "decided_at": store.now()})
        store.save_action(a)
        store.audit("agent_rejected", a.invoice_id, tool=a.tool, action_id=a.id)
        return a, store.get(a.invoice_id)
    problem = guard_action(a.tool, a.params)
    if problem:
        a = a.model_copy(update={"status": "failed", "result": problem, "decided_at": store.now()})
        store.save_action(a)
        return a, store.get(a.invoice_id)
    inv = store.get(a.invoice_id)
    assert inv is not None
    store.audit("agent_approved", inv.id, tool=a.tool, action_id=a.id)
    client = client_for_invoice(inv)
    try:
        result = run_tool(client, a.tool, a.params)
    except AdapterError as e:
        a = a.model_copy(update={"status": "failed", "result": e.message[:300], "decided_at": store.now()})
        store.save_action(a)
        raise
    inv = store.get(inv.id) or inv
    if inv.provider != "mock":
        from .adapters.payments import client_for
        try:
            inv = client_for(inv).refresh(inv)
        except AdapterError:
            pass
    if a.tool == "send_invoice_reminder":
        inv = inv.model_copy(update={"reminded_at": store.now()})
    store.save(inv)
    a = a.model_copy(update={"status": "executed", "result": (result or "ok")[:300], "decided_at": store.now()})
    store.save_action(a)
    store.audit("agent_executed", inv.id, tool=a.tool, action_id=a.id, provider=inv.provider)
    return a, inv
