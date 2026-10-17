"""Render the README's mermaid diagram to docs/architecture.png (needs playwright + a local mermaid.min.js).
Usage: python scripts/render_diagram.py /path/to/mermaid.min.js"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
src = re.search(r"```mermaid\n(.*?)```", (ROOT / "README.md").read_text(), re.S).group(1)
js = Path(sys.argv[1]).read_text()
html = f"""<html><body style="margin:0;background:#fff"><div id="d" class="mermaid" style="padding:24px;display:inline-block">{src}</div>
<script>{js}</script><script>mermaid.initialize({{startOnLoad:false, theme:'base', themeVariables:{{primaryColor:'#f3f9e6',
primaryBorderColor:'#76b900', lineColor:'#3a4256', fontFamily:'Inter, Arial, sans-serif', fontSize:'15px'}}}});
mermaid.run().then(()=>document.body.dataset.done=1);</script></body></html>"""
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(device_scale_factor=2, viewport={"width": 1600, "height": 900})
    pg.set_content(html)
    pg.wait_for_selector("body[data-done]")
    out = ROOT / "docs" / "architecture.png"
    pg.locator("#d").screenshot(path=out)
    b.close()
print("wrote", out)
