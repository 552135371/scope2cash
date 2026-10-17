"""Record the one-click "instant sample project" (app/sample_project.json) from a real run of the pipeline.
Runs build_scope + build_pricing + build_quote once on the Bloom Pilates sample brief with whatever LLM/search keys
are set, then stores the scope, the price reference and the model-written proposal text. The app replays this
fixture so a judge can reach the PayPal steps in one click, even when the hosted demo just woke up or today's live
LLM budget is spent. Dates and amounts are recomputed at load time; no keys or emails are stored.
Usage: python scripts/record_sample_project.py"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import agent, config  # noqa: E402
from app.evals import load_briefs  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "sample_project.json"

s = config.get_settings()
brief = next(c for c in load_briefs() if c["id"] == "pilates_ios")
scope, t1 = agent.build_scope(brief["brief"])
pricing, t2 = agent.build_pricing(scope)
quote, t3 = agent.build_quote(scope, pricing, None, "market")
trace = t1 + t2 + t3
modes = sorted({t.mode for t in trace})
data = {
    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "brief_id": brief["id"], "brief": brief["brief"],
    "llm": {"provider": s.llm_label if s.llm_live else "offline heuristics", "models": s.models() if s.llm_live else {}},
    "search": "Tavily" if s.search_live else "labelled sample sources (no Tavily key)",
    "modes": modes,
    "scope": scope.model_dump(), "pricing": pricing.model_dump(),
    "cover_message": quote.cover_message, "negotiation_tips": quote.negotiation_tips,
    "recorded_trace": [t.model_dump(include={"step", "model", "mode", "tier", "ms", "tokens"}) for t in trace],
}
blob = json.dumps(data, indent=2, ensure_ascii=False)
for k in ("DEEPSEEK_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX_BUSINESS_EMAIL", "TAVILY_API_KEY"):
    import os
    v = os.environ.get(k, "").strip()
    assert not (v and len(v) > 6 and v in blob), f"{k} leaked into the fixture"
OUT.write_text(blob + "\n")
print(f"wrote {OUT} · modes={modes} · total ${quote.total:,.0f} · {len(quote.line_items)} milestones")
