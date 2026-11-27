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
    if(await img.evaluate(x=>x.complete&&x.naturalWidth>0)){
      // Complete does not guarantee decoded pixels are painted.
      const painted=await img.evaluate(async x=>{
        const ready=await Promise.race([x.decode().then(()=>true,()=>false),new Promise(r=>setTimeout(()=>r(false),2000))]);
        if(!ready)return false;
        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
        return x.isConnected&&x.naturalWidth>0&&x.getBoundingClientRect().width>0;
      });
      if(painted)return;
    }
    await new Promise(r=>setTimeout(r,200));
  }
  throw Error("Spatial panel failed to load within 20 seconds");
}
(async()=>{
 const b=await chromium.launch({headless:true});
 try{
  fs.mkdirSync("titan-review-screenshots",{recursive:true});
  for(const [width,height] of [[360,640],[390,844],[768,1024],[1440,900],[844,390]]){
   const p=await b.newPage({viewport:{width,height}});
   p.setDefaultTimeout(25000);
   // Exercise actual local dashboard JS, real pinned ApexCharts, and all map
   // assets without depending on third-party CDN/font/observation availability.
   await p.route("**/*",async route=>{
     const url=new URL(route.request().url());
     if(url.origin==="http://127.0.0.1:8765")return route.continue();
     if(url.href==="https://cdn.jsdelivr.net/npm/apexcharts@3.54.1/dist/apexcharts.min.js")
       return route.fulfill({path:require.resolve("apexcharts/dist/apexcharts.min.js"),contentType:"application/javascript"});
     return route.abort("blockedbyclient");
   });
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
   assert(!(await p.locator(".preview-controls").isVisible()),"Point controls must not appear in spatial workspace");
   assert(await p.locator("#preview-safety-banner").isVisible());
   const layout=await p.evaluate(()=>{const f=document.getElementById("spatial-viewer").getBoundingClientRect(),c=document.querySelector(".content");return {frameHeight:f.height,frameBottom:f.bottom,viewport:innerHeight,overflow:document.documentElement.scrollWidth-innerWidth,outerScroll:c.scrollHeight-c.clientHeight}});
   assert(layout.frameHeight>=height*.30 && layout.frameBottom<=height+3 && layout.overflow<=1 && layout.outerScroll<=3,JSON.stringify(layout));
   console.log("SPATIAL_LAYOUT",width,height,JSON.stringify(layout));
   const frame=p.frameLocator("#spatial-viewer");
   await frame.locator("#product option").first().waitFor({state:"attached"});
   assert.equal(await frame.locator("#product option").count(),25);
   await frame.locator("#product").selectOption("wind850");
   await frame.locator("#map-heading").waitFor();
   assert(await frame.locator("html").evaluate(x=>x.scrollWidth<=x.clientWidth+1),"Embedded horizontal overflow");
   await p.waitForFunction(()=>document.getElementById("spatial-fullscreen").href.includes("product=wind850"));
   if(height>480){
     await p.locator("#theme-toggle").click();
     const theme=await p.evaluate(()=>document.documentElement.classList.contains("light-mode")?"light":"dark");
     await frame.locator("html[data-theme='"+theme+"']").waitFor({state:"attached"});
     await p.locator("#theme-toggle").click();
     await frame.locator("html[data-theme='"+(theme==="light"?"dark":"light")+"']").waitFor({state:"attached"});
   }
   await checkImage(frame);
   console.log("EMBEDDED_IMAGE_PASS",width);
   await frame.locator("#focus").click();
   assert.equal(await frame.locator("#panels article").count(),1);
   await checkImage(frame);
   await frame.locator("#panels .map").click();
   assert(await frame.locator("#zoom").evaluate(x=>x.open));
   await frame.locator("#plus").click();
   assert.equal(await frame.locator("#zoom-level").textContent(),"150%");
   await frame.locator("#close").click();
   await frame.locator("#compare").click();
   await checkImage(frame);
   await p.screenshot({animations:"disabled",path:"titan-review-screenshots/spatial-layout-"+width+"x"+height+".png"});
   await p.locator('.nav-links li[data-tab="tab-intel"]').click();
   assert(!(await p.locator("#tab-spatial").isVisible()));
   assert(!(await p.locator(".preview-controls").isVisible()),"Point controls hidden outside airport guidance");
   await p.locator('#tab-intel [data-open-tab="tab-spatial"]').click();
   assert.equal(await frame.locator("#product").inputValue(),"wind850");
   assert.equal(new URL(p.url()).hash,"#tab-spatial");
   await p.reload({waitUntil:"domcontentloaded",timeout:30000});
   await p.waitForSelector("#tab-spatial.active");
   await checkImage(p.frameLocator("#spatial-viewer"));
   assert(await p.locator("#preview-safety-banner").isVisible());
   await p.screenshot({animations:"disabled",path:"titan-review-screenshots/dashboard-spatial-"+width+".png"});
   console.log("EMBEDDED_NAVIGATION_PASS",width);
   await p.close();
  }
 }finally{await b.close()}
 console.log("TITAN_MAIN_DASHBOARD_SPATIAL_PASS 360 390 768 1440 844-landscape")
})().catch(e=>{console.error(e);process.exit(1)});
