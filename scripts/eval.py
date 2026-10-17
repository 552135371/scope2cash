"""Run the Scope2Cash eval set and print a scorecard.

Usage:
  python scripts/eval.py                 # mock mode (offline, free)
  python scripts/eval.py --mode live     # uses NEBIUS_API_KEY / TAVILY_API_KEY from .env (costs tokens/credits)
  python scripts/eval.py --only vague_uber cafe_spanish --out eval/results.md
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["mock", "live", "auto"], default="mock")
ap.add_argument("--only", nargs="*")
ap.add_argument("--set", choices=["main", "holdout"], default="main", help="holdout = briefs never used to tune the heuristics")
ap.add_argument("--out", default="")
ap.add_argument("--min-pass-rate", type=float, default=0.0, help="exit 1 if the pass rate is below this (CI gate)")
args = ap.parse_args()

if args.mode == "mock":
    os.environ["SCOPE2CASH_FORCE_MOCK"] = "1"
elif args.mode == "live":
    os.environ.pop("SCOPE2CASH_FORCE_MOCK", None)

from app import config, evals  # noqa: E402
from app.adapters import llm  # noqa: E402

s = config.reload_settings()
if args.mode == "live" and not s.llm_live:
    sys.exit("live mode needs NEBIUS_API_KEY in .env (TAVILY_API_KEY optional)")
if s.llm_live:
    chk = llm.check_models()
    print("model check:", "ok" if chk.ok else f"PROBLEM {chk.missing or chk.error}", *chk.notes, sep="\n  ")

results = evals.run(args.only, evals.HOLDOUT if args.set == "holdout" else evals.BRIEFS)
card = evals.scorecard(results)
print(f"\n{'case':26} score   type        hours  conf  qs  modes")
for r in results:
    m = r.summary
    print(f"{r.id:26} {r.passed:>2}/{r.total:<3}  {m.get('type', 'error'):10} {m.get('hours', 0):>6}  "
          f"{m.get('confidence', 0):.2f}  {m.get('questions', 0):>2}  {','.join(r.modes)}")
    for k, ok in r.checks.items():
        if not ok:
            print(f"{'':28}x {k}: {r.details.get(k, '')}")
print(f"\nSCORE {card['passed_checks']}/{card['total_checks']} ({card['pass_rate'] * 100:.0f}%), "
      f"{card['perfect_cases']}/{card['cases']} perfect cases, tokens={card['tokens']}, {card['ms']} ms")
out = Path(args.out or ROOT / "eval" / ("results_holdout.md" if args.set == "holdout" else "results.md"))
out.parent.mkdir(parents=True, exist_ok=True)
label = ("hold-out set (eval/briefs_holdout.json, never used to tune the heuristics)" if args.set == "holdout"
         else "main set (eval/briefs.json)")
out.write_text(evals.to_markdown(results, card, label))
out.with_suffix(".json").write_text(evals.to_json(results, card))
print(f"wrote {out} and {out.with_suffix('.json')}")
sys.exit(1 if card["pass_rate"] < args.min_pass_rate else 0)
