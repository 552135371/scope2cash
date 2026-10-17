"""AG Grid dashboard check + screenshots in a headless browser: cash-flow chart (AG Charts Community) linked to the
grid, client scoreboard, inline agent nudges with Approve, saved views, CSV export, narrow layout.
Usage: S2C_URL=http://localhost:8771/ python scripts/screenshots_aggrid.py [out_dir]   (mock mode is fine)
S2C_SECRETS_CHECK=1 makes it fail if the page DOM contains any value of the secret env vars listed in SECRET_ENV."""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screens/aggrid")
OUT.mkdir(parents=True, exist_ok=True)
URL = os.environ.get("S2C_URL", "http://localhost:8771/")
SECRET_ENV = ("DEEPSEEK_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX_BUSINESS_EMAIL", "PAYPAL_INVOICER_EMAIL",
              "TAVILY_API_KEY", "NEBIUS_API_KEY", "PAYPAL_WEBHOOK_ID")
SAMPLE = os.environ.get("S2C_SHOT_PREFIX", "")


def no_secrets(pg, where):
    html = pg.content()
    leaks = [k for k in SECRET_ENV if len(os.environ.get(k, "")) >= 6 and os.environ[k] in html]
    assert not leaks, f"secret value of {leaks} found in DOM at {where}"
    return True


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2, accept_downloads=True)
    pg = ctx.new_page()
    pg.set_default_timeout(int(os.environ.get("S2C_TIMEOUT_MS", "30000")))
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errors.append("console: " + m.text))
    pg.goto(URL); pg.wait_for_selector(".badge")
    pg.add_style_tag(content=".topbar{position:static}")
    if os.environ.get("S2C_LIVE_PRELUDE") == "1":  # real PayPal sandbox rows next to the samples (no LLM call: instant project)
        pg.click("#instant"); pg.wait_for_selector("#go-inv")
        pg.fill("#email", os.environ.get("S2C_CLIENT_EMAIL", "sb-demo-client@personal.example.com"))
        pg.click("#go-inv"); pg.wait_for_selector("#s5 [data-a=send]"); pg.wait_for_timeout(500)
        pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_selector("#s5 [data-a=cardpay]"); pg.wait_for_timeout(500)
        pg.locator("#s5 [data-a=cardpay]").first.click(); pg.wait_for_selector("#s5 .pill.PAID"); pg.wait_for_timeout(500)
        pg.locator("#s5 [data-a=send]").first.click(); pg.wait_for_timeout(2500)
    if os.environ.get("S2C_SEED", "1") == "1":
        pg.click("#seed")
    pg.wait_for_selector("#grid .ag-row"); pg.wait_for_selector("#board .ag-row"); pg.wait_for_selector("#cashchart canvas")
    pg.wait_for_timeout(1200)
    engines = pg.inner_text("#grid-engine") + " | " + pg.inner_text("#chart-engine")
    pg.locator("#pipe").scroll_into_view_if_needed()
    no_secrets(pg, "load")
    pg.wait_for_selector("#toast.hidden", state="attached", timeout=8000)
    pg.locator("#pipe").screenshot(path=OUT / f"{SAMPLE}30_dashboard_chart_scoreboard_grid.png")

    # 1) chart -> grid: click the tallest overdue bar segment via the chart's own API-free route: find a week with overdue ids
    cash = pg.evaluate("P.cash")
    week = next(w for w in sorted(cash["weeks"], key=lambda w: -w["overdue"]) if w["overdue"] > 0)
    # simulate the seriesNodeClick listener with the same datum the chart passes (canvas hit-testing is pixel-fragile)
    pg.evaluate("""(w) => { const ids = w.ids.overdue; setWeek({week: w.week, series: 'overdue', ids: new Set(ids), text: 'Overdue · week of ' + w.week}); }""", week)
    pg.wait_for_timeout(500)
    shown = pg.evaluate("visible().map(i => i.id)")
    assert sorted(shown) == sorted(week["ids"]["overdue"]), (shown, week)
    assert "week of" in pg.inner_text("#active-filters")
    pg.click("#active-filters [data-clear=week]"); pg.wait_for_timeout(300)
    # and a real mouse click on the canvas: sweep along a line just above the x axis until a bar is hit
    box = pg.locator("#cashchart canvas").first.bounding_box()
    hit = None
    for fy in (0.70, 0.67, 0.64, 0.60, 0.56):
        for k in range(4, 100, 2):
            pg.mouse.click(box["x"] + box["width"] * k / 100, box["y"] + box["height"] * fy)
            pg.wait_for_timeout(100)
            hit = pg.evaluate("P.week && {week: P.week.week, series: P.week.series, ids: [...P.week.ids]}")
            if hit and hit["series"] == "overdue":
                break
            if hit:  # another series: clear so the next click on the same bar does not just toggle it off
                pg.evaluate("setWeek(null)"); hit = None
        if hit:
            break
    assert hit, "no bar hit by a real click"
    pg.wait_for_timeout(400)
    assert sorted(pg.evaluate("visible().map(i => i.id)")) == sorted(hit["ids"]), hit
    pg.wait_for_selector("#toast.hidden", state="attached", timeout=8000)
    pg.locator("#pipe").screenshot(path=OUT / f"{SAMPLE}31a_chart_click_filters_grid.png")
    # 2) scoreboard -> grid
    pg.click("#active-filters [data-clear=week]"); pg.wait_for_timeout(300)
    assert not pg.evaluate("P.week")
    pg.locator("#board .ag-row", has_text="Harbor Yoga").first.click(); pg.wait_for_timeout(600)
    rows = pg.evaluate("visible().map(i => i.client_name)")
    assert rows and set(rows) == {"Harbor Yoga"}, rows
    cash_h = pg.evaluate("P.cash.totals")
    assert cash_h["collected"] == 0 and cash_h["overdue"] > 0, cash_h  # chart follows the grid filter
    pg.locator("#pipe").screenshot(path=OUT / f"{SAMPLE}31_client_filter_linked_chart.png")
    pg.click("#active-filters [data-clear=client]"); pg.wait_for_timeout(300)

    # 3) agent proposes nudges -> they appear inline in the grid -> approve one from the grid
    pg.fill("#ask", "Nudge everyone more than 3 days late"); pg.click("#ask-go")
    pg.wait_for_selector("#grid .nudge [data-pa=approve]", timeout=int(os.environ.get("S2C_AGENT_TIMEOUT_MS", "90000")))
    pg.wait_for_timeout(600)
    n_inline = pg.locator("#grid .nudge").count()
    no_secrets(pg, "agent")
    pg.wait_for_selector("#toast.hidden", state="attached", timeout=8000)
    pg.locator("#pipe").screenshot(path=OUT / f"{SAMPLE}32_agent_nudges_inline_approve.png")
    before = pg.evaluate("fetch('/api/agent/actions?status=executed').then(r => r.json()).then(x => x.length)")
    pg.locator("#grid .nudge [data-pa=approve]").first.click()
    pg.wait_for_function(f"fetch('/api/agent/actions?status=executed').then(r => r.json()).then(x => x.length > {before})")
    pg.wait_for_timeout(800)
    left = pg.locator("#grid .nudge").count()
    assert left == n_inline - 1, (n_inline, left)

    # 4) saved view + CSV export
    pg.select_option("#views", label="Chase list · latest first"); pg.wait_for_timeout(400)
    late = pg.evaluate("visible().map(i => lateDays(i))")
    assert late == sorted(late, reverse=True) and all(d > 0 for d in late), late
    pg.click("#view-save"); pg.fill("#view-name", "Monday chase"); pg.click("#view-form button"); pg.wait_for_timeout(200)
    pg.select_option("#views", label="All invoices"); pg.wait_for_timeout(200)
    pg.select_option("#views", label="Monday chase"); pg.wait_for_timeout(300)
    assert pg.evaluate("P.filter") == "overdue"
    with pg.expect_download() as dl:
        pg.click("#csv")
    csv_path = OUT.parent / "_last_export.csv"
    dl.value.save_as(csv_path)
    csv = csv_path.read_text()
    lines = [x for x in csv.splitlines() if x.strip()]
    assert lines[0].lstrip('\ufeff').startswith('"Client"') and len(lines) - 1 == len(late), lines
    csv_path.unlink()

    # 5) narrow layout
    pg.set_viewport_size({"width": 768, "height": 1400}); pg.wait_for_timeout(900)
    pg.wait_for_selector("#toast.hidden", state="attached", timeout=8000)
    hidden = pg.evaluate("!P.grid.getColumn('last_event').isVisible() && !P.grid.getColumn('client_name').isPinned()")
    assert hidden
    pg.locator("#pipe").screenshot(path=OUT / f"{SAMPLE}33_narrow_layout.png")
    no_secrets(pg, "end")
    print("chart click hit:", hit["series"], hit["week"], len(hit["ids"]), "rows")
    print("engines:", engines)
    print("inline nudges:", n_inline, "-> after approve:", left)
    print("csv rows:", len(lines) - 1, "chase list days late:", late)
    print("agent trace:", [t for t in pg.locator("#trace li").all_inner_texts() if "Collections agent" in t])
    print("errors:", errors)
    b.close()
