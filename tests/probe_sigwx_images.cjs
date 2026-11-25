'use strict';
// One-shot read-only test of the two exact user-provided image URLs.
// No query guessing, no anti-bot challenge bypass, no saved imagery.
const {chromium}=require('playwright');
const urls={medium:"https://web-aviation.bmkg.go.id/storage/photos/2/sigwx_202611251200.png?v=1795587495605",high:"https://web-aviation.bmkg.go.id/model/highsigwx/20261126_0000_PGGE05_EGRR.png?id=3615522"};
(async()=>{const b=await chromium.launch({headless:true});try{
const page=await b.newPage();const origin='https://anzthebest0-cpu.github.io/wawp-forecast-engine/__sigwx_images';
await page.route(origin,r=>r.fulfill({contentType:'text/html',body:'<!doctype html><title>Image test</title>'}));await page.goto(origin);
for(const [level,url] of Object.entries(urls)){
const status=[];const listen=r=>{if(r.url()===url){const h=r.headers();status.push({http:r.status(),content_type:h['content-type']||null,cors:h['access-control-allow-origin']||null,corp:h['cross-origin-resource-policy']||null});}};page.on('response',listen);
const image=await page.evaluate(url=>new Promise(resolve=>{const i=new Image(),finish=result=>{clearTimeout(timer);resolve(result);};const timer=setTimeout(()=>finish({loaded:false,reason:'timeout'}),10000);i.onload=()=>finish({loaded:true,width:i.naturalWidth,height:i.naturalHeight});i.onerror=()=>finish({loaded:false,reason:'image error'});i.src=url;}),url);
page.off('response',listen);console.log(JSON.stringify({level,https:true,status,image}));}
}finally{await b.close();}})().catch(e=>{console.error(e.name);process.exitCode=1;});
