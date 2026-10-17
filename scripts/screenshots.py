"""Drive the full demo flow in a headless browser and save screenshots. Usage: python scripts/screenshots.py [out_dir]"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screens")
OUT.mkdir(parents=True, exist_ok=True)
import os
URL = os.environ.get("S2C_URL", "http://localhost:8765/")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    pg.set_default_timeout(int(os.environ.get("S2C_TIMEOUT_MS", "30000")))  # live LLM steps can take 30-90 s
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.wait_for_selector(".badge")
    pg.add_style_tag(content=".topbar{position:static}")  # keep the sticky header out of element screenshots
    pg.click("#sample"); pg.screenshot(path=OUT / "01_brief.png")
    pg.click("#go-scope"); pg.wait_for_selector("#go-price"); pg.wait_for_timeout(500)
    pg.locator("#s2").screenshot(path=OUT / "02_scope.png")
    pg.click("#go-price"); pg.wait_for_selector("#go-quote"); pg.wait_for_timeout(500)
    pg.locator("#s3").screenshot(path=OUT / "03_pricing.png")
    pg.click("#go-quote"); pg.wait_for_selector("#go-inv"); pg.wait_for_timeout(500)
    pg.locator("#s4").screenshot(path=OUT / "04_quote.png")
    tr = pg.locator("#trace").locator("xpath=..")
    tr.scroll_into_view_if_needed(); pg.wait_for_timeout(300)
    tr.screenshot(path=OUT / "08_agent_trace.png")
    pg.click("#go-inv"); pg.wait_for_selector("#s5 [data-a=send]"); pg.wait_for_timeout(400)
    pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_selector("#s5 [data-a=pay]")
    pg.locator("#s5 [data-a=pay]").first.click(); pg.wait_for_timeout(300)
    pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_selector("#s5 [data-a=overdue]")
    pg.locator("#s5 [data-a=overdue]").first.click(); pg.wait_for_timeout(300)
    pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_timeout(400)
    # Step 5 is no longer the bottom of the page (the v0.3 pipeline sits below it), so scroll to it explicitly.
    pg.evaluate("document.querySelector('#s5').scrollIntoView({block: 'end'}); window.scrollBy(0, 24)"); pg.wait_for_timeout(600)
    pg.screenshot(path=OUT / "05_get_paid.png")
    pg.locator("#s5 [data-a=remind]").first.click(); pg.wait_for_selector("#m-copy"); pg.wait_for_timeout(400)
    pg.screenshot(path=OUT / "06_reminder.png")
    pg.click("#m-close")
    pg.screenshot(path=OUT / "00_full.png", full_page=True)
    # A thin brief: the agent lowers confidence and asks before quoting.
    pg.goto(URL); pg.wait_for_selector(".badge"); pg.add_style_tag(content=".topbar{position:static}")
    pg.wait_for_function("document.querySelectorAll('#samples option').length > 0")
    idx = pg.evaluate("[...document.querySelectorAll('#samples option')].findIndex(o => o.textContent.includes('Vague'))")
    pg.select_option("#samples", str(idx)); pg.click("#go-scope"); pg.wait_for_selector("#go-price"); pg.wait_for_timeout(500)
    pg.locator("#s2").screenshot(path=OUT / "07_vague_brief.png")
    b.close()
    print("errors:", errors)
