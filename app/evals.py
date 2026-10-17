"""Eval harness: run the agent over eval/briefs.json and score each output against expected properties.
Used by scripts/eval.py (CLI) and tests/test_eval.py (mock-mode regression gate)."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import agent, config

ROOT = Path(__file__).resolve().parent.parent
BRIEFS = ROOT / "eval" / "briefs.json"
CREEP_WORDS = re.compile(r"scope|feature list|unlimited|timeline|rush|budget|add-on|optional|mvp|cap |compliance|hipaa|reference product|compares", re.I)


@dataclass
class CaseResult:
    id: str
    title: str
    checks: Dict[str, bool] = field(default_factory=dict)
    details: Dict[str, str] = field(default_factory=dict)
    ms: int = 0
    modes: List[str] = field(default_factory=list)
    tokens: int = 0
    warnings: List[str] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def passed(self) -> int:
        return sum(self.checks.values())

    @property
    def total(self) -> int:
        return len(self.checks)


def load_briefs(path: Path = BRIEFS) -> List[Dict[str, Any]]:
    return json.loads(path.read_text())


def _check(res: CaseResult, name: str, ok: bool, detail: str = "") -> None:
    res.checks[name] = bool(ok)
    if detail:
        res.details[name] = detail


def run_case(case: Dict[str, Any]) -> CaseResult:
    exp = case["expect"]
    res = CaseResult(case["id"], case["title"])
    t0 = time.time()
    try:
        scope, t1 = agent.build_scope(case["brief"])
        pricing, t2 = agent.build_pricing(scope)
        quote, t3 = agent.build_quote(scope, pricing, None, "market")
    except Exception as e:  # noqa: BLE001 - an eval must report, not crash
        res.error = f"{e.__class__.__name__}: {e}"[:300]
        res.ms = int((time.time() - t0) * 1000)
        _check(res, "runs", False, res.error)
        return res
    res.ms = int((time.time() - t0) * 1000)
    trace = t1 + t2 + t3
    res.modes = sorted({t.mode for t in trace})
    res.tokens = sum(t.tokens for t in trace)
    res.warnings = [t.warning for t in trace if t.warning]
    hours = sum(m.est_hours for m in scope.milestones)
    res.summary = {"type": scope.project_type, "milestones": len(scope.milestones), "hours": round(hours),
                   "confidence": scope.confidence, "questions": len(scope.open_questions), "budget": scope.client_budget,
                   "deadline": scope.deadline, "language": scope.language, "rate": quote.hourly_rate, "total": quote.total,
                   "grounded": pricing.grounded, "client": scope.client_name}

    _check(res, "milestones_3_to_7", 3 <= len(scope.milestones) <= 7, f"{len(scope.milestones)} milestones")
    _check(res, "deliverables_present", all(m.deliverables and m.est_hours > 0 for m in scope.milestones))
    _check(res, "project_type", scope.project_type in exp["project_type"], f"got {scope.project_type}, want {exp['project_type']}")
    lo, hi = exp["hours"]
    _check(res, "hours_in_range", lo <= hours <= hi, f"{hours:.0f} h, want {lo}-{hi}")
    _check(res, "no_giant_milestone", max(m.est_hours for m in scope.milestones) <= 0.7 * hours + 1 or len(scope.milestones) < 3,
           "largest milestone <= 70% of hours")
    if exp.get("vague"):
        _check(res, "asks_when_vague", len(scope.open_questions) >= 3 and scope.confidence <= 0.5,
               f"{len(scope.open_questions)} questions, confidence {scope.confidence}")
    else:
        _check(res, "confident_when_clear", scope.confidence >= 0.5, f"confidence {scope.confidence}")
    if exp.get("creep"):
        _check(res, "flags_scope_risk", any(CREEP_WORDS.search(r) for r in scope.risks), "; ".join(scope.risks)[:200])
    want_budget = exp.get("budget")
    if want_budget is None:
        _check(res, "no_invented_budget", scope.client_budget is None, f"got {scope.client_budget}")
    else:
        got = scope.client_budget or 0
        _check(res, "budget_extracted", abs(got - want_budget) <= 0.05 * want_budget, f"got {scope.client_budget}, want {want_budget}")
        if quote.total > want_budget:
            _check(res, "budget_gap_explained", bool(quote.budget_note), quote.budget_note[:160])
    _check(res, "deadline_detected", bool(scope.deadline) == bool(exp.get("deadline")), f"got {scope.deadline!r}")
    if exp.get("language"):
        _check(res, "language_detected", scope.language.lower().startswith(exp["language"]), f"got {scope.language}")
    if exp.get("client_name"):
        _check(res, "client_name", scope.client_name.lower() == exp["client_name"].lower(), f"got {scope.client_name!r}")
    # Pricing must be backed by sources, or explicitly labelled as a placeholder.
    _check(res, "price_sourced_or_labelled", pricing.grounded or "placeholder" in pricing.rationale.lower(),
           f"grounded={pricing.grounded}, evidence={pricing.evidence[:3]}")
    _check(res, "price_has_citations", len(pricing.sources) >= 1 and all(s.url for s in pricing.sources))
    pct = sum(li.percent for li in quote.line_items)
    dues = [li.due_date for li in quote.line_items]
    _check(res, "quote_arithmetic", quote.total == sum(li.amount for li in quote.line_items) and abs(pct - 100) <= 0.6
           and dues == sorted(dues), f"sum%={pct:.1f}")
    allowed = {round(quote.total), round(quote.hourly_rate)}
    found = {round(float(x.replace(",", ""))) for x in re.findall(r"\$\s?(\d[\d,]*(?:\.\d+)?)", quote.cover_message)}
    _check(res, "cover_no_invented_prices", found <= allowed, f"found {sorted(found)}, allowed {sorted(allowed)}")
    _check(res, "three_negotiation_tips", len(quote.negotiation_tips) == 3)
    return res


HOLDOUT = ROOT / "eval" / "briefs_holdout.json"


def run(only: Optional[List[str]] = None, path: Path = BRIEFS) -> List[CaseResult]:
    cases = [c for c in load_briefs(path) if not only or c["id"] in only]
    return [run_case(c) for c in cases]


def scorecard(results: List[CaseResult]) -> Dict[str, Any]:
    checks: Dict[str, List[bool]] = {}
    for r in results:
        for k, v in r.checks.items():
            checks.setdefault(k, []).append(v)
    passed = sum(r.passed for r in results)
    total = sum(r.total for r in results)
    return {"cases": len(results), "passed_checks": passed, "total_checks": total,
            "pass_rate": round(passed / total, 3) if total else 0.0,
            "perfect_cases": sum(1 for r in results if r.passed == r.total),
            "by_check": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(checks.items())},
            "modes": sorted({m for r in results for m in r.modes}),
            "tokens": sum(r.tokens for r in results), "ms": sum(r.ms for r in results)}


def to_markdown(results: List[CaseResult], card: Dict[str, Any], label: str = "main set (eval/briefs.json)") -> str:
    s = config.get_settings()
    when = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M (UTC%z)")
    llm = f"live ({s.llm_label})" if s.llm_live else "mock (offline heuristics)"
    search = "live (Tavily)" if s.search_live else "mock (labelled samples)"
    lines = [f"# Scope2Cash eval results: {label}", "",
             f"- Run: {when}", f"- LLM: {llm}; models fast=`{s.model_fast}`, main=`{s.model_main}`, deep=`{s.model_deep}`",
             f"- Search: {search}", f"- Modes seen in traces: {', '.join(card['modes'])}",
             f"- **Score: {card['passed_checks']}/{card['total_checks']} checks ({card['pass_rate'] * 100:.0f}%), "
             f"{card['perfect_cases']}/{card['cases']} cases perfect**", ""]
    if not s.llm_live:
        lines += ["> Mock mode exercises the pipeline, guardrails and scoring offline. It is a regression gate for the "
                  "harness and heuristics, not evidence of model quality"
                  + (" (the heuristics were written alongside these cases)" if "hold-out" not in label else
                     " (these hold-out briefs were written after the heuristics and never used to tune them)")
                  + "; rerun with `--mode live` once keys are set.", ""]
    lines += ["| Case | Score | Type | Hours | Milestones | Conf. | Qs | Budget | Deadline | Rate | Quote | Grounded | Time |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        m = r.summary
        if r.error:
            lines.append(f"| {r.id} | 0/1 | error | | | | | | | | | | {r.ms} ms |")
            continue
        lines.append(f"| {r.id} | {r.passed}/{r.total} | {m['type']} | {m['hours']} | {m['milestones']} | {m['confidence']:.2f} | "
                     f"{m['questions']} | {('$%s' % format(m['budget'], ',.0f')) if m['budget'] else '-'} | {m['deadline'] or '-'} | "
                     f"${m['rate']:.0f}/h | ${m['total']:,.0f} | {'yes' if m['grounded'] else 'no'} | {r.ms} ms |")
    lines += ["", "## Pass rate by check", "", "| Check | Passed |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in card["by_check"].items()]
    fails = [(r, k) for r in results for k, ok in r.checks.items() if not ok]
    lines += ["", "## Failures", ""]
    lines += [f"- **{r.id}** `{k}`: {r.details.get(k, '')}" for r, k in fails] or ["- none"]
    warns = [(r.id, w) for r in results for w in r.warnings]
    if warns:
        lines += ["", "## Warnings surfaced in traces", ""] + [f"- {i}: {w}" for i, w in warns]
    lines += ["", "## What each check means", "",
              "- `milestones_3_to_7`, `deliverables_present`, `no_giant_milestone`: the plan is invoiceable milestone by milestone.",
              "- `project_type`, `hours_in_range`: classification and effort sanity against a human-set range.",
              "- `asks_when_vague` / `confident_when_clear`: confidence is calibrated; thin briefs produce questions, not guesses.",
              "- `flags_scope_risk`: scope-creep, timeline, budget or compliance signals become explicit risks.",
              "- `no_invented_budget`, `budget_extracted`, `budget_gap_explained`, `deadline_detected`: only stated facts are used.",
              "- `price_sourced_or_labelled`, `price_has_citations`: no hourly price without a cited source, or it is labelled a placeholder.",
              "- `quote_arithmetic`, `cover_no_invented_prices`: totals add up and the proposal mentions no price beyond the total and rate.", ""]
    return "\n".join(lines)


def to_json(results: List[CaseResult], card: Dict[str, Any]) -> str:
    return json.dumps({"scorecard": card, "cases": [{"id": r.id, "passed": r.passed, "total": r.total, "checks": r.checks,
                                                     "details": r.details, "summary": r.summary, "modes": r.modes,
                                                     "tokens": r.tokens, "ms": r.ms, "warnings": r.warnings, "error": r.error}
                                                    for r in results]}, indent=2, default=str)
