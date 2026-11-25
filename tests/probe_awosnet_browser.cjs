'use strict';
// One-shot, read-only browser diagnostic. No observation payloads logged or saved.
const {chromium}=require('playwright');
const origin='https://anzthebest0-cpu.github.io';
const endpoints=['https://wawp.awosnet.com/awiAwosNet.php','http://wawp.awosnet.com/awiAwosNet.php'];
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
   const ctx=await browser.newContext();
   const page=await ctx.newPage();
   // Serve a minimal document at the intended Pages origin, preserving real
   // Chromium origin/security enforcement, without loading production content.
   const documentURL=origin+'/wawp-forecast-engine/__awos_access_diagnostic';
   await page.route(documentURL,r=>r.fulfill({contentType:'text/html',body:'<!doctype html><title>AWOS access diagnostic</title>'}));
   await page.goto(documentURL);
   let findings=[];
   for(const endpoint of endpoints){
     const url=endpoint+'?t='+Date.now();
     let raw;
     try{
       const r=await ctx.request.get(url,{headers:{Origin:origin},timeout:6500,maxRedirects:0});
       const h=r.headers();
       raw={status:r.status(),cors:h['access-control-allow-origin']||null,content_type:h['content-type']||null};
       await r.dispose();
     }catch(e){raw={error:/ECONNREFUSED/.test(e.message)?'connection_refused':/ENOTFOUND|EAI_AGAIN/.test(e.message)?'dns_failure':/timeout/i.test(e.message)?'timeout':'transport_failure'};}
     let consoleErrors=[],failed=[];
     const onConsole=m=>{if(m.type()==='error'||m.type()==='warning'){const t=m.text();if(/mixed content/i.test(t))consoleErrors.push('mixed_content');if(/CORS|cross.origin/i.test(t))consoleErrors.push('cors');}};
     const onFailed=r=>{if(r.url().startsWith(endpoint))failed.push(r.failure()?.errorText||'request_failed');};
     page.on('console',onConsole);page.on('requestfailed',onFailed);
     const direct=await page.evaluate(async url=>{
       try{const r=await fetch(url,{credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(6500)});const status=r.status;await r.body?.cancel();return {readable:r.ok,status};}
       catch(_){return {readable:false,error:'fetch_failed'};}
     },url);
     page.off('console',onConsole);page.off('requestfailed',onFailed);
     findings.push({protocol:new URL(endpoint).protocol,transport:raw,browser:direct,security_messages:[...new Set(consoleErrors)],request_failures:[...new Set(failed)]});
   }
   console.log(JSON.stringify({checked_at_utc:new Date().toISOString(),origin:await page.evaluate(()=>location.origin),secure_context:await page.evaluate(()=>isSecureContext),findings,https_direct_feasible:findings[0].browser.readable===true},null,2));
   await ctx.close();
 }finally{await browser.close();}
})().catch(e=>{console.error('Diagnostic could not execute:',e.name);process.exitCode=1;});
