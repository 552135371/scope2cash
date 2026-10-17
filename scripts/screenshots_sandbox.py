"""End-to-end run against the REAL PayPal sandbox (plus whatever LLM/search keys are set), with screenshots.
Brief -> scope -> price -> quote -> real sandbox invoice drafts -> approve & send -> Orders v2 checkout link ->
the "client" pays with a PayPal sandbox test card -> invoice PAID -> collections agent (PayPal Agent Toolkit, live).
Sandbox only: no real money moves and invoices go to an example.com address.
Every screenshot is checked first: if any PayPal/LLM credential or the sandbox business email appears in the page,
the script stops without saving it.
Usage: S2C_URL=http://localhost:8777/ python scripts/screenshots_sandbox.py <out_dir>"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screens/real")
OUT.mkdir(parents=True, exist_ok=True)
URL = os.environ.get("S2C_URL", "http://localhost:8777/")
CLIENT = os.environ.get("S2C_CLIENT_EMAIL", "sb-demo-client@personal.example.com")
SECRETS = [v for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX_BUSINESS_EMAIL", "PAYPAL_INVOICER_EMAIL",
                       "DEEPSEEK_API_KEY", "TAVILY_API_KEY") if (v := os.environ.get(k, "").strip()) and len(v) > 6]


def shot(pg, name, locator=None, full=False):
    html = pg.content()
    leaks = [i for i, s in enumerate(SECRETS) if s in html]
    if leaks:
        raise SystemExit(f"refusing to save {name}: a credential/business email is visible in the page")
    (locator.screenshot if locator is not None else (lambda **k: pg.screenshot(full_page=full, **k)))(path=OUT / name)
    print("saved", name)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    pg.set_default_timeout(int(os.environ.get("S2C_TIMEOUT_MS", "180000")))
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.wait_for_selector(".badge")
    pg.add_style_tag(content=".topbar{position:static}")
    shot(pg, "20_header_badges_sandbox.png", pg.locator(".topbar"))
    pg.click("#sample"); pg.click("#go-scope"); pg.wait_for_selector("#go-price")
    pg.click("#go-price"); pg.wait_for_selector("#go-quote")
    pg.click("#go-quote"); pg.wait_for_selector("#go-inv")
    pg.fill("#email", CLIENT)
    pg.click("#go-inv"); pg.wait_for_selector("#s5 [data-a=send]"); pg.wait_for_timeout(500)
    shot(pg, "21_sandbox_drafts.png", pg.locator("#s5"))
    pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_selector("#s5 [data-a=checkout]"); pg.wait_for_timeout(500)
    pg.locator("#s5 [data-a=checkout]").first.click(); pg.wait_for_selector("#s5 a:has-text('Pay with PayPal')"); pg.wait_for_timeout(500)
    shot(pg, "22_sent_with_checkout_link.png", pg.locator("#s5"))
    pg.locator("#s5 [data-a=cardpay]").first.click(); pg.wait_for_selector("#s5 .pill.PAID"); pg.wait_for_timeout(800)
    shot(pg, "23_paid_via_sandbox_checkout.png", pg.locator("#s5"))
    tr = pg.locator("#trace").locator("xpath=..")
    tr.scroll_into_view_if_needed(); pg.wait_for_timeout(300)
    shot(pg, "24_agent_trace_sandbox.png", tr)
    pg.locator("#pipe").scroll_into_view_if_needed(); pg.click("#pipe-refresh"); pg.wait_for_timeout(1500)
    pg.fill("#ask", "Who still owes me money, and is anything overdue?"); pg.click("#ask-go")
    pg.wait_for_selector("#agent-out :is(p, div, li)"); pg.wait_for_timeout(1200)
    shot(pg, "25_pipeline_collections_agent_live.png", pg.locator("#pipe"))
    shot(pg, "26_full_sandbox_run.png", full=True)
    print("errors:", errors)
    b.close()
