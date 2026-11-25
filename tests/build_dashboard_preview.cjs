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
(() => {
 const now=Date.now(),hour=Math.floor(now/3600000)*3600000;
 const iso=t=>new Date(t).toISOString(),wita=t=>iso(t+8*3600000).slice(0,19).replace('T',' ');
 const manifest={contract:'raw-model-preview-v1',status:'model_only',as_of_utc:iso(now-60000),expires_at_utc:iso(now+3600000),models:{ECMWF_HRES:{},GFS_GLOBAL:{}},taf_status:'withheld_pending_independent_validation'};
 const rows=Array.from({length:96},(_,i)=>({Datetime:wita(hour+i*3600000),Temperature:28+3*Math.sin(i*Math.PI/12),Dewpoint:24,Wind:5+i%4,Gust:i===3?null:12,Rain:i===2?null:i%7===0?4:0,Pressure:1011+Math.sin(i*Math.PI/6),'Wind Dir.':240+i%6*10,'High Clouds':20,'Mid Clouds':40,'Low Clouds':i===4?null:60,Model_Count:2}));
 const series=(key,delta=0)=>Object.fromEntries(rows.map(r=>[r.Datetime,r[key]===null?null:r[key]+delta]));
 const models={Preview_Manifest:manifest,Temperature:{ECMWF_HRES:series('Temperature'),GFS_GLOBAL:series('Temperature',1.2)},Dewpoint:{ECMWF_HRES:series('Dewpoint'),GFS_GLOBAL:series('Dewpoint',.5)},'Wind Speed':{ECMWF_HRES:series('Wind'),GFS_GLOBAL:series('Wind',2)},Rainfall:{ECMWF_HRES:series('Rain'),GFS_GLOBAL:series('Rain',.5)},Model_Metadata:{}};
 const data={'taf_guidance.json':{metadata:{preview_manifest:manifest,latest_data_pull_utc:iso(now-60000)},data:rows},'individual_models.json':models,'latest_weights.json':{weights:{}},'latest_performance.json':{metrics:{Month:{Temperature:{overall:{ECMWF_HRES:{MAE:1,RMSE:1.2,Bias:.2}}},Rainfall:{overall:{ECMWF_HRES:{HSS:.3,CSI:.4}},significance:{fused:{dm_stat:1,p_value:.01,nw_bandwidth:4}}}}}},'tafor_intel.json':{default:{taf_text:'DEMO ONLY — NOT AN OPERATIONAL TAF'}}};
 window.fetch=async(input)=>{const m=/^data\/([^?]+)/.exec(String(input));if(m&&Object.hasOwn(data,m[1]))return new Response(JSON.stringify(data[m[1]]),{headers:{'Content-Type':'application/json'}});return new Response('Not included in offline demonstration',{status:404});};
})();
`;
html=html.replace(/<meta http-equiv="refresh"[^>]*>/i,'');
html=html.replace(/<link[^>]*href="https:\/\/fonts\.googleapis\.com[^>]*>/gi,'');
html=html.replace(/<link rel="stylesheet" href="style\.css[^"]*">/,()=>'<style>'+read('style.css')+'</style>');
html=html.replace(/<script src="([^"]+)"><\/script>/g,(_,src)=>{
 const content=src.startsWith('https://cdn.jsdelivr.net/npm/apexcharts@')?fs.readFileSync(require.resolve('apexcharts/dist/apexcharts.min.js'),'utf8'):read(src.split('?')[0]);
 return '<script>'+inline(content)+'</script>';
});
html=html.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data: blob:; font-src data:; connect-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'"><script>'+inline(demo)+'</script><style>body{padding-top:44px!important}#offline-demo-banner{position:fixed;top:0;left:0;right:0;z-index:999999;background:#7c2d12;color:white;text-align:center;padding:10px;font:600 13px system-ui;line-height:1.2}</style>');
html=html.replace('<body>','<body><div id="offline-demo-banner" role="note">OFFLINE DESIGN DEMO · SYNTHETIC DATA ONLY · NOT FOR OPERATIONAL USE</div>');
html=html.replace('<title>TITAN A WAWP Aviation Guidance</title>','<title>TITAN — Offline dashboard design demo</title>');
assert.ok(html.includes('OFFLINE DESIGN DEMO'));assert.ok(!/<script src=/.test(html));assert.ok(html.includes('name="titan-awos-proxy" content=""'));
fs.mkdirSync('dashboard-preview',{recursive:true});fs.writeFileSync('dashboard-preview/TITAN-preview.html',html);
fs.writeFileSync('dashboard-preview/README.txt','Extract the ZIP and double-click TITAN-preview.html in Chrome or Edge. No Python server or installation. This is a design preview using invented data, not a current weather forecast. External weather charts, operational archives and live AWOS are intentionally unavailable. Do not use for operational decisions. Production remains unchanged.\n');
