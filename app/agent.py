"""Scope2Cash agent pipeline: requirement -> scope -> market pricing -> quote -> invoices -> reminders.
Each step routes to a Nemotron tier (fast/main/deep). In mock mode deterministic heuristics stand in."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

from .adapters.llm import get_llm
from .adapters.search import get_search
from .config import settings
from .models import (Invoice, LineItem, Milestone, PricingReference, Quote, Scope, TraceStep)

# ---------------------------------------------------------------- heuristics (mock LLM)
COMPONENTS = [
    (r"\b(login|sign ?up|auth|account|user profile)", "User accounts & authentication", ["Email/social sign-in", "Profile & settings"], 10),
    (r"\b(payment|stripe|paypal|checkout|subscription|billing)", "Payments & subscriptions", ["Checkout flow", "Subscription plans", "Receipts"], 14),
    (r"\b(dashboard|admin|analytics|report)", "Admin dashboard & analytics", ["Admin panel", "Key metrics view"], 16),
    (r"\b(chatbot|ai|llm|gpt|assistant|agent|recommend)", "AI assistant integration", ["LLM prompt design", "Chat UI with streaming", "Guardrails & evaluation set"], 18),
    (r"\b(booking|calendar|appointment|schedul)", "Booking & calendar", ["Availability calendar", "Booking confirmation flow"], 14),
    (r"\b(notification|email|reminder|sms|push)", "Notifications", ["Transactional emails", "Reminder triggers"], 6),
    (r"\b(integrat|zapier|n8n|crm|hubspot|airtable|google sheets|api)", "Third-party integrations", ["API integration", "Data sync"], 10),
    (r"\b(landing page|website|marketing site|seo)", "Marketing website", ["Responsive landing page", "Basic SEO setup"], 12),
]


def _detect_type(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(ios|iphone|ipad|swift|swiftui|app store)\b", t):
        return "ios"
    if re.search(r"\b(android|mobile app|react native|flutter)\b", t):
        return "mobile"
    if re.search(r"\b(n8n|zapier|make\.com|automation|workflow)\b", t):
        return "automation"
    return "web"


def _client_name(text: str) -> str:
    for pat in [r"(?:I'm|I am)\s+\w+\s+(?:from|at)\s+([A-Z][\w&\- ]{1,30})", r"(?:We are|We're)\s+([A-Z][\w&\- ]{1,30})",
                r"(?:company|startup|studio|clinic|agency) called\s+([A-Z][\w&\- ]{1,30})"]:
        m = re.search(pat, text)
        if m:
            name = re.split(r"\s+(?:in|from|at|based)\s+", m.group(1).strip())[0]
            return name.rstrip(".,")
    return "Client"


def mock_scope(text: str) -> Dict[str, Any]:
    t = text.lower()
    ptype = _detect_type(text)
    comps = [(n, d, h) for pat, n, d, h in COMPONENTS if re.search(pat, t)]
    if not comps:
        comps = [("Core feature build", ["Main user flow", "Data model & storage"], 24)]
    platform = {"ios": ("Native iOS app (SwiftUI)", ["Core screens in SwiftUI", "Local persistence", "TestFlight build"], 30),
                "mobile": ("Mobile app core", ["Core screens", "Local persistence"], 30),
                "automation": ("Workflow automation core", ["Trigger & routing logic", "Error handling & retries"], 12),
                "web": ("Web app foundation", ["Responsive UI shell", "Backend API & database"], 18)}[ptype]
    core = [platform] + [c for c in comps if c[0] not in ("AI assistant integration", "Payments & subscriptions")]
    advanced = [c for c in comps if c[0] in ("AI assistant integration", "Payments & subscriptions")]
    ms = [{"title": "Discovery & UX design", "deliverables": ["Requirements sign-off", "Clickable wireframes", "Technical plan"],
           "est_hours": 10, "weeks": 1}]
    ms.append({"title": "Core build", "deliverables": [d for c in core for d in c[1]][:6],
               "est_hours": sum(c[2] for c in core), "weeks": 2})
    if advanced:
        ms.append({"title": " & ".join(c[0].split(" ")[0] for c in advanced) + " features",
                   "deliverables": [d for c in advanced for d in c[1]], "est_hours": sum(c[2] for c in advanced), "weeks": 1.5})
    ms.append({"title": "QA, launch & handover", "deliverables": ["Bug-fix pass", "Deployment" if ptype != "ios" else "App Store submission",
                                                                 "Handover docs & 14-day support"], "est_hours": 8, "weeks": 1})
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if len(x.split()) >= 6]
    first = next((x for x in sentences if not re.match(r"(?i)^(hi|hello|hey|dear)\b", x)), sentences[0] if sentences else text[:180])
    title_bits = {"ios": "iOS app", "mobile": "mobile app", "automation": "automation workflow", "web": "web app"}[ptype]
    client = _client_name(text)
    feats = ", ".join(c[0].split(" ")[0].lower() for c in comps[:4])
    return {
        "project_title": f"{client} {title_bits}" if client != "Client" else f"Custom {title_bits} MVP",
        "client_name": _client_name(text),
        "summary": (first[:200] + ("..." if len(first) > 200 else "")) + (f" Key areas: {feats}." if feats else ""),
        "project_type": ptype,
        "milestones": ms,
        "assumptions": ["Client provides brand assets and copy", "Up to 2 revision rounds per milestone",
                        "Third-party service fees (hosting, APIs) billed to client"],
        "risks": ["Scope creep on AI behaviour - fix acceptance examples up front"] if advanced else ["Unclear content ownership"],
        "open_questions": ["What is the launch deadline?", "Who owns hosting and accounts?", "Is there an existing design system?"],
    }


def mock_pricing(scope: Scope, snippets: List[str]) -> Dict[str, Any]:
    nums = [float(x.replace(",", "")) for s in snippets for x in re.findall(r"\$(\d[\d,]*)\s*(?:/h|per hour|-|to)", s)]
    hourly = [n for n in nums if 15 <= n <= 250] or [45, 95]
    lo, hi = min(hourly), max(hourly)
    return {"hourly_low": lo, "hourly_high": hi,
            "rationale": f"Comparable {dict(ios='iOS', mobile='mobile', web='web', automation='automation').get(scope.project_type, scope.project_type)} projects bill ${lo:.0f}-${hi:.0f}/h; range applied to the estimated hours."}


def mock_quote_copy(scope: Scope, total: float, rate: float) -> Dict[str, Any]:
    return {
        "cover_message": (f"Hi {scope.client_name if scope.client_name != 'Client' else 'there'},\n\n"
                          f"Thanks for the detailed brief. I've broken \"{scope.project_title}\" into {len(scope.milestones)} milestones "
                          f"so you only pay as each one is delivered and approved. The total is ${total:,.0f}, based on "
                          f"{sum(m.est_hours for m in scope.milestones):.0f} estimated hours at ${rate:.0f}/h.\n\n"
                          "Each milestone is invoiced through PayPal when it starts, so you're covered by PayPal's buyer protection. "
                          "Happy to jump on a quick call to walk through the plan.\n\nBest,\nHuilin"),
        "negotiation_tips": [
            "Anchor on outcomes: tie each milestone to a demo the client can click through.",
            "If the client pushes on price, trade scope (drop a feature) rather than discounting your rate.",
            "Offer a 5% discount for paying the first two milestones upfront instead of cutting the total.",
        ],
    }


def mock_reminder(inv: Invoice, days_overdue: int, freelancer: str) -> Dict[str, Any]:
    tone = "friendly" if days_overdue <= 3 else ("firm" if days_overdue <= 14 else "final")
    opener = {"friendly": "Just a quick nudge", "firm": "Following up", "final": "A final reminder"}[tone]
    return {"subject": f"{opener}: invoice {inv.number} for \"{inv.milestone}\"", "tone": tone,
            "body": (f"Hi,\n\n{opener} on invoice {inv.number} (${inv.amount:,.2f}) for the \"{inv.milestone}\" milestone, "
                     f"which was due on {inv.due_date}"
                     + (f" ({days_overdue} days ago)" if days_overdue > 0 else "") + ".\n\n"
                     f"You can pay securely via PayPal here: {inv.payer_link or '[PayPal link]'}\n\n"
                     + ("If anything about the deliverable needs adjusting, just reply and I'll sort it out right away.\n\n" if tone != "final" else
                        "Work on the next milestone is paused until this is settled. Please let me know if there's an issue I can help with.\n\n")
                     + f"Thanks,\n{freelancer}")}


# ---------------------------------------------------------------- pipeline
def _trace(step: str, tier: str, ms: int, note: str = "") -> TraceStep:
    llm = get_llm()
    return TraceStep(step=step, model=llm.model_for(tier), mode=llm.mode, ms=ms, note=note)  # type: ignore[arg-type]


def build_scope(requirement: str) -> Tuple[Scope, List[TraceStep]]:
    llm = get_llm()
    system = ("You are a senior freelance delivery lead. Turn a client's raw request into a fixed-scope plan. "
              "Return JSON with keys: project_title, client_name, summary, project_type (ios|mobile|web|automation), "
              "milestones[{title, deliverables[], est_hours, weeks}] (3-5 milestones, realistic hours), assumptions[], risks[], open_questions[].")
    data, ms = llm.complete_json("fast", system, requirement, lambda: mock_scope(requirement))
    scope = Scope.model_validate(data)
    return scope, [_trace("Extract scope & milestones", "fast", ms, f"{len(scope.milestones)} milestones")]


def build_pricing(scope: Scope) -> Tuple[PricingReference, List[TraceStep]]:
    search = get_search()
    hours = sum(m.est_hours for m in scope.milestones)
    query = f"freelance {scope.project_type} developer rates 2026 {scope.project_title} fixed price cost"
    t0 = time.time()
    sources = search.search(query, max_results=4)
    trace = [TraceStep(step="Market research (Tavily)", model="tavily-search", mode=search.mode,  # type: ignore[arg-type]
                       ms=int((time.time() - t0) * 1000) + (0 if search.mode == "live" else 420), note=f"{len(sources)} sources")]
    llm = get_llm()
    system = ("You price freelance software work. From the search snippets, estimate a realistic hourly range for a senior "
              "independent developer. Return JSON {hourly_low, hourly_high, rationale} (rationale <= 2 sentences, cite numbers).")
    user = f"Project: {scope.project_title} ({scope.project_type}), ~{hours:.0f}h.\nSnippets:\n" + "\n".join(f"- {s.title}: {s.snippet}" for s in sources)
    data, ms = llm.complete_json("main", system, user, lambda: mock_pricing(scope, [s.snippet for s in sources]))
    lo, hi = float(data["hourly_low"]), float(data["hourly_high"])
    mid = (lo + hi) / 2
    pricing = PricingReference(currency=settings.currency, hourly_low=lo, hourly_high=hi, low=round(lo * hours, -1),
                               median=round(mid * hours, -1), high=round(hi * hours, -1), rationale=data.get("rationale", ""),
                               sources=sources, mock=search.mode == "mock")
    trace.append(_trace("Synthesize price range", "main", ms, f"${lo:.0f}-${hi:.0f}/h"))
    return pricing, trace


def build_quote(scope: Scope, pricing: PricingReference, hourly_rate: float | None, position: str) -> Tuple[Quote, List[TraceStep]]:
    lo, hi = pricing.hourly_low, pricing.hourly_high
    rate = hourly_rate or {"value": lo + (hi - lo) * 0.2, "market": (lo + hi) / 2, "premium": lo + (hi - lo) * 0.85}[position]
    rate = round(rate)
    start = date.today()
    items, weeks = [], 0.0
    for m in scope.milestones:
        amount = math.ceil(m.est_hours * rate / 10) * 10
        weeks += m.weeks
        items.append(LineItem(milestone=m.title, hours=m.est_hours, amount=amount,
                              due_date=(start + timedelta(days=math.ceil(weeks * 7))).isoformat(), percent=0))
    total = sum(i.amount for i in items)
    for i in items:
        i.percent = round(i.amount / total * 100, 1)
    llm = get_llm()
    system = ("You write concise, warm client proposals for an independent developer. Return JSON {cover_message} "
              "(<= 120 words, plain text, mention milestone-based PayPal invoicing).")
    copy, ms1 = llm.complete_json("main", system, json.dumps({"scope": scope.model_dump(), "total": total, "rate": rate}),
                                  lambda: mock_quote_copy(scope, total, rate))
    system2 = ("You are a pricing negotiation coach. Return JSON {negotiation_tips: [3 short, specific tips]} for this quote.")
    tips, ms2 = llm.complete_json("deep", system2, json.dumps({"total": total, "rate": rate, "market": pricing.model_dump(exclude={'sources'})}),
                                  lambda: mock_quote_copy(scope, total, rate))
    quote = Quote(currency=settings.currency, hourly_rate=rate, total=total, line_items=items,
                  terms=["Milestone invoices issued via PayPal at milestone start; due on the milestone date",
                         "Work on a milestone begins once its invoice is paid",
                         "2 revision rounds per milestone; extra changes billed at the hourly rate",
                         "Source code and IP transfer to client on final payment"],
                  cover_message=copy.get("cover_message", ""), negotiation_tips=tips.get("negotiation_tips", []))
    return quote, [_trace("Draft proposal message", "main", ms1), _trace("Negotiation coaching", "deep", ms2, "3 tips")]


def build_reminder(inv: Invoice, freelancer: str = "Huilin") -> Tuple[Dict[str, Any], List[TraceStep]]:
    days = max((date.today() - date.fromisoformat(inv.due_date)).days, inv.days_overdue)
    llm = get_llm()
    system = ("Draft a payment reminder email for an unpaid freelance invoice. Tone escalates with days overdue "
              "(<=3 friendly, <=14 firm, else final but professional). Return JSON {subject, body, tone}.")
    data, ms = llm.complete_json("main", system, json.dumps({**inv.model_dump(), "days_overdue": days, "freelancer": freelancer}),
                                 lambda: mock_reminder(inv, days, freelancer))
    return data, [_trace("Draft payment reminder", "main", ms, f"{max(days, 0)} days overdue")]
