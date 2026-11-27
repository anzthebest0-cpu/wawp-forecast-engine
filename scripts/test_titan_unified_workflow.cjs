const assert=require('node:assert/strict');const {chromium}=require('playwright');const {diagnostics,steps}=require('../docs/forecast-workflow.js');const fs=require('node:fs');
assert.equal(new Set(diagnostics.flatMap(x=>x.products.map(p=>p.id))).size,25);assert.equal(steps.length,5);
(async()=>{const b=await chromium.launch();try{for(const width of [390,1440]){
 const p=await b.newPage({viewport:{width,height:900}});
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin==='http://127.0.0.1:8765')return r.continue();if(u.href==='https://cdn.jsdelivr.net/npm/apexcharts@3.54.1/dist/apexcharts.min.js')return r.fulfill({path:require.resolve('apexcharts/dist/apexcharts.min.js'),contentType:'application/javascript'});return r.abort();});
 await p.goto('http://127.0.0.1:8765/',{waitUntil:'domcontentloaded'});await p.waitForSelector('[data-workflow-step="diagnose"]');
 await p.locator('[data-review="observe"]').check();assert.equal(await p.locator('#workflow-count').textContent(),'1 / 5 reviewed');
 await p.locator('[data-workflow-step="diagnose"]').click();await p.locator('[data-scale="synoptic"]').click();await p.locator('[data-diagnostic="wind850"]').click();
 const frame=p.frameLocator('#spatial-viewer');await frame.locator('#product option').first().waitFor({state:'attached'});assert.equal(await frame.locator('#product').inputValue(),'wind850');assert.equal(await frame.locator('#domain').inputValue(),'regional');
 const ids=await frame.locator('#product option').evaluateAll(xs=>xs.map(x=>x.value));assert.deepEqual(new Set(ids),new Set(diagnostics.flatMap(x=>x.products.map(p=>p.id))));
 await p.locator('#spatial-analysis-link').click();await p.locator('[data-scale="local"]').click();await p.locator('[data-diagnostic="rain"]').click();await p.waitForFunction(()=>document.getElementById('spatial-fullscreen').href.includes('product=rain'));
 assert.equal(await frame.locator('#domain').inputValue(),'kolaka');assert.equal(await frame.locator('#product').inputValue(),'rain');
 await p.locator('[data-tab="tab-intel"]').click();assert.equal(await p.locator('#workflow-count').textContent(),'1 / 5 reviewed');await p.locator('.shift-notes summary').click();await p.locator('#shift-evidence').fill('Manual evidence test');
 const download=p.waitForEvent('download');await p.locator('#shift-export').click();const file=await download;assert((await file.suggestedFilename()).endsWith('.txt'));const content=fs.readFileSync(await file.path(),'utf8');assert(content.includes('Manual evidence test')&&content.includes('Automated TAF withheld'));
 await p.locator('#shift-reset').click();assert.equal(await p.locator('#workflow-count').textContent(),'0 / 5 reviewed');assert.equal(await p.locator('#shift-evidence').inputValue(),'');
 await p.locator('[data-tab="tab-health"]').click();await p.waitForFunction(()=>document.getElementById('workflow-health').textContent!=='Status not yet loaded.');assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await p.locator('[data-tab="tab-climatology"]').click();await p.locator('[data-workspace-view="tab-verify"]').click();assert((await p.locator('#prospective-readiness').textContent()).length>10);await p.close();
 }}finally{await b.close()}console.log('TITAN_UNIFIED_WORKFLOW_PASS 390 1440')})().catch(e=>{console.error(e);process.exit(1)});
