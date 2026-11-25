'use strict';
// One explicitly requested read-only check of public provider date selectors.
// No credentials, observation archive, or downloaded chart files.
const {chromium}=require('playwright');
const origin='https://web-aviation.bmkg.go.id';
const safe=u=>{try{const x=new URL(u,origin);return x.origin===origin?x.origin+x.pathname+(x.search? '?'+[...x.searchParams].map(([k,v])=>encodeURIComponent(k)+'='+(/^\d{2,14}(?:[-/]\d{2,4})*$/.test(v)?encodeURIComponent(v):'[redacted]')).join('&'):''):null;}catch{return null;}};
(async()=>{const browser=await chromium.launch({headless:true});try{
 for(const level of ['medium','high']){
  const page=await browser.newPage(),requests=[],responses=[],pending=[];
  page.on('request',r=>{const url=safe(r.url());if(url&&['xhr','fetch','image','document','script'].includes(r.resourceType())&&requests.length<80){const post=r.postData();requests.push({url,method:r.method(),type:r.resourceType(),post:post?[...new URLSearchParams(post)].map(([k,v])=>[k,/^[\d/-]+$/.test(v)?v:'[redacted]']):null});}});
  page.on('response',r=>{const url=safe(r.url());if(url&&responses.length<100){const h=r.headers();responses.push({url,status:r.status(),type:h['content-type'],cors:h['access-control-allow-origin']||null,frame:h['x-frame-options']||null,csp_frame:(h['content-security-policy']||'').split(';').find(s=>s.trim().startsWith('frame-ancestors'))||null});}});
  const url=origin+'/web/sigwx_'+level+'_level.php';
  try{
   const res=await page.goto(url,{waitUntil:'domcontentloaded',timeout:20000});
   await page.waitForTimeout(1800);
   const describe=()=>page.evaluate(()=>({title:document.title,selects:[...document.querySelectorAll('select')].map(e=>({id:e.id,name:e.name,options:[...e.options].slice(0,15).map(o=>({value:o.value,text:o.textContent.trim()}))})),images:[...document.images].map(i=>({src:i.currentSrc||i.src,alt:i.alt,width:i.naturalWidth,height:i.naturalHeight})),frames:[...document.querySelectorAll('iframe')].map(i=>i.src),scripts:[...document.scripts].filter(s=>!s.src).map(s=>s.textContent.split('\n').filter(l=>/sigwx|ajax|fetch\(|\.change\(|addEventListener|\.jpeg|\.png|\.jpg/i.test(l)).slice(0,25).map(l=>l.trim().slice(0,400))).flat().slice(0,35)}));
   const before=await describe();
   const choice=await page.evaluate(()=>{const all=[...document.querySelectorAll('select')];let e=all.find(x=>/date|tanggal/i.test(x.id+' '+x.name))||all.find(x=>[...x.options].some(o=>/\d{4}[-/]\d{2}|^\d{8}$/.test(o.value)||/\d{2}[-/]\d{2}[-/]\d{4}/.test(o.textContent)));if(!e)return null;const o=[...e.options].find(x=>x.value&&!x.disabled&&!/^(select|choose|pilih)/i.test(x.textContent.trim()));return o?{index:all.indexOf(e),value:o.value}:null;});
   if(choice){await page.locator('select').nth(choice.index).selectOption(choice.value,{timeout:5000});await page.waitForTimeout(2500);}
   const after=await describe();
   // Try the official page in an unmodified cross-origin iframe. Do not bypass frame policy.
   const framePage=await browser.newPage(),frameErrors=[];
   framePage.on('console',m=>{if(/refused to frame|frame-ancestors|x-frame-options/i.test(m.text()))frameErrors.push(m.text().slice(0,300));});
   const preview='https://anzthebest0-cpu.github.io/wawp-forecast-engine/__sigwx_probe';
   await framePage.route(preview,r=>r.fulfill({contentType:'text/html',body:'<!doctype html><iframe src="'+url+'"></iframe>'}));
   await framePage.goto(preview,{waitUntil:'domcontentloaded',timeout:15000});await framePage.waitForTimeout(3000);
   const frameResult=framePage.frames().map(f=>safe(f.url())||f.url().split(':')[0]+':');
   await framePage.close();
   console.log(JSON.stringify({level,http:res?.status(),before,choice,after,requests,responses,frameResult,frameErrors},null,2));
  }catch(e){console.log(JSON.stringify({level,error:e.name,requests,responses}));}
  finally{await page.close();}
 }
}finally{await browser.close();}})().catch(e=>{console.error(e.name);process.exitCode=1;});
