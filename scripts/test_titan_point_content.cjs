const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {epoch,windowRows,numeric}=require('../docs/preview-ui.js');
const base='http://127.0.0.1:8765';
(async()=>{const browser=await chromium.launch();try{
 const page=await browser.newPage({viewport:{width:1366,height:768}});
 await page.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin===base)return r.continue();if(u.href==='https://cdn.jsdelivr.net/npm/apexcharts@3.54.1/dist/apexcharts.min.js')return r.fulfill({path:require.resolve('apexcharts/dist/apexcharts.min.js'),contentType:'application/javascript'});return r.abort();});
 const guidance=await (await page.request.get(base+'/data/taf_guidance.json')).json();
 const models=await (await page.request.get(base+'/data/individual_models.json')).json();
 const m=guidance.metadata?.preview_manifest;
 const now=Date.now(),eligible=m?.contract==='raw-model-preview-v1'&&m.status==='model_only'&&Date.parse(m.as_of_utc)<=now&&now<Date.parse(m.expires_at_utc);
 const rows=eligible?windowRows(guidance.data||[],now,24):[];
 await page.goto(base+'/?hours=24',{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>document.querySelector('#tab-meteogram')?.dataset.pointState!=='pending');
 await page.locator('[data-tab="tab-meteogram"]').click();
 if(rows.length){
  assert.equal(await page.locator('#tab-meteogram').getAttribute('data-point-state'),'available');
  await page.waitForFunction(()=>document.querySelector('#terminal-timeline .terminal-table tbody tr td'));
  const cells=await page.locator('#terminal-timeline .terminal-table tbody tr').first().locator('td').allTextContents();assert.equal(cells.length,rows.length);
  for(let i=0;i<rows.length;i++){const v=numeric(rows[i].Wind);assert(cells[i].startsWith(v===null?'—':v.toFixed(1)),'Wind value differs from gated payload');}
  await page.waitForFunction(()=>document.querySelectorAll('#awos-history-meteogram .unified-track').length===10);assert(await page.locator('#awos-history-meteogram .unified-track').first().isVisible());assert(!(await page.locator('#forecast-only-detail').getAttribute('open')));await page.locator('#forecast-only-detail > summary').click();
  if(rows.some(r=>numeric(r.Wind)!==null)){await page.waitForSelector('#chart-wind .apexcharts-canvas',{state:'visible',timeout:25000});assert(await page.locator('#chart-wind .apexcharts-series path').count()>0,'Wind chart missing actual series');}
  if(rows.some(r=>numeric(r.Rain)!==null)){await page.waitForSelector('#chart-rain .apexcharts-canvas',{state:'visible',timeout:25000});assert(await page.locator('#chart-rain .apexcharts-series').count()>0);}
  if(rows.length>1){await page.locator('#terminal-next').click();assert.equal(await page.locator('#terminal-time').inputValue(),'1');await page.waitForFunction(t=>[...document.querySelectorAll('#chart-wind .terminal-crosshair,#chart-rain .terminal-crosshair,#chart-temp .terminal-crosshair')].filter(x=>!x.hidden&&x.dataset.epoch===String(t)).length>=2,epoch(rows[1].Datetime));assert((await page.locator('#terminal-readout').textContent()).includes('Wind'));}
  await page.screenshot({path:'titan-review-screenshots/airport-meteogram-1366.png',animations:'disabled',fullPage:true});
  await page.locator('#advanced-meteogram summary').click();await page.waitForFunction(()=>document.querySelector('#chart-pressure')?.dataset.rendered==='1');
  await page.locator('[data-workspace-view="tab-matrix"]').click();assert.equal(await page.locator('#data-table tbody tr').count(),rows.length);
  if(JSON.stringify(models.Preview_Manifest)===JSON.stringify(m)&&Object.keys(models['Wind Speed']||{}).length){
   await page.locator('[data-workspace-view="tab-models"]').click();assert(await page.locator('#model-select option').count()>0);assert(await page.locator('#model-table tbody tr').count()>0);
   await page.locator('[data-workspace-view="tab-spread"]').click();await page.waitForFunction(()=>document.querySelector('#spread-wind .apexcharts-canvas')||document.getElementById('spread-wind')?.textContent.includes('No matched model values'));
   if(Object.keys(models.Rainfall||{}).length)assert(await page.locator('#spread-rain .rain-model-table tbody tr').count()>0);
   await page.waitForSelector('#trace-chart .apexcharts-canvas',{state:'visible'});assert(await page.locator('#trace-models input').count()>0);await page.locator('#trace-param').selectOption('Rainfall');await page.waitForSelector('#trace-chart .apexcharts-canvas',{state:'visible'});
   await page.waitForFunction(()=>{const p=document.querySelector('.model-trace-panel');return p&&Number(getComputedStyle(p).opacity)===1&&p.querySelector('.apexcharts-series path')&&p.getBoundingClientRect().height>0;});
   await page.locator('.model-trace-panel').screenshot({path:'titan-review-screenshots/model-disagreement-1366.png',animations:'disabled'});
  }
  console.log('TITAN_POINT_CONTENT_POPULATED_PASS',JSON.stringify({models:Object.keys(m.models).length,displayed_rows:rows.length,source_rows:m.row_count,expiry:m.expires_at_utc}));
 }else{
  for(const id of ['tab-meteogram','tab-spread','tab-matrix','tab-models']){
   await page.locator('[data-workspace-view="'+id+'"]').click();
   assert.equal(await page.locator('#'+id).getAttribute('data-point-state'),'unavailable');
   assert(await page.locator('#'+id+' .point-empty').isVisible());
   assert.equal(await page.locator('#'+id+' .chart-panel:visible').count(),0);if(id==='tab-meteogram')assert(await page.locator('.unified-meteogram-panel').isVisible());
  }
  console.log('TITAN_POINT_CONTENT_UNAVAILABLE_PASS (not a populated forecast test)');
 }
 assert((await page.locator('#preview-safety-banner').textContent()).includes('withheld'));
 await page.close();
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
