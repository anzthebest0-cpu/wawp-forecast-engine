from pathlib import Path
from playwright.sync_api import sync_playwright
import json,subprocess,sys,time
from urllib.request import urlopen
root=Path("docs").resolve()
c=json.loads((root/"spatial-preview/catalog.json").read_text())
entries=c["entries"]
valid=next(e["valid_utc"] for e in entries if e["product"]=="rain" and e["domain"]=="kolaka" and any(x["product"]=="wind925" and x["domain"]=="kolaka" and x["valid_utc"]==e["valid_utc"] for x in entries))
missing=next(t for t in sorted({e["valid_utc"] for e in entries}) if not any(e["product"]=="CAPE_ML" and e["domain"]=="kolaka" and e["valid_utc"]==t for e in entries))
server=subprocess.Popen([sys.executable,"-m","http.server","8765","--bind","127.0.0.1","--directory",str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    for _ in range(30):
        try:urlopen("http://127.0.0.1:8765/",timeout=1).close();break
        except OSError:time.sleep(.2)
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--no-sandbox"])
        page=b.new_page(viewport={"width":1440,"height":1000})
        page.route("**/*",lambda route:route.continue_() if route.request.url.startswith("http://127.0.0.1:8765/") else route.abort())
        req=[];page.on("request",lambda r:req.append(r.url))
        page.goto("http://127.0.0.1:8765/",wait_until="domcontentloaded")
        assert page.locator("#tab-intel").evaluate("(x)=>x.classList.contains('active')")
        assert not any("spatial-preview/catalog" in x for x in req)
        page.locator('[data-tab="tab-spatial"]').click()
        page.wait_for_function("document.querySelector('#spatial-panels img')?.naturalWidth>0")
        assert page.locator("#spatial-status").inner_text().startswith("HISTORICAL RESEARCH ONLY")
        assert page.locator("#spatial-product option").count()==25
        assert page.locator("#spatial-lead option").count()==len({e["valid_utc"] for e in entries})
        page.select_option("#spatial-domain","kolaka");page.select_option("#spatial-lead",valid);page.select_option("#spatial-other","rain")
        expected=sum(e["product"] in {"wind925","rain"} and e["domain"]=="kolaka" and e["valid_utc"]==valid for e in entries)
        page.wait_for_function(f"[...document.querySelectorAll('#spatial-panels img')].length==={expected}&&[...document.querySelectorAll('#spatial-panels img')].every(x=>x.naturalWidth>0)")
        assert "Window:" in page.locator("#spatial-panels").inner_text()
        page.screenshot(path="integrated-rain.png",full_page=True)
        page.locator("#spatial-panels img").first.click();assert page.locator("#spatial-zoom").evaluate("(x)=>x.open")
        page.locator("#spatial-close").click()
        page.screenshot(path="integrated-desktop.png",full_page=True)
        page.select_option("#spatial-other","");page.select_option("#spatial-product","CAPE_ML");page.select_option("#spatial-lead",missing)
        assert "Not available at this valid time" in page.locator("#spatial-panels").inner_text()
        page.locator('[data-tab="tab-regional"]').click();assert page.locator("#tab-regional").evaluate("(x)=>x.classList.contains('active')")
        page.locator('[data-tab="tab-intel"]').click();assert page.locator("#tab-intel").evaluate("(x)=>x.classList.contains('active')")
        # Returning to the existing tab should preserve the intentionally missing selection.
        page.locator('[data-tab="tab-spatial"]').click()
        assert page.locator("#tab-spatial").evaluate("(x)=>x.classList.contains('active')")
        assert page.locator("#spatial-product").input_value()=="CAPE_ML"
        assert page.locator("#spatial-lead").input_value()==missing
        assert "Not available at this valid time" in page.locator("#spatial-panels").inner_text()
        # Recover from a missing selection without refreshing or changing meteorological data.
        page.select_option("#spatial-product","wind925")
        page.select_option("#spatial-lead",valid)
        page.wait_for_function("document.querySelector('#spatial-panels img')?.naturalWidth>0")
        print("RETURN_NAVIGATION_PASS: state preserved and valid chart restored",flush=True)
        # Test a genuine initial deep link, not a same-document hash navigation.
        mobile=b.new_page(viewport={"width":390,"height":844},is_mobile=True,has_touch=True)
        mobile.route("**/*",lambda route:route.continue_() if route.request.url.startswith("http://127.0.0.1:8765/") else route.abort())
        mobile.on("pageerror",lambda err:print("MOBILE_PAGE_ERROR:",err,flush=True))
        mobile.goto("http://127.0.0.1:8765/#tab-spatial",wait_until="domcontentloaded")
        try:
            mobile.wait_for_function("document.querySelector('#tab-spatial')?.classList.contains('active') && document.querySelector('#spatial-panels img')?.naturalWidth>0")
            assert mobile.locator("#spatial-status").inner_text().startswith("HISTORICAL RESEARCH ONLY")
            assert mobile.locator("#spatial-product").input_value()=="wind925"
            print("FRESH_MOBILE_DEEP_LINK_PASS",flush=True)
        except Exception:
            print("MOBILE_DIAGNOSTIC",mobile.url,mobile.locator("#spatial-status").inner_text(),mobile.locator("#spatial-panels").inner_text(),flush=True)
            raise
        finally:
            mobile.screenshot(path="integrated-mobile.png",full_page=True)
        b.close()
    print("FULL_INTEGRATION_PASS: existing navigation, lazy loading, 25 products, UTC/WITA valid times, comparison windows, zoom, missing-data behavior, mobile")
finally:server.terminate();server.wait()
