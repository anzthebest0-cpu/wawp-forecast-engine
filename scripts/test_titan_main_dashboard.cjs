const {chromium}=require("playwright");
const assert=require("node:assert/strict");
const fs=require("node:fs");
async function checkImage(frame){
  const img=frame.locator("#panels img").first();
  await img.waitFor({state:"visible",timeout:20000});
  await img.scrollIntoViewIfNeeded({timeout:10000});
  // Never await an unbounded decode() on an offscreen lazy-loaded image.
  // Poll the browser's actual decoded dimensions with a bounded deadline.
  const end=Date.now()+20000;
  while(Date.now()<end){
    if(await img.evaluate(x=>x.complete&&x.naturalWidth>0))return;
    await new Promise(r=>setTimeout(r,200));
  }
  throw Error("Spatial panel failed to load within 20 seconds");
}
(async()=>{
 const b=await chromium.launch({headless:true});
 try{
  fs.mkdirSync("titan-review-screenshots",{recursive:true});
  for(const width of [390,1440]){
   const p=await b.newPage({viewport:{width,height:900}});
   p.setDefaultTimeout(25000);
   p.on("pageerror",e=>console.log("DASHBOARD_PAGE_ERROR",e.message));
   p.on("requestfailed",r=>{if(r.url().startsWith("http://127.0.0.1:8765"))console.log("LOCAL_REQUEST_FAILED",r.url(),r.failure()?.errorText)});
   await p.goto("http://127.0.0.1:8765/",{waitUntil:"domcontentloaded",timeout:30000});
   const nav=p.locator('.nav-links li[data-tab="tab-spatial"]');
   await nav.waitFor({state:"visible"});
   await p.waitForSelector('.nav-links li[data-tab="tab-spatial"][role="button"]');
   assert.equal(await p.locator("#spatial-viewer").getAttribute("src"),null);
   await nav.click();
   assert(await p.locator("#tab-spatial").isVisible());
   assert.equal(await nav.getAttribute("aria-current"),"page");
   const frame=p.frameLocator("#spatial-viewer");
   await frame.locator("#product option").first().waitFor({state:"attached"});
   assert.equal(await frame.locator("#product option").count(),25);
   await frame.locator("#product").selectOption("wind850");
   await checkImage(frame);
   console.log("EMBEDDED_IMAGE_PASS",width);
   await p.locator('.nav-links li[data-tab="tab-intel"]').click();
   assert(!(await p.locator("#tab-spatial").isVisible()));
   await p.locator('[data-open-tab="tab-spatial"]').click();
   assert.equal(await frame.locator("#product").inputValue(),"wind850");
   assert.equal(new URL(p.url()).hash,"#tab-spatial");
   await p.reload({waitUntil:"domcontentloaded",timeout:30000});
   await p.waitForSelector("#tab-spatial.active");
   await checkImage(p.frameLocator("#spatial-viewer"));
   assert(await p.locator("#preview-safety-banner").isVisible());
   await p.screenshot({path:"titan-review-screenshots/dashboard-spatial-"+width+".png"});
   console.log("EMBEDDED_NAVIGATION_PASS",width);
   await p.close();
  }
 }finally{await b.close()}
 console.log("TITAN_MAIN_DASHBOARD_SPATIAL_PASS 390 1440")
})().catch(e=>{console.error(e);process.exit(1)});
