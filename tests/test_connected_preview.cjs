'use strict';
const {chromium}=require('playwright'),{pathToFileURL}=require('node:url'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{const b=await chromium.launch({headless:true});try{
 const page=await b.newPage({viewport:{width:1440,height:900}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(pathToFileURL(path.resolve('dashboard-connected-preview/TITAN-connected.html')).href+'#tab-matrix',{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>!document.getElementById('preview-safety-banner').textContent.includes('until freshness'),{},{timeout:35000});
 const source=await page.locator('#connected-source-status').textContent(),gate=await page.locator('#preview-safety-banner').textContent();
 assert.match(await page.locator('#connected-preview-banner').textContent(),/Published TITAN data only/);
 assert.match(await page.locator('#taf-text-display').textContent(),/withheld/i);
 await page.screenshot({path:'browser-audit/connected-matrix.png'});
 if(gate.startsWith('RAW MODEL PREVIEW')){
  await page.locator('.nav-links [data-tab="tab-meteogram"]').click();await page.locator('#chart-temp .apexcharts-svg').first().waitFor({timeout:15000});await page.screenshot({path:'browser-audit/connected-meteogram.png'});
 }
 console.log(JSON.stringify({source_status:source,gate,uncaught_errors:errors},null,2));assert.deepEqual(errors,[]);
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
