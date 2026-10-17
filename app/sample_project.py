"""One-click "instant sample project" for judges and cold starts.

Replays app/sample_project.json, a scope + price reference + proposal recorded from a real DeepSeek run of the
Bloom Pilates sample brief (scripts/record_sample_project.py). Line items, due dates and the total are recomputed
from today with the same deterministic pricing code as a live quote, so the PayPal steps that follow (drafts,
approve & send, checkout, webhook) are exactly the real ones. No model or search call is made, so this path works
even right after the free host wakes up or when today's live LLM budget is spent; the trace says it is a replay."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import agent, config
from .models import PricingReference, Quote, Scope, TraceStep

FIXTURE = Path(__file__).parent / "sample_project.json"


@lru_cache(maxsize=1)
def _fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def load() -> Tuple[str, Scope, PricingReference, Quote, List[TraceStep]]:
    d, s = _fixture(), config.get_settings()
    scope = Scope.model_validate(d["scope"])
    pricing = PricingReference.model_validate(d["pricing"])
    rate, items, total, budget_note, _cut = agent.price_items(scope, pricing, None, "market")
    cover = d["cover_message"].replace("Huilin", s.freelancer_name) if s.freelancer_name else d["cover_message"]
    quote = Quote(currency=pricing.currency, hourly_rate=rate, total=total, line_items=items, budget_note=budget_note,
                  terms=list(agent.QUOTE_TERMS), cover_message=cover, negotiation_tips=list(d.get("negotiation_tips", [])))
    when = d.get("recorded_at", "")[:10]
    trace = [TraceStep(step=f"Replay recorded sample: {t['step']}", model=t["model"], mode="replay", ms=0,
                       tier=t.get("tier", "tool"), tokens=t.get("tokens", 0),
                       note=f"recorded {when} ({t['mode']}) · no model call now")
             for t in d.get("recorded_trace", [])]
    trace.append(TraceStep(step="Price milestones from today", model="pricing-rules", mode="replay", ms=0, tier="tool",
                           note=f"{len(items)} milestones · ${total:,.0f} @ ${rate:,.0f}/h"))
    return d["brief"], scope, pricing, quote, trace
