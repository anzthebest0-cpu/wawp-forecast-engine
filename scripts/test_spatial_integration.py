from pathlib import Path
from playwright.sync_api import sync_playwright
import subprocess,sys,time
from urllib.request import urlopen
root=Path("docs").resolve()
server=subprocess.Popen([sys.executable,"-m","http.server","8765","--bind","127.0.0.1","--directory",str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    for _ in range(30):
        try:
            urlopen("http://127.0.0.1:8765/",timeout=1).close();break
        except OSError:time.sleep(.2)
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--no-sandbox"])
        page=b.new_page(viewport={"width":1440,"height":1000})
        # Existing external providers are not part of this fixed-cycle smoke test.
        page.route("**/*",lambda route:route.continue_() if route.request.url.startswith("http://127.0.0.1:8765/") else route.abort())
        requests=[];page.on("request",lambda req:requests.append(req.url))
        page.goto("http://127.0.0.1:8765/",wait_until="domcontentloaded")
        assert page.locator("#tab-intel").evaluate("(x)=>x.classList.contains('active')")
        assert not any("spatial-preview/catalog" in x for x in requests)
        page.locator('[data-tab="tab-spatial"]').click()
        page.wait_for_function("document.querySelector('#spatial-panels img')?.naturalWidth>0")
        assert page.locator("#spatial-status").inner_text().startswith("HISTORICAL RESEARCH ONLY")
        assert page.locator("#spatial-product option").count()==25
        page.select_option("#spatial-domain","kolaka");page.select_option("#spatial-other","rain")
        page.wait_for_function("[...document.querySelectorAll('#spatial-panels img')].length===2&&[...document.querySelectorAll('#spatial-panels img')].every(x=>x.naturalWidth>0)")
        page.locator("#spatial-panels img").first.click()
        assert page.locator("#spatial-zoom").evaluate("(x)=>x.open")
        page.locator("#spatial-close").click()
        page.screenshot(path="integrated-desktop.png",full_page=True)
        page.locator('[data-tab="tab-regional"]').click()
        assert page.locator("#tab-regional").evaluate("(x)=>x.classList.contains('active')")
        page.locator('[data-tab="tab-intel"]').click()
        assert page.locator("#tab-intel").evaluate("(x)=>x.classList.contains('active')")
        page.goto("http://127.0.0.1:8765/#tab-spatial",wait_until="domcontentloaded")
        page.wait_for_function("document.querySelector('#spatial-panels img')?.naturalWidth>0")
        page.set_viewport_size({"width":390,"height":844})
        page.screenshot(path="integrated-mobile.png",full_page=True)
        b.close()
    print("INTEGRATION_PASS: existing nav, lazy load, historical banner, 25 products, comparison, zoom, return navigation, deep link")
finally:server.terminate();server.wait()
