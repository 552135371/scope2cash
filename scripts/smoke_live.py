"""One-shot live smoke test for when API keys land in .env. Cheap and safe by default:
  - Nebius: GET /v1/models (checks configured Nemotron IDs) + one tiny JSON call per tier, timed
  - Tavily: one basic search (1 credit)
  - PayPal: OAuth token only (no invoice is created unless you pass --paypal-draft, which creates ONE sandbox
    draft that is never sent)
Writes eval/live_observations.md so real latency/behaviour notes can feed the Nebius feedback doc.
Usage: python scripts/smoke_live.py [--paypal-draft]
"""
import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.adapters import llm, payments, search  # noqa: E402
from app.adapters.http import AdapterError  # noqa: E402
from app.models import Invoice  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--paypal-draft", action="store_true")
args = ap.parse_args()
s = config.reload_settings()
lines = [f"# Live smoke test ({datetime.now().astimezone():%Y-%m-%d %H:%M %Z})", ""]


def log(msg):
    print(msg)
    lines.append(f"- {msg}")


if s.llm_live:
    chk = llm.check_models()
    log(f"Nebius /v1/models: ok={chk.ok} missing={chk.missing} notes={chk.notes} error={chk.error!r}")
    log(f"NVIDIA models visible: {', '.join(chk.nvidia_available) or 'none'}")
    client = llm.NebiusLLM()
    for tier in ("fast", "main", "deep"):
        t0 = time.time()
        r = client.complete_json(tier, "Return JSON {\"ok\": true, \"model_tier\": str}.", f"tier={tier}", lambda: {"ok": False})
        log(f"{tier} {r.model}: mode={r.mode} {int((time.time() - t0) * 1000)} ms usage={r.usage} data={json.dumps(r.data)[:80]} {r.warning}")
else:
    log("Nebius: skipped (no NEBIUS_API_KEY)")

if s.search_live:
    t0 = time.time()
    res = search.TavilySearch().search(["freelance iOS developer hourly rate 2026"], max_results=3)
    log(f"Tavily: mode={res.mode} {int((time.time() - t0) * 1000)} ms sources={len(res.sources)} credits={res.credits} {res.warning}")
else:
    log("Tavily: skipped (no TAVILY_API_KEY)")

if s.payments_live:
    pp = payments.PayPalInvoicing()
    try:
        pp._token()
        log(f"PayPal {s.paypal_env}: OAuth token ok")
        if args.paypal_draft:
            inv = Invoice(id="pending", number="AUTO", milestone="Smoke test draft (not sent)", amount=1.0, currency=s.currency,
                          due_date=(date.today() + timedelta(days=7)).isoformat(), client_email=s.paypal_invoicer_email or "buyer@example.com")
            d = pp.create_draft(inv, "Scope2Cash smoke test", "Smoke test draft, never sent.")
            log(f"PayPal draft created: {d.id} number={d.number} status={d.status} warnings={pp.warnings}")
    except AdapterError as e:
        log(f"PayPal error: {e}")
else:
    log("PayPal: skipped (no PAYPAL_CLIENT_ID/SECRET)")

out = ROOT / "eval" / "live_observations.md"
out.write_text("\n".join(lines) + "\n")
print(f"wrote {out}")
