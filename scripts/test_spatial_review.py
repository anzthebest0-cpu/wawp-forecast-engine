"""Offline Chromium interaction and image-load smoke tests."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
root=Path("titan-spatial-review").resolve()
entries=json.loads((root/"catalog.json").read_text())["entries"]
assert len(entries)==216 and len({e["product"] for e in entries})==25
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=["--no-sandbox"])
    page=browser.new_page(viewport={"width":1440,"height":900})
    errors=[]
    page.on("pageerror",lambda err:errors.append(str(err)))
    page.goto((root/"index.html").as_uri())
    page.locator("#panels img").wait_for()
    page.wait_for_function("document.querySelector('#panels img').naturalWidth > 0")
    for e in entries:
        page.select_option("#product",e["product"])
        page.select_option("#domain",e["domain"])
        page.select_option("#lead",str(e["lead"]))
        page.wait_for_function("document.querySelector('#panels img')?.naturalWidth > 0")
        assert page.locator("#panels img").get_attribute("src")==e["image"]
    page.select_option("#product","TQV")
    page.select_option("#domain","kolaka")
    page.select_option("#lead","6")
    page.select_option("#other","mucape")
    assert page.locator("#panels img").count()==2
    page.wait_for_function("[...document.querySelectorAll('#panels img')].every(i=>i.naturalWidth>0)")
    page.locator("#panels img").first.click()
    assert page.locator("#zoom").evaluate("(d)=>d.open")
    page.locator("#close").click()
    page.locator("#next").click()
    assert page.locator("#lead").input_value()=="12"
    assert page.locator("#next").is_disabled()
    page.select_option("#other","")
    page.select_option("#product","rain")
    page.select_option("#lead","0")
    assert page.locator(".empty").count()==1
    page.select_option("#lead","6")
    page.set_viewport_size({"width":390,"height":844})
    page.select_option("#product","TQV")
    page.wait_for_function("document.querySelector('#panels img')?.naturalWidth > 0")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert not errors,errors
    page.screenshot(path=str(root/"mobile-preview.png"),full_page=True)
    page.set_viewport_size({"width":1440,"height":900})
    page.select_option("#product","wind925")
    page.select_option("#other","rain")
    page.wait_for_function("[...document.querySelectorAll('#panels img')].every(i=>i.naturalWidth>0)")
    page.screenshot(path=str(root/"desktop-preview.png"),full_page=True)
    browser.close()
print("BROWSER_PASS: 216 images, 25 products, zoom, compare, navigation, missing combinations and mobile width")
