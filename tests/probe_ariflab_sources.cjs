'use strict';
// Bounded, one-off inspection. No raw reports, images, credentials or cookies saved.
const {chromium,request}=require('playwright');
const base='https://ariflab.meteopenerbangan.com/';
const origin='https://anzthebest0-cpu.github.io';
const files=['all_metar.txt?v=1','avail_TAF.txt','speci.csv','sigmet.csv','active_warnings.xlsx'];
const images=['wind_streamlines_925p0_cubic.png','RH_850p0_cubic.png','RH_700p0_cubic.png','LI_Surface_cubic.png','freezing_level_surface_cubic.png','Wind_Speed_Surface_cubic.png','SI_Surface_cubic.png','KI_Surface_cubic.png','Wind_Speed_925p0_cubic.png','CB_PRED_H1_BANDARA.png','CB_PRED_H2_BANDARA.png','CB_PRED_H3_BANDARA.png'];
const metar=/\b(?:METAR|SPECI)(?: COR)? WAWP \d{6}Z[^\r\n<]{0,400}/g,taf=/\bTAF(?: AMD| COR)? WAWP \d{6}Z[^\r\n<]{0,600}/g;
const summary=(s)=>({length:s.length,metar:[...s.matchAll(metar)].map(m=>m[0].match(/WAWP (\d{6}Z)/)?.[1]).slice(0,10),taf:[...s.matchAll(taf)].map(m=>({issue:m[0].match(/WAWP (\d{6}Z)/)?.[1],validity:m[0].match(/\b\d{4}\/\d{4}\b/)?.[0]})).slice(0,10),wawpOccurrences:(s.match(/\bWAWP\b/g)||[]).length});
(async()=>{const b=await chromium.launch({headless:true}),a=await request.newContext({timeout:12000});try{
const p=await b.newPage();const u=origin+'/wawp-forecast-engine/__ariflab_probe';
await p.route(u,r=>r.fulfill({contentType:'text/html',body:'<!doctype html><title>Source diagnostic</title>'}));await p.goto(u);
for(const file of files){
const url=new URL(file,base).href;
try{
 const q=await a.get(url,{headers:{Origin:origin,'User-Agent':'TITAN-WAWP-source-check/1.0'},maxRedirects:0});const h=q.headers();const bytes=Number(h['content-length']||0);
 let reports=null;if(q.ok()&&!file.endsWith('.xlsx')&&bytes<=2000000){const body=await q.body();if(body.length<=2000000)reports=summary(body.toString('utf8'));}
 const cross=await p.evaluate(async(url)=>{try{const r=await fetch(url,{credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(10000)});const out={readable:true,status:r.status,type:r.headers.get('content-type')};await r.body?.cancel();return out;}catch(e){return {readable:false,error:e.name};}},url);
 console.log(JSON.stringify({kind:'feed',file,http:q.status(),cors:h['access-control-allow-origin']||null,type:h['content-type'],lastModified:h['last-modified']||null,cache:h['cache-control']||null,reports,cross}));
}catch(e){console.log(JSON.stringify({kind:'feed',file,error:e.name}));}}
for(const file of images){const url=new URL(file,base).href;const result=await p.evaluate(url=>new Promise(resolve=>{const i=new Image();const timer=setTimeout(()=>resolve({loaded:false,error:'timeout'}),8000);i.onload=()=>{clearTimeout(timer);resolve({loaded:true,width:i.naturalWidth,height:i.naturalHeight});};i.onerror=()=>{clearTimeout(timer);resolve({loaded:false,error:'image error'});};i.src=url;}),url);console.log(JSON.stringify({kind:'chart',file,...result}));}
}finally{await a.dispose();await b.close();}})().catch(e=>{console.error(e.name);process.exitCode=1;});
