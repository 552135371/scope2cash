"""PayPal-track screenshots in a headless browser: AG Grid pipeline, collections agent, approval, webhook feed.
Usage: S2C_URL=http://localhost:8765/ python scripts/screenshots_paypal.py [out_dir]  (mock mode is fine)"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screens")
OUT.mkdir(parents=True, exist_ok=True)
URL = os.environ.get("S2C_URL", "http://localhost:8765/")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errors.append("console: " + m.text))
    pg.goto(URL); pg.wait_for_selector(".badge")
    pg.add_style_tag(content=".topbar{position:static}")
    # One real project first (brief -> invoices), then the sample clients.
    pg.click("#sample"); pg.click("#go-scope"); pg.wait_for_selector("#go-price")
    pg.click("#go-price"); pg.wait_for_selector("#go-quote"); pg.click("#go-quote"); pg.wait_for_selector("#go-inv")
    pg.click("#go-inv"); pg.wait_for_selector("#s5 [data-a=send]"); pg.locator("#s5 [data-a=send]").first.click()
    pg.wait_for_timeout(400)
    pg.click("#seed"); pg.wait_for_selector("#grid .ag-row, #grid table"); pg.wait_for_timeout(800)
    engine = pg.inner_text("#grid-engine")
    pg.locator("#pipe").scroll_into_view_if_needed(); pg.wait_for_timeout(300)
    pg.locator("#pipe").screenshot(path=OUT / "10_pipeline_ag_grid.png")
    pg.click("#pipe-filter [data-f=overdue]"); pg.wait_for_timeout(500)
    pg.locator("#grid").screenshot(path=OUT / "11_pipeline_overdue_filter.png")
    pg.click("#pipe-filter [data-f=all]")
    pg.fill("#ask", "Nudge everyone more than 3 days late"); pg.click("#ask-go")
    pg.wait_for_selector("#props .prop"); pg.wait_for_timeout(500)
    pg.locator(".pipe-bottom").screenshot(path=OUT / "12_collections_agent_proposals.png")
    pg.locator("#props [data-pa=approve]").first.click(); pg.wait_for_selector("#props .prop.executed"); pg.wait_for_timeout(400)
    # Simulate a client paying: a signed webhook goes through /api/paypal/webhook's pipeline.
    lumen = pg.evaluate("fetch('/api/invoices').then(r => r.json()).then(xs => xs.find(i => i.number === 'S2C-SAMPLE-05').id)")
    pg.locator(f'#grid [data-a=pay][data-id="{lumen}"]').click(); pg.wait_for_timeout(900)
    pg.locator("#pipe").screenshot(path=OUT / "13_approved_and_webhook_paid.png")
    pg.screenshot(path=OUT / "14_full_paypal.png", full_page=True)
    print("grid engine:", engine)
    print("errors:", errors)
    b.close()
