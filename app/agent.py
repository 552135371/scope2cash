"""Scope2Cash agent pipeline: requirement -> scope -> market pricing -> quote -> invoices -> reminders.

Routing (tiers map to provider models, e.g. DeepSeek: fast = deepseek-flash/low effort, main = deepseek-flash,
deep = deepseek-v4-pro/high effort; Nemotron: Nano/Super/Ultra): fast extracts scope from routine briefs, main handles
long/non-English briefs, price synthesis, proposals and reminders, and deep does negotiation coaching. Every LLM step has a
deterministic heuristic (app/heuristics.py) used in mock mode and as a visible fallback when a live call fails.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import config, fx
from . import heuristics as H
from .adapters.llm import LLMResult, get_llm
from .adapters.search import get_search
from .models import Invoice, LineItem, PricingReference, Quote, Scope, TraceStep

SCOPE_SCHEMA = ('{"project_title": str, "client_name": str (the business/company name if stated, else the person\'s '
                'first name, else "Client"; just the name, no location, role or extra words), '
                '"summary": str (<= 2 sentences), '
                '"project_type": one of ios|mobile|web|automation|design|data (data = scraping, ETL, data pipelines, '
                'dashboards; automation = workflow tools like n8n/Zapier/Make and app-to-app integrations; web = websites '
                'and web apps, including AI/chat features embedded in a website; ios = iOS-only app; mobile = '
                'cross-platform/Android app; design = logos, branding, graphics), '
                '"milestones": [{"title": str, "deliverables": [str], "est_hours": number, "weeks": number}] (3-6 items, '
                'first is discovery, last is QA/handover), "assumptions": [str], "risks": [str], '
                '"open_questions": [str] (questions to ask the client; more when the brief is vague), '
                '"confidence": number 0-1 (how completely the brief defines the work: 0.6-0.85 when it names the platform '
                'and concrete features or deliverables even if some details are open; 0.3-0.5 when key requirements are '
                'missing; below 0.3 for one-line or "app like X" requests), "client_budget": number or null (only if stated), '
                '"deadline": str or null (only if stated), "language": ISO 639-1 code of the brief}')


def _need(*vals: Any) -> None:
    """Validation helper for LLM output: every value must be present and non-empty."""
    for v in vals:
        if v is None or (isinstance(v, (str, list, dict)) and not (v.strip() if isinstance(v, str) else v)):
            raise ValueError("missing or empty field in model output")


def _trace(step: str, r: LLMResult, note: str = "") -> TraceStep:
    toks = int(r.usage.get("total_tokens") or (r.usage.get("prompt_tokens", 0) + r.usage.get("completion_tokens", 0)))
    return TraceStep(step=step, model=r.model, mode=r.mode, ms=r.ms, tier=r.tier, note=note, warning=r.warning, tokens=toks)  # type: ignore[arg-type]


def _clean_client_name(name: str) -> str:
    """Models sometimes return 'Sarah (Bloom Pilates, Austin)' or 'Lucía – Café Aroma': prefer the business name."""
    name = (name or "").strip().strip('"')
    m = re.match(r"^\s*([^()]+?)\s*\(([^,()]+)", name)
    if m:
        return m.group(2).strip()
    parts = re.split(r"\s+[-–—|]\s+|\s+from\s+", name, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return re.sub(r",.*$", "", name).strip() or "Client"


def build_scope(requirement: str) -> Tuple[Scope, List[TraceStep]]:
    llm = get_llm()
    tier, why = H.route_scope_tier(requirement)
    system = ("You are a senior freelance delivery lead. Turn a client's raw request into a fixed-scope plan a solo "
              "freelancer can quote. Be realistic about hours (a solo senior professional, not an agency). Never invent "
              "budgets or deadlines the client didn't state. Scope the smallest version that meets the stated needs (an "
              "MVP / phase 1) using standard frameworks, plugins and managed services; don't pad hours or add features "
              "the client didn't ask for - list them as optional phase-2 assumptions instead. "
              "If the brief is vague, keep hours conservative, lower the "
              "confidence, and ask specific questions. Write the plan in English even if the brief is not.\n"
              f"Today is {date.today().isoformat()}. Return JSON exactly in this shape: {SCOPE_SCHEMA}")
    r = llm.complete_json(tier, system, requirement[:12000], lambda: H.mock_scope(requirement), validate=Scope.model_validate)
    scope = Scope.model_validate(r.data)
    scope.client_name = _clean_client_name(scope.client_name)
    # Deterministic guardrails: budget/deadline are only kept if the client actually stated them.
    stated_budget, stated_deadline = H.extract_budget(requirement), H.extract_deadline(requirement)
    if scope.client_budget and not stated_budget:
        scope.client_budget = None
    if stated_budget and not scope.client_budget:
        scope.client_budget = stated_budget
    if stated_deadline and not scope.deadline:
        scope.deadline = H.pretty_deadline(stated_deadline)
    # The budget's currency comes from the brief's own text (deterministic), never from the model.
    scope.budget_currency = H.extract_budget_currency(requirement) if scope.client_budget else None
    hours = sum(m.est_hours for m in scope.milestones)
    return scope, [_trace("Extract scope & milestones", r, f"{len(scope.milestones)} milestones · {hours:.0f} h · {why}")]


def pricing_queries(scope: Scope) -> List[str]:
    year = date.today().year
    role = {"ios": "iOS developer", "mobile": "mobile app developer", "web": "full-stack web developer",
            "automation": "n8n Zapier automation specialist", "design": "logo and brand designer",
            "data": "web scraping developer"}.get(scope.project_type, "software developer")
    generic = re.compile(r"discovery|qa\b|launch|handover|final|audit|mapping|testing|monitoring", re.I)
    feats = [re.sub(r"(?i)^core build( i+)?:\s*", "", m.title) for m in scope.milestones if not generic.search(m.title)]
    feats = [f for f in feats if f.lower() not in ("core build", "workflow build")][:3]
    phrase = H.TYPE_PHRASE.get(scope.project_type, "software project")
    return [f"freelance {role} hourly rate {year}",
            f"freelance {phrase} fixed price cost {year}" + (f": {', '.join(feats).lower()}" if feats else "")]


def build_pricing(scope: Scope) -> Tuple[PricingReference, List[TraceStep]]:
    s = config.get_settings()
    search = get_search()
    hours = sum(m.est_hours for m in scope.milestones)
    queries = pricing_queries(scope)
    sr = search.search(queries, max_results=4, project_type=scope.project_type)
    trace = [TraceStep(step="Market research (Tavily)", model="tavily-search", mode=sr.mode, ms=sr.ms, tier="tool",  # type: ignore[arg-type]
                       note=f"{len(queries)} queries · {len(sr.sources)} sources" + (f" · {sr.credits} credits" if sr.credits else ""),
                       warning=sr.warning)]
    snippets = [x.snippet for x in sr.sources]
    llm = get_llm()
    system = ("You price freelance work from market evidence. Use ONLY figures that appear in the numbered sources. "
              "Return JSON {\"hourly_low\": number, \"hourly_high\": number, \"rationale\": str (<= 2 sentences, cite source "
              "numbers like [1])}. If the sources contain no hourly figures, return null for both and say so.")
    user = (f"Project: {scope.project_title} ({scope.project_type}), about {hours:.0f} hours.\nSources:\n"
            + "\n".join(f"[{i + 1}] {x.title}: {x.snippet}" for i, x in enumerate(sr.sources)))

    def _valid(d: Dict[str, Any]) -> None:
        if d.get("hourly_low") is not None:
            float(d["hourly_low"]), float(d["hourly_high"])

    r = llm.complete_json("main", system, user, lambda: H.mock_pricing(scope, snippets, s.default_rate), validate=_valid)
    data = r.data
    if data.get("hourly_low") is None or data.get("hourly_high") is None:
        data = {**H.mock_pricing(scope, snippets, s.default_rate), "rationale": data.get("rationale") or ""}
    lo, hi = float(data["hourly_low"]), float(data["hourly_high"])
    lo, hi, grounded, evidence, adjust = H.ground_pricing(lo, hi, snippets)
    rationale = data.get("rationale", "")
    if not grounded:
        lo, hi = round(s.default_rate * 0.8), round(s.default_rate * 1.4)
        rationale = (f"No hourly figures found in the sources, so this range is a placeholder anchored on your default "
                     f"rate (${s.default_rate:.0f}/h). Treat it as unverified.")
    mid = (lo + hi) / 2
    pricing = PricingReference(currency=s.currency, hourly_low=lo, hourly_high=hi, low=round(lo * hours, -1),
                               median=round(mid * hours, -1), high=round(hi * hours, -1), rationale=rationale,
                               sources=sr.sources, mock=sr.mode != "live", grounded=grounded, evidence=evidence, queries=queries)
    step = _trace("Synthesize price range", r, f"${lo:.0f}-${hi:.0f}/h · " + ("grounded in sources" if grounded else "UNGROUNDED placeholder"))
    if adjust and grounded:
        step.warning = (step.warning + " " if step.warning else "") + f"Guardrail: {adjust}."
    trace.append(step)
    return pricing, trace


def _cut_option(scope: Scope, items: List[LineItem]) -> Optional[Tuple[str, float]]:
    """The most expensive milestone that isn't discovery, core, or handover: the natural thing to drop."""
    skip = ("discovery", "core", "qa", "handover", "audit", "mapping", "final", "scraper build", "workflow build")
    cands = [li for li in items if not any(k in li.milestone.lower() for k in skip)]
    if not cands:
        return None
    c = max(cands, key=lambda li: li.amount)
    return c.milestone, c.amount


def price_items(scope: Scope, pricing: PricingReference, hourly_rate: float | None,
                position: str, currency: str = "USD") -> Tuple[float, List[LineItem], float, str, Optional[Tuple[str, float]]]:
    """Deterministic part of the quote: rate, milestone line items with due dates from today, total, budget note.
    Everything returned is in `currency`. The market range (pricing.currency, USD from the sources) is converted at the
    fx snapshot only to pick a starting rate; an explicit hourly_rate is taken as already being in `currency`."""
    k = fx.rate(pricing.currency, currency) if pricing.currency != currency else 1.0
    lo, hi = pricing.hourly_low * k, pricing.hourly_high * k
    rate = round(hourly_rate or {"value": lo + (hi - lo) * 0.2, "market": (lo + hi) / 2, "premium": lo + (hi - lo) * 0.85}[position])
    start = date.today()
    items, weeks = [], 0.0
    for m in scope.milestones:
        amount = math.ceil(m.est_hours * rate / 10) * 10
        weeks += m.weeks
        items.append(LineItem(milestone=m.title, hours=m.est_hours, amount=amount, deliverables=m.deliverables,
                              due_date=(start + timedelta(days=math.ceil(weeks * 7))).isoformat(), percent=0))
    total = sum(i.amount for i in items)
    for i in items:
        i.percent = round(i.amount / total * 100, 1)
    budget, conv = scope.client_budget, ""
    if budget and scope.budget_currency and scope.budget_currency != currency:
        conv = f" (stated as {fx.fmt(budget, scope.budget_currency)}; ≈ converted at {fx.label()})"
        budget = fx.convert(budget, scope.budget_currency, currency)
    gap = (total - budget) if budget else None
    cut = _cut_option(scope, items)
    budget_note = ""
    if gap is not None:
        if gap > 0:
            budget_note = f"Quote is {fx.fmt(gap, currency)} above the client's stated budget ({fx.fmt(budget, currency)}{conv})."
            if cut:
                budget_note += f" Dropping \"{cut[0]}\" brings it to {fx.fmt(total - cut[1], currency)}."
        else:
            budget_note = f"Quote fits the client's stated budget ({fx.fmt(budget, currency)}{conv})."
    return rate, items, total, budget_note, cut


def fx_note(pricing: PricingReference, currency: str) -> str:
    if pricing.currency == currency:
        return ""
    return (f"Market range is in {pricing.currency} (from the sources); the starting rate was converted at "
            f"1 {pricing.currency} = {fx.rate(pricing.currency, currency):.4f} {currency} ({fx.label()}, a reference "
            f"snapshot, not PayPal's rate). Invoices are drafted in {currency}; nothing is converted on PayPal.")


QUOTE_TERMS = ["Each milestone is invoiced via PayPal when it starts and is due on its milestone date",
               "Work on a milestone begins once its invoice is paid",
               "2 revision rounds per milestone; extra changes billed at the hourly rate",
               "Source files and IP transfer to the client on final payment"]


def build_quote(scope: Scope, pricing: PricingReference, hourly_rate: float | None, position: str,
                currency: str | None = None) -> Tuple[Quote, List[TraceStep]]:
    s = config.get_settings()
    currency = fx.normalize(currency or s.currency)
    rate, items, total, budget_note, cut = price_items(scope, pricing, hourly_rate, position, currency)
    budget = scope.client_budget
    if budget and scope.budget_currency and scope.budget_currency != currency:
        budget = fx.convert(budget, scope.budget_currency, currency)
    gap = (total - budget) if budget else None
    llm = get_llm()
    system = ("You write concise, warm client proposals for an independent freelancer. Return JSON {\"cover_message\": str} "
              "(<= 130 words, plain text, mention milestone-based PayPal invoicing, ask the single most important open "
              f"question, sign as {s.freelancer_name}). The only numbers with a currency you may write are the total and the hourly "
              "rate given, in the given currency (write its symbol or ISO code); never restate the client's budget or invent other prices.")
    payload = {"scope": scope.model_dump(exclude={"assumptions"}), "total": total, "rate": rate, "currency": currency}
    r1 = llm.complete_json("main", system, json.dumps(payload), lambda: H.mock_cover(scope, total, rate, s.freelancer_name, currency),
                           validate=lambda d: _need(d.get("cover_message")))
    system2 = ("You are a pricing negotiation coach for freelancers. Think through the client's budget, timeline and risks, "
               "then return JSON {\"negotiation_tips\": [exactly 3 short, specific, actionable tips]}. Prefer trading scope "
               "over discounting. Only use numbers present in the input.")
    deep_in = {"currency": currency, "total": total, "rate": rate, "client_budget": budget, "deadline": scope.deadline,
               "confidence": scope.confidence, "risks": scope.risks, "milestones": [(li.milestone, li.amount) for li in items],
               "market": pricing.model_dump(include={"currency", "hourly_low", "hourly_high", "low", "median", "high", "grounded"})}
    r2 = llm.complete_json("deep", system2, json.dumps(deep_in), lambda: H.mock_tips(scope, total, rate, gap, cut, currency),
                           validate=lambda d: _need(d.get("negotiation_tips")))
    tips = [str(t) for t in r2.data.get("negotiation_tips", [])][:4]
    quote = Quote(currency=currency, hourly_rate=rate, total=total, line_items=items, budget_note=budget_note,
                  terms=list(QUOTE_TERMS), fx_note=fx_note(pricing, currency),
                  cover_message=str(r1.data.get("cover_message", "")), negotiation_tips=tips)
    return quote, [_trace("Draft proposal message", r1), _trace("Negotiation coaching", r2, f"{len(tips)} tips")]


def build_reminder(inv: Invoice) -> Tuple[Dict[str, Any], List[TraceStep]]:
    s = config.get_settings()
    try:
        days = max((date.today() - date.fromisoformat(inv.due_date)).days, inv.days_overdue)
    except ValueError:
        days = inv.days_overdue
    llm = get_llm()
    system = ("Draft a payment reminder email for an unpaid freelance invoice. Tone escalates with days overdue "
              "(<=3 friendly, <=14 firm, else final but professional). Include the invoice number, amount, due date and "
              f"payer link exactly as given. Sign as {s.freelancer_name}. Return JSON {{\"subject\": str, \"body\": str, \"tone\": \"friendly\"|\"firm\"|\"final\"}}.")
    data_in = {**inv.model_dump(include={"number", "milestone", "amount", "currency", "due_date", "payer_link"}), "days_overdue": days}
    r = llm.complete_json("main", system, json.dumps(data_in), lambda: H.mock_reminder(inv, days, s.freelancer_name),
                          validate=lambda d: _need(d.get("subject"), d.get("body")))
    return r.data, [_trace("Draft payment reminder", r, f"{max(days, 0)} days overdue")]
