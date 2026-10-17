"""Drive the full demo flow in a headless browser and save screenshots. Usage: python scripts/screenshots.py [out_dir]"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screens")
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:8765/"

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL); pg.wait_for_selector(".badge")
    pg.click("#sample"); pg.screenshot(path=OUT / "01_brief.png")
    pg.click("#go-scope"); pg.wait_for_selector("#go-price"); pg.wait_for_timeout(500)
    pg.locator("#s2").screenshot(path=OUT / "02_scope.png")
    pg.click("#go-price"); pg.wait_for_selector("#go-quote"); pg.wait_for_timeout(500)
    pg.locator("#s3").screenshot(path=OUT / "03_pricing.png")
    pg.click("#go-quote"); pg.wait_for_selector("#go-inv"); pg.wait_for_timeout(500)
    pg.locator("#s4").screenshot(path=OUT / "04_quote.png")
    pg.click("#go-inv"); pg.wait_for_selector("#s5 [data-a=send]"); pg.wait_for_timeout(400)
    pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_selector("#s5 [data-a=pay]")
    pg.locator("#s5 [data-a=pay]").first.click(); pg.wait_for_timeout(300)
    pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_selector("#s5 [data-a=overdue]")
    pg.locator("#s5 [data-a=overdue]").first.click(); pg.wait_for_timeout(300)
    pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_timeout(400)
    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)"); pg.wait_for_timeout(600)
    pg.screenshot(path=OUT / "05_get_paid.png")
    pg.locator("#s5 [data-a=remind]").first.click(); pg.wait_for_selector("#m-copy"); pg.wait_for_timeout(400)
    pg.screenshot(path=OUT / "06_reminder.png")
    pg.click("#m-close")
    pg.screenshot(path=OUT / "00_full.png", full_page=True)
    b.close()
    print("errors:", errors)
