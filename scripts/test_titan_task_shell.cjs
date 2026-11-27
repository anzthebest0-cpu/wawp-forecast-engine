const assert=require('node:assert/strict');const {chromium}=require('playwright');
(async()=>{const b=await chromium.launch();try{for(const [width,height] of [[360,640],[390,844],[768,1024],[1366,768],[1440,900],[1920,1080],[844,390]]){
const p=await b.newPage({viewport:{width,height}});await p.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin==='http://127.0.0.1:8765')return r.continue();if(u.href==='https://cdn.jsdelivr.net/npm/apexcharts@3.54.1/dist/apexcharts.min.js')return r.fulfill({path:require.resolve('apexcharts/dist/apexcharts.min.js'),contentType:'application/javascript'});return r.abort();});
await p.goto('http://127.0.0.1:8765/',{waitUntil:'domcontentloaded'});await p.waitForSelector('[data-workflow-step="observe"]');
assert.equal(await p.locator('.nav-links li[data-tab]:visible').count(),6);assert(await p.locator('#aero-metar').isVisible());assert(!(await p.locator('.preview-controls').isVisible()));
await p.locator('[data-tab="tab-observations"]').click();assert(await p.locator('#obs-status').isVisible());assert.equal(await p.locator('#obs-status').count(),1);
await p.locator('[data-tab="tab-meteogram"]').click();assert(await p.locator('.preview-controls').isVisible());for(const id of ['tab-spread','tab-matrix','tab-models','tab-meteogram']){await p.locator('[data-workspace-view="'+id+'"]').click();assert(await p.locator('#'+id).isVisible());}
await p.locator('#global-handover').click();assert(await p.locator('#handover-dialog').evaluate(x=>x.open));await p.locator('#shift-evidence').fill('Local test');await p.locator('#handover-close').click();
await p.locator('[data-tab="tab-intel"]').click();assert.equal(await p.locator('#shift-evidence').inputValue(),'Local test');assert(!(await p.locator('#taf-text-display').isVisible()));
assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await p.screenshot({path:'titan-review-screenshots/briefing-'+width+'x'+height+'.png'});await p.close();
}}finally{await b.close()}console.log('TITAN_TASK_SHELL_PASS 7 VIEWPORTS')})().catch(e=>{console.error(e);process.exit(1)});
