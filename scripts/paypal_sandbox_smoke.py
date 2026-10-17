"""One-command go-live check against the REAL PayPal sandbox (never live money).

    export PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... PAYPAL_MODE=sandbox      # PAYPAL_ENV works too
    export PAYPAL_SANDBOX_BUSINESS_EMAIL=sb-xxxx@business.example.com             # sandbox business account
                                                     # (PAYPAL_INVOICER_EMAIL also works)
    export PAYPAL_BUYER_EMAIL=sb-yyyy@personal.example.com                        # sandbox personal (buyer) account
    python scripts/paypal_sandbox_smoke.py                    # token -> draft -> send -> status -> webhook check
    python scripts/paypal_sandbox_smoke.py --app-url https://scope2cash.onrender.com --require-webhook
    python scripts/paypal_sandbox_smoke.py --wait-paid 600    # then pay the printed link as the sandbox buyer

Steps (each prints PASS / FAIL / WARN / SKIP):
  1. config     credentials present, mode is sandbox (live is refused), buyer address looks like a sandbox account
  2. oauth      POST /v1/oauth2/token
  3. draft      POST /v2/invoicing/invoices (one $1.00 invoice, through the same adapter the app uses)
  4. send       POST /v2/invoicing/invoices/{id}/send             (skip with --no-send)
  5. status     GET  /v2/invoicing/invoices/{id}                  (status + payer link for the video)
  6. webhook    GET  /v1/notifications/webhooks: a listener for /api/paypal/webhook with INVOICING.INVOICE.PAID
  7. app        GET  {app-url}/api/health: payments and webhooks report "live"           (only with --app-url)
  8. paid       poll the invoice until PAID                                               (only with --wait-paid)
Exit codes: 0 all required steps passed, 1 a step failed, 2 missing/unsafe configuration (nothing was called).
Secrets are never printed or written. A Markdown report goes to eval/sandbox_smoke.md.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.adapters import http, payments  # noqa: E402
from app.adapters.http import AdapterError  # noqa: E402
from app.models import Invoice  # noqa: E402

WEBHOOK_PATH = "/api/paypal/webhook"
NEEDED_EVENTS = ("INVOICING.INVOICE.PAID",)
NICE_EVENTS = ("INVOICING.INVOICE.CANCELLED", "INVOICING.INVOICE.REFUNDED", "INVOICING.INVOICE.UPDATED")
SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
SANDBOX_DOMAINS = ("example.com",)  # PayPal sandbox accounts look like sb-xxxx@personal.example.com


class Report:
    def __init__(self, out: Callable[[str], None]):
        self.out, self.rows, self.failed = out, [], False

    def add(self, step: str, state: str, detail: str) -> None:
        self.rows.append((step, state, detail))
        if state == "FAIL":
            self.failed = True
        self.out(f"[{state:4}] {step:8} {detail}")

    def markdown(self, header: List[str]) -> str:
        lines = header + ["", "| step | result | detail |", "|---|---|---|"]
        lines += [f"| {s} | {st} | {d.replace('|', '/')} |" for s, st, d in self.rows]
        return "\n".join(lines) + "\n"


def requested_mode() -> str:
    """PAYPAL_MODE (mock | sandbox | live) wins over the older PAYPAL_ENV (sandbox | live); default sandbox."""
    return (os.getenv("PAYPAL_MODE") or os.getenv("PAYPAL_ENV") or "sandbox").strip().lower()


def invoicer_email(s: config.Settings) -> str:
    return s.paypal_invoicer_email or getattr(s, "paypal_sandbox_business_email", "") or os.getenv("PAYPAL_SANDBOX_BUSINESS_EMAIL", "")


def _sandbox_address(email: str) -> bool:
    dom = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    return any(dom == d or dom.endswith("." + d) for d in SANDBOX_DOMAINS)


def check_config(args, s: config.Settings) -> List[str]:
    """Problems that stop the run before any network call."""
    problems = []
    missing = [n for n, v in (("PAYPAL_CLIENT_ID", s.paypal_client_id), ("PAYPAL_CLIENT_SECRET", s.paypal_client_secret)) if not v]
    if missing:
        problems.append("missing " + ", ".join(missing) + " (developer.paypal.com > Apps & Credentials > Sandbox app)")
    mode = requested_mode()
    if mode != "sandbox" or s.paypal_base != SANDBOX_BASE:
        problems.append(f"PAYPAL_MODE/PAYPAL_ENV is {mode!r}; this script only runs against the sandbox ({SANDBOX_BASE})")
    if s.force_mock:
        problems.append("SCOPE2CASH_FORCE_MOCK=1 is set; unset it for a live sandbox check")
    if not args.buyer:
        problems.append("missing PAYPAL_BUYER_EMAIL (or --buyer): the sandbox *personal* account that receives the invoice")
    elif not _sandbox_address(args.buyer) and not args.allow_any_recipient:
        problems.append(f"buyer {args.buyer!r} is not a sandbox address (*@*.example.com); pass --allow-any-recipient to override")
    return problems


def _webhook_check(rep: Report, pp: payments.PayPalInvoicing, s: config.Settings, app_url: str, required: bool) -> None:
    try:
        hooks = pp.list_webhooks()
    except AdapterError as e:
        rep.add("webhook", "FAIL" if required else "WARN", f"could not list webhooks: {e.message[:160]}")
        return
    want_url = f"{app_url}{WEBHOOK_PATH}" if app_url else ""
    hook: Optional[Dict] = None
    if s.paypal_webhook_id:
        hook = next((h for h in hooks if h.get("id") == s.paypal_webhook_id), None)
        if hook is None:
            rep.add("webhook", "FAIL" if required else "WARN",
                    f"PAYPAL_WEBHOOK_ID is set but not among this app's {len(hooks)} webhook(s); copy the ID again")
            return
    elif want_url:
        hook = next((h for h in hooks if (h.get("url") or "").rstrip("/") == want_url), None)
    else:
        hook = next((h for h in hooks if (h.get("url") or "").rstrip("/").endswith(WEBHOOK_PATH)), None)
    if hook is None:
        urls = ", ".join(h.get("url", "?") for h in hooks) or "none"
        rep.add("webhook", "FAIL" if required else "WARN",
                f"no listener for {want_url or '*' + WEBHOOK_PATH} (subscribed: {urls}); deploy, then add it in the dashboard")
        return
    names = {(e.get("name") or "") for e in hook.get("event_types") or []}
    missing = [e for e in NEEDED_EVENTS if e not in names and "*" not in names]
    nice = [e for e in NICE_EVENTS if e not in names and "*" not in names]
    if want_url and (hook.get("url") or "").rstrip("/") != want_url:
        rep.add("webhook", "WARN", f"webhook {hook.get('id')} points at {hook.get('url')}, expected {want_url}")
    if missing:
        rep.add("webhook", "FAIL" if required else "WARN", f"webhook {hook.get('id')} lacks {', '.join(missing)}")
        return
    extra = f"; consider adding {', '.join(nice)}" if nice else ""
    hint = "" if s.paypal_webhook_id else f"; set PAYPAL_WEBHOOK_ID={hook.get('id')} on the server"
    rep.add("webhook", "PASS", f"{hook.get('id')} -> {hook.get('url')}{extra}{hint}")


def _app_check(rep: Report, app_url: str, required: bool) -> None:
    try:
        j = http.request("Scope2Cash app", "GET", f"{app_url}/api/health", timeout_s=60, retries=2).json()
    except (AdapterError, ValueError) as e:
        rep.add("app", "FAIL", f"{app_url}/api/health unreachable: {str(e)[:160]} (free Render instances take ~30 s to wake)")
        return
    ad = j.get("adapters") or {}
    pay, hook, llm = (ad.get(k, {}).get("mode") for k in ("payments", "webhooks", "llm"))
    detail = f"version={j.get('version')} payments={pay} webhooks={hook} llm={llm}({(ad.get('llm') or {}).get('provider_id')})"
    if pay != "live":
        rep.add("app", "FAIL", detail + ": set PAYPAL_CLIENT_ID/SECRET on the server")
    elif hook != "live":
        rep.add("app", "FAIL" if required else "WARN", detail + ": set PAYPAL_WEBHOOK_ID on the server")
    else:
        rep.add("app", "PASS", detail)


def run(argv: Optional[List[str]] = None, out: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep, report_path: Optional[Path] = None) -> int:
    ap = argparse.ArgumentParser(description="Go-live smoke test against the PayPal sandbox.")
    ap.add_argument("--buyer", default=os.getenv("PAYPAL_BUYER_EMAIL") or "", help="sandbox personal account email")
    ap.add_argument("--amount", type=float, default=1.0)
    ap.add_argument("--no-send", action="store_true", help="stop after creating the draft")
    ap.add_argument("--app-url", default=(os.getenv("PUBLIC_BASE_URL") or "").rstrip("/"), help="deployed app, e.g. https://x.onrender.com")
    ap.add_argument("--require-webhook", action="store_true", help="a missing webhook subscription is a failure, not a warning")
    ap.add_argument("--wait-paid", type=int, default=0, metavar="SECONDS", help="poll until the sandbox buyer pays")
    ap.add_argument("--allow-any-recipient", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)
    args.app_url = args.app_url.rstrip("/")

    s = config.reload_settings()
    rep = Report(out)
    started = datetime.now().astimezone()
    out(f"Scope2Cash PayPal sandbox smoke · {started:%Y-%m-%d %H:%M %Z} · base {s.paypal_base}")

    problems = check_config(args, s)
    if problems:
        for p in problems:
            rep.add("config", "FAIL", p)
        out("Nothing was sent to PayPal. Fix the items above and re-run.")
        return 2
    if not invoicer_email(s):
        rep.add("config", "WARN", "PAYPAL_SANDBOX_BUSINESS_EMAIL / PAYPAL_INVOICER_EMAIL is empty; "
                                  "invoices will show the REST app owner's default sandbox business profile")
    rep.add("config", "PASS", f"sandbox · client id set ({len(s.paypal_client_id)} chars) · buyer {args.buyer}")

    pp = payments.PayPalInvoicing()
    inv: Optional[Invoice] = None
    try:
        pp._token(force=True)
        rep.add("oauth", "PASS", "client-credentials token issued")
    except AdapterError as e:
        rep.add("oauth", "FAIL", f"{e.message[:200]} (check the ID/secret pair is from the *Sandbox* tab)")
    if not rep.failed:
        try:
            draft = Invoice(id="pending", number="AUTO", milestone="Scope2Cash go-live smoke test", amount=args.amount,
                            currency=s.currency, due_date=(date.today() + timedelta(days=7)).isoformat(),
                            client_email=args.buyer, client_name="Sandbox buyer", project="Go-live smoke test")
            inv = pp.create_draft(draft, "Scope2Cash smoke test", "Sandbox smoke test invoice, safe to pay with a sandbox buyer.",
                                  ["Go-live check"], ["Sandbox only"])
            rep.add("draft", "PASS" if inv.status == "DRAFT" else "WARN",
                    f"{inv.id} number={inv.number} status={inv.provider_status or inv.status}"
                    + (f" warnings={pp.warnings}" if pp.warnings else ""))
        except AdapterError as e:
            rep.add("draft", "FAIL", e.message[:240])
    if inv is not None and not args.no_send:
        try:
            inv = pp.send(inv)
            rep.add("send", "PASS", f"status={inv.provider_status or inv.status}")
        except AdapterError as e:
            rep.add("send", "FAIL", e.message[:240])
    elif inv is not None:
        rep.add("send", "SKIP", "--no-send")
    if inv is not None:
        try:
            inv = pp.refresh(inv)
            rep.add("status", "PASS", f"{inv.provider_status} -> app status {inv.status}; payer link: {inv.payer_link or 'n/a'}")
        except AdapterError as e:
            rep.add("status", "FAIL", e.message[:240])
    if not any(r[0] == "oauth" and r[1] == "FAIL" for r in rep.rows):
        _webhook_check(rep, pp, s, args.app_url, args.require_webhook)
    if args.app_url:
        _app_check(rep, args.app_url, args.require_webhook)
    else:
        rep.add("app", "SKIP", "no --app-url / PUBLIC_BASE_URL")
    if args.wait_paid and inv is not None and not args.no_send and not rep.failed:
        out(f"Waiting up to {args.wait_paid}s: log in as {args.buyer} and pay {inv.payer_link}")
        deadline, last = time.monotonic() + args.wait_paid, inv.provider_status
        while True:
            try:
                inv = pp.refresh(inv)
                last = inv.provider_status
            except AdapterError as e:
                last = f"refresh error: {e.message[:80]}"
            if inv.status == "PAID" or time.monotonic() >= deadline:
                break
            sleep(5)
        rep.add("paid", "PASS" if inv.status == "PAID" else "FAIL", f"last status {last}")

    passed = not rep.failed
    out("RESULT: " + ("PASS, ready to go live" if passed else "FAIL, see above"))
    if not args.no_report:
        path = report_path or ROOT / "eval" / "sandbox_smoke.md"
        path.write_text(rep.markdown([f"# PayPal sandbox smoke ({started:%Y-%m-%d %H:%M %Z})", "",
                                      f"Result: **{'PASS' if passed else 'FAIL'}** · base {s.paypal_base}"]))
        out(f"wrote {path}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(run())
