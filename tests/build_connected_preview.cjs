'use strict';
// Build an interactive, offline DEMO from the exact dashboard source.
// Deliberately excludes every real docs/data file and every AWOS response.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const doc='docs';
let html=fs.readFileSync(path.join(doc,'index.html'),'utf8');
const read=f=>fs.readFileSync(path.join(doc,f),'utf8');
const inline=s=>s.replace(/<\/script/gi,'<\\/script');
const demo=String.raw`
(()=>{
 const base='https://anzthebest0-cpu.github.io/wawp-forecast-engine/';
 const nativeFetch=window.fetch.bind(window),states={};
 const allowed=new Set(['tafor_intel.json','latest_weights.json','latest_performance.json','taf_guidance.json','climatology.json','individual_models.json','db_health.json','persistency.json','diurnal_climatology.json','system_workflow.json','operational_residuals.json','tafor_shadow.json','weight_history.jsonl']);
 const render=()=>{const el=document.getElementById('connected-source-status');if(!el)return;const entries=Object.entries(states),good=entries.filter(([,v])=>v==='OK').length,bad=entries.filter(([,v])=>v!=='OK').map(([k,v])=>k+': '+v);el.textContent='Published sources: '+good+'/'+allowed.size+' retrieved'+(bad.length?' · Unavailable: '+bad.join('; '):'');};
 window.fetch=async(input,options)=>{
  const m=/^data\/([a-z_]+\.(?:json|jsonl))(\?.*)?$/.exec(String(input));
  if(!m||!allowed.has(m[1]))throw Error('Review source not allowlisted');
  const name=m[1],target=location.protocol==='file:'?new URL('data/'+name+(m[2]||''),base).href:'data/'+name+(m[2]||'');
  try{const r=await nativeFetch(target,{...options,credentials:'omit',cache:'no-store',signal:AbortSignal.timeout(20000)});states[name]=r.ok?'OK':'HTTP '+r.status;render();if(!r.ok)throw Error('HTTP '+r.status);return r;}
  catch(e){if(!states[name]||states[name]==='OK')states[name]='fetch failed';render();throw e;}
 };
 document.addEventListener('DOMContentLoaded',render);
})();
`;
html=html.replace(/<meta http-equiv="refresh"[^>]*>/i,'');
html=html.replace(/<link[^>]*href="https:\/\/fonts\.googleapis\.com[^>]*>/gi,'');
html=html.replace(/<link rel="stylesheet" href="style\.css[^"]*">/,()=>'<style>'+read('style.css')+'</style>');
html=html.replace(/<script src="([^"]+)"><\/script>/g,(_,src)=>{
 const content=src.startsWith('https://cdn.jsdelivr.net/npm/apexcharts@')?fs.readFileSync(require.resolve('apexcharts/dist/apexcharts.min.js'),'utf8'):read(src.split('?')[0]);
 return '<script>'+inline(content)+'</script>';
});

html=html.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src https: data: blob:; font-src data:; connect-src https://anzthebest0-cpu.github.io http://127.0.0.1:8766; object-src \'none\'; base-uri \'none\'; form-action \'none\'"><script>'+inline(demo)+'</script><style>#connected-preview-banner{margin:8px 0 14px;padding:10px 12px;border:2px solid #2563eb;border-radius:6px;color:var(--text-primary);font:600 13px system-ui}#connected-source-status{font-weight:400;margin-top:6px;overflow-wrap:anywhere}</style>');
const banner='<div id="connected-preview-banner" role="note">CONNECTED DESIGN REVIEW · Published TITAN data only · Original freshness and TAF gates retained · AWOS live disabled<div id="connected-source-status" role="status">Retrieving published sources…</div></div>';
assert.ok(html.includes('<div class="preview-controls">'));html=html.replace('<div class="preview-controls">',banner+'<div class="preview-controls">');
html=html.replace('<title>TITAN A WAWP Aviation Guidance</title>','<title>TITAN — Connected design review</title>');
assert.ok(html.includes('name="titan-awos-proxy" content=""'));assert.ok(!/<script src=/.test(html));
fs.mkdirSync('dashboard-connected-preview',{recursive:true});fs.writeFileSync('dashboard-connected-preview/TITAN-connected.html',html);
fs.copyFileSync('scripts/serve_connected_preview.py','dashboard-connected-preview/serve_connected_preview.py');
fs.writeFileSync('dashboard-connected-preview/Open-connected-preview.bat','@echo off\r\ncd /d "%~dp0"\r\npython serve_connected_preview.py\r\npause\r\n');
fs.writeFileSync('dashboard-connected-preview/README.txt','Extract ZIP and open TITAN-connected.html in Chrome or Edge. Internet required. If browser source requests fail, double-click Open-connected-preview.bat (Python 3 required). Local relay reads only existing public production data; no stored observations or synthetic fallback. Original freshness gates remain enforced. External images depend on providers; AWOS live is disabled. Close local server console to stop. Design review only.\n');
