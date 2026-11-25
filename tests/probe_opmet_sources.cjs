'use strict';
// Explicit one-shot read-only source identification. Never save reports, credentials, bodies or cookies.
const {chromium,request}=require('playwright');
const site='https://stamet-tolitoli.bmkg.go.id/cuaca/penerbangan?icao=WAWP';
const reportType=(text,type)=>{const match=String(text||'').match(type==='metar'?/\b(?:METAR|SPECI)(?: COR)? WAWP \d{6}Z[^\r\n<]{0,400}/: /\bTAF(?: AMD| COR)? WAWP \d{6}Z[^\r\n<]{0,600}/);return match?match[0].trim():null;};
const summarize=(raw,type)=>({present:!!raw,issue:raw?.match(/\bWAWP (\d{6}Z)\b/)?.[1]||null,validity:type==='taf'?raw?.match(/\b\d{4}\/\d{4}\b/)?.[0]||null:null});
const safe=url=>{try{const u=new URL(url);return ['https:','http:'].includes(u.protocol)?u.origin+u.pathname:null;}catch{return null;}};
(async()=>{const browser=await chromium.launch({headless:true});const api=await request.newContext({timeout:15000});try{
 const p=await browser.newPage(),requests=[],responses=[];
 p.on('request',q=>{if(['fetch','xhr','document','script'].includes(q.resourceType())&&requests.length<60){const url=safe(q.url());if(url)requests.push({url,type:q.resourceType(),method:q.method()});}});
 p.on('response',q=>{if(['fetch','xhr','document'].includes(q.request().resourceType())&&responses.length<40){const url=safe(q.url());if(url)responses.push({url,status:q.status(),type:q.headers()['content-type']||null,cors:q.headers()['access-control-allow-origin']||null});}});
 const raw={metar:null,taf:null};
 try{const res=await p.goto(site,{waitUntil:'domcontentloaded',timeout:25000});await p.waitForTimeout(3500);
 const data=await p.evaluate(()=>({title:document.title,text:document.body.innerText.slice(0,30000),forms:[...document.forms].map(f=>({action:f.action,method:f.method})),scripts:[...document.scripts].map(s=>s.src).filter(Boolean),inlineUsesFetch:[...document.scripts].filter(s=>!s.src).some(s=>/\bfetch\s*\(|XMLHttpRequest|\.ajax\s*\(/.test(s.textContent))}));
 for(const type of ['metar','taf'])raw[type]=reportType(data.text,type);
 console.log(JSON.stringify({source:'Tolitoli',http:res?.status(),title:data.title,reports:{metar:summarize(raw.metar,'metar'),taf:summarize(raw.taf,'taf')},forms:data.forms.map(f=>({action:safe(f.action),method:f.method})),scripts:data.scripts.map(safe).filter(Boolean),inlineUsesFetch:data.inlineUsesFetch,requests,responses}));
 }catch(e){console.log(JSON.stringify({source:'Tolitoli',error:e.name,requests,responses}));}
 for(const type of ['metar','taf']){
  const url='https://aviationweather.gov/api/data/'+type+'?ids=WAWP&format=json';
  try{const res=await api.get(url,{headers:{'User-Agent':'TITAN-WAWP-source-check/1.0','Origin':'https://anzthebest0-cpu.github.io'},maxRedirects:0});const h=res.headers();let rows=[],parseError=false;if(res.ok()&&res.status()!==204){try{const j=await res.json();if(Array.isArray(j))rows=j;else parseError=true;}catch{parseError=true;}}
  const matched=rows.filter(x=>x.icaoId==='WAWP'||x.stationId==='WAWP'||x.icao==='WAWP');const text=matched.map(x=>reportType(x.rawOb||x.rawTAF||x.rawTaf||x.raw_text||'',type)).find(Boolean)||null;
  console.log(JSON.stringify({source:'NOAA AWC',type,http:res.status(),cors:h['access-control-allow-origin']||null,contentType:h['content-type']||null,parseError,matchingRecords:matched.length,report:summarize(text,type),sameIssueAsTolitoli:!!text&&!!raw[type]&&summarize(text,type).issue===summarize(raw[type],type).issue}));
  }catch(e){console.log(JSON.stringify({source:'NOAA AWC',type,error:e.name}));}
 }
}finally{await api.dispose();await browser.close();}})().catch(e=>{console.error(e.name);process.exitCode=1;});
