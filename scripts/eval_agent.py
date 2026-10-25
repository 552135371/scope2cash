"""Collections-agent eval: does the agent queue exactly the right PayPal actions (and nothing else)?

Runs every case in eval/agent_cases.json against the 8 SAMPLE invoices in a throwaway SQLite DB.
Nothing is ever executed: the agent only *proposes*; this script checks the proposals.
  python scripts/eval_agent.py               # mock planner (offline, free)
  python scripts/eval_agent.py --mode live   # LLM tool calling via DEEPSEEK_API_KEY (or NEBIUS/NVIDIA) (costs tokens); PayPal stays mock
"""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["mock", "live"], default="mock")
ap.add_argument("--out", default=str(ROOT / "eval" / "results_agent.md"))
ap.add_argument("--min-pass-rate", type=float, default=0.0)
ap.add_argument("--only", default="", help="comma-separated case ids (e.g. to spend live tokens on new cases only)")
args = ap.parse_args()

os.environ["SCOPE2CASH_DB"] = str(Path(tempfile.mkdtemp()) / "agent_eval.db")
if args.mode == "mock":
    os.environ["SCOPE2CASH_FORCE_MOCK"] = "1"
for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):  # sample invoices are mock; never touch a real PayPal account here
    os.environ[k] = ""

from app import config, paypal_agent, store  # noqa: E402
from app.demo_data import sample_invoices  # noqa: E402

s = config.reload_settings()
if args.mode == "live" and not s.llm_live:
    sys.exit("live mode needs an LLM key (DEEPSEEK_API_KEY, NEBIUS_API_KEY or NVIDIA_API_KEY)")
cases = json.loads((ROOT / "eval" / "agent_cases.json").read_text())["cases"]
if args.only:
    cases = [c for c in cases if c["id"] in set(args.only.split(","))]
rows, passed = [], 0
for c in cases:
    store.delete_samples()
    for inv in sample_invoices():
        store.save(inv, seq=store.next_seq())
    t = time.time()
    r = paypal_agent.run_agent(c["instruction"])
    got = sorted([p.tool, p.invoice_number] for p in r.proposals)
    want = sorted(c["expect"])
    ok = got == want
    passed += ok
    modes = sorted({tc.mode for tc in r.tool_calls}) if r.tool_calls else []
    rows.append((c["id"], ok, want, got, ",".join(modes), int((time.time() - t) * 1000)))
    print(f"{'PASS' if ok else 'FAIL'}  {c['id']:<24} want={want} got={got}")
rate = passed / len(cases)
print(f"\nAGENT SCORE {passed}/{len(cases)} ({rate:.0%}) mode={args.mode}")
lines = [f"# Scope2Cash collections-agent eval ({args.mode})", "",
         f"- Run: {datetime.now().astimezone():%Y-%m-%d %H:%M (UTC%z)}", f"- Toolkit: {paypal_agent.toolkit_label()}",
         f"- Score: **{passed}/{len(cases)}** exact proposal sets (proposals only; nothing executed)", "",
         "| case | pass | expected | proposed | tool modes | ms |", "|---|---|---|---|---|---|"]
fmt = lambda xs: "<br>".join(f"{t.replace('send_invoice_reminder', 'remind').replace('cancel_sent_invoice', 'cancel').replace('create_payout', 'payout')} {n}" for t, n in xs) or "(none)"
from app.adapters import llm as _llm  # noqa: E402
if _llm.USAGE_TOTALS:
    rep = _llm.usage_report()
    print("usage:", json.dumps(rep))
    lines[3:3] = [f"- LLM: {s.llm_label}, est. cost ${rep['est_cost_usd_peak']:.4f} (peak rates), "
                  + ", ".join(f"{m} {t['calls']} calls/{t['prompt'] + t['completion']} tok" for m, t in rep["models"].items())]
lines += [f"| {i} | {'yes' if ok else '**NO**'} | {fmt(w)} | {fmt(g)} | {m} | {ms} |" for i, ok, w, g, m, ms in rows]
Path(args.out).write_text("\n".join(lines) + "\n")
print("wrote", args.out)
sys.exit(1 if rate < args.min_pass_rate else 0)
