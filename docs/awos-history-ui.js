/* Rolling AWOS history: read-only, no browser persistence. */
(function(root){
'use strict';
const HOUR=3600000,WINDOWS=[3,6,12,24];
const finite=v=>typeof v==='number'&&Number.isFinite(v);
function parseHistory(data,hours,now,obs){
 if(!data||data.schema!=='titan-awos-history-v1'||data.station!=='WAWP'||data.source!=='AWOSnet'||data.retention_hours!==24||data.window_hours!==hours||data.wind_reference!=='unverified_source_bearing'||!WINDOWS.includes(hours)||!Array.isArray(data.rows)||data.rows.length>1500||!Array.isArray(data.reports)||data.reports.length>1500)throw Error('history schema');
 const generated=obs.utc(data.generated_at_utc);
 if(!finite(generated)||generated>now||now-generated>120000)throw Error('history retrieval time');
 const start=generated-hours*HOUR,rows=[],seen=new Set(),reports=[];
 for(const raw of data.rows){
  const s=obs.snapshot(raw,now);
  const retrieved=obs.utc(raw.retrieved_at_utc);
  if(s.at<start||s.at>generated||!finite(retrieved)||retrieved<s.at||retrieved>generated||seen.has(s.at))throw Error('history timestamp');
  seen.add(s.at);rows.push(s);
 }
 for(const raw of data.reports){
  const r=obs.report(raw.report_text,raw.report_date_utc,now);
  if(r.at!==obs.utc(raw.observed_at_utc)||r.at<start||r.at>generated)throw Error('report timestamp');
  reports.push(r);
 }
 return {rows:rows.sort((a,b)=>a.at-b.at),reports:reports.sort((a,b)=>b.at-a.at),generated,start,hours};
}
function direction(s){
 const v=s?.values;
 return finite(v?.wind_speed_kt)&&v.wind_speed_kt>0&&finite(v.wind_direction_deg)?v.wind_direction_deg%360:null;
}
function circularChange(from,to){return finite(from)&&finite(to)?((to-from+540)%360)-180:null;}
function tendency(rows,minutes,key,now){
 const latest=rows[rows.length-1];
 if(!latest||now-latest.at>300000)return null;
 const target=latest.at-minutes*60000;
 const old=rows.filter(s=>s.at<latest.at&&Math.abs(s.at-target)<=120000).sort((a,b)=>Math.abs(a.at-target)-Math.abs(b.at-target)||a.at-b.at)[0];
 if(!old)return null;
 const val=s=>key==='direction'?direction(s):s.values[key];
 const a=val(old),b=val(latest);
 if(!finite(a)||!finite(b))return null;
 return {difference:key==='direction'?circularChange(a,b):b-a,elapsedMinutes:(latest.at-old.at)/60000};
}
const api={parseHistory,direction,circularChange,tendency};
if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
root.TitanAwosHistory=api;
const el=id=>document.getElementById(id);
const tracks=[
 ['Temperature / dewpoint (°C)',[['temperature_c','Temperature','temp'],['dewpoint_c','Dewpoint','dew']]],
 ['Wind speed / gust (kt)',[['wind_speed_kt','2-min wind','wind'],['gust_kt','Gust','gust']]],
 ['Wind direction (°) · source bearing',[['direction','Non-calm bearings','wind']],true],
 ['Altimeter setting (hPa)',[['altimeter_setting_hpa','Altimeter','pressure']]],
 ['Relative humidity (%)',[['relative_humidity_pct','RH','dew']]],
 ['Current-hour rainfall (mm) · not summed',[['rain_current_hour_mm','Source accumulation','rain']]]
];
const value=(s,k)=>k==='direction'?direction(s):s.values[k];
const stamp=t=>new Date(t).toISOString().slice(0,16).replace('T',' ')+' UTC';
function plot(rows,start,end,track,models=[],options={}){
 const [title,series,bearing]=track,unified=Number.isFinite(options.now);
 const card=document.createElement('section');card.className='awos-history-track';
 if(unified)card.classList.add('unified-track');
 const h=document.createElement('h4');h.textContent=title;card.append(h);
 const obsValue=(s,k)=>value(s,k),modelValue=(s,k)=>s.values[k];
 const nums=rows.flatMap(s=>series.map(([k])=>obsValue(s,k))).concat(models.flatMap(s=>series.map(([k])=>modelValue(s,k)))).filter(finite);
 if(!nums.length){const p=document.createElement('p');p.className='section-note';p.textContent='No valid measurements in this window.';card.append(p);return card;}
 let lo=bearing?0:nums.length?Math.floor(Math.min(...nums)):0,hi=bearing?360:nums.length?Math.ceil(Math.max(...nums)):1;
 if(['wind_speed_kt','rain_current_hour_mm','model_rain','spread'].includes(series[0][0]))lo=Math.min(0,lo);
 if(series[0][0]==='relative_humidity_pct'||series[0][0]==='cloud_low'){lo=0;hi=100;}
 if(hi<=lo)hi=lo+1;
 const small=window.innerWidth<=660,W=unified?(small?420:960):640,L=small?43:52,R=W-15,top=13,bottom=99,H=unified?130:152;
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('role','img');svg.setAttribute('aria-label',title+' over the selected time window, with missing observations left as gaps');
 const add=(tag,attrs,txt)=>{const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(txt!==undefined)e.textContent=txt;svg.append(e);return e;};
 const X=t=>L+(t-start)/(end-start)*(R-L),Y=v=>bottom-(v-lo)/(hi-lo)*(bottom-top);
 if(unified){
  add('rect',{x:L,y:top,width:Math.max(0,X(options.now)-L),height:bottom-top,class:'unified-past'});
  add('rect',{x:X(options.now),y:top,width:Math.max(0,R-X(options.now)),height:bottom-top,class:'unified-future'});
 }
 for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,y=Y(v);add('line',{x1:L,x2:R,y1:y,y2:y,class:'obs-context-grid'});add('text',{x:L-5,y:y+4,'text-anchor':'end',class:'obs-context-label'},bearing?String(v):v.toFixed(1));}
 const zone=typeof titanPreviewSettings==='function'?titanPreviewSettings().zone:'UTC';
 const ticks=unified?(small?4:7):4;
 for(let i=0;i<=ticks;i++){const t=start+(end-start)*i/ticks;const label=typeof TitanPreview!=='undefined'?TitanPreview.label(t,zone,true):new Date(t).toISOString().slice(11,16);add('text',{x:X(t),y:H-9,'text-anchor':i===0?'start':i===ticks?'end':'middle',class:'obs-context-label'},label);}
 for(const [key,label,cls] of series){
  let d='',dots='',prev=null;
  for(const s of rows){const v=obsValue(s,key);if(!finite(v)){prev=null;continue;}const x=X(s.at),y=Y(v);
   dots+='M'+(x-.8).toFixed(2)+' '+y.toFixed(2)+'h1.6 ';
   if(!bearing){d+=(prev!==null&&s.at-prev<=180000?'L':'M')+x.toFixed(2)+' '+y.toFixed(2)+' ';prev=s.at;}
  }
  if(d)add('path',{d,class:'awos-history-line '+cls});
  if(dots)add('path',{d:dots,class:'awos-history-line '+cls+' points'});
  let fd='',md='',last=null;
  for(const s of models){const v=modelValue(s,key);if(!finite(v)){last=null;continue;}const x=X(s.at),y=Y(v);
   if(!bearing)fd+=(last!==null&&s.at-last===HOUR?'L':'M')+x.toFixed(2)+' '+y.toFixed(2)+' ';
   // Individual model valid-time markers remain visible even for a singleton.
   md+='M'+(x-2).toFixed(2)+' '+y.toFixed(2)+'h4 ';last=s.at;
  }
  if(fd)add('path',{d:fd,class:'awos-history-line '+cls+' model'});
  if(md)add('path',{d:md,class:'awos-history-line '+cls+' model-points'+(bearing?' model-bearing':'')});
 }
 if(unified){
  add('line',{x1:X(options.now),x2:X(options.now),y1:top,y2:bottom,class:'unified-now'});
  add('text',{x:X(options.now),y:10,'text-anchor':'middle',class:'unified-now-label'},'NOW');
  const cursor=add('line',{x1:L,x2:L,y1:top,y2:bottom,class:'unified-cursor',visibility:'hidden'});
  svg.dataset.start=String(start);svg.dataset.end=String(end);svg.dataset.left=String(L);svg.dataset.right=String(R);
  const choose=e=>{const matrix=svg.getScreenCTM();if(!matrix)return;const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const q=p.matrixTransform(matrix.inverse());if(q.x<L||q.x>R||q.y<top||q.y>bottom)return;selectUnifiedTime(start+(q.x-L)/(R-L)*(end-start));};
  svg.addEventListener('pointermove',e=>{if(e.pointerType==='mouse')choose(e);});svg.addEventListener('click',choose);
 }
 card.append(svg);
 const legend=document.createElement('div');legend.className='awos-history-legend';
 for(const [,label,cls] of series){const item=document.createElement('span'),swatch=document.createElement('i');swatch.className=cls;item.append(swatch,document.createTextNode(label));legend.append(item);}
 if(unified&&options.note)legend.append(document.createTextNode(options.note));
 else if(models.length)legend.append(document.createTextNode(' · dashed: model, solid: observations'));
 card.append(legend);return card;
}
let data=null,controller=null,timer=null,sequence=0,lastRequest=0;
function visible(){return document.visibilityState==='visible'&&['tab-observations','tab-meteogram'].some(id=>el(id)?.classList.contains('active'));}
function clear(message){
 data=null;unifiedSignature='';el('awos-history-status').textContent=message;el('awos-history-tracks').replaceChildren();el('awos-history-tendency').replaceChildren();el('awos-history-reports').replaceChildren();
 renderMeteogram(true);
}
let unifiedState=null,selectedTime=null,unifiedSignature='';
const modelFields={temperature_c:'Temperature',dewpoint_c:'Dewpoint',wind_speed_kt:'Wind',gust_kt:'Gust',model_pressure:'Pressure',model_rain:'Rain'};
function modelSample(r){
 const values={};for(const [k,v] of Object.entries(modelFields))values[k]=finite(r[v])?r[v]:null;
 values.direction=finite(r['Wind Dir.'])&&r['Wind Dir.']>=0&&r['Wind Dir.']<=360&&finite(r.Wind)&&r.Wind>0?r['Wind Dir.']%360:null;
 values.spread=finite(values.temperature_c)&&finite(values.dewpoint_c)?values.temperature_c-values.dewpoint_c:null;
 values.cloud_low=finite(r['Low Clouds'])?r['Low Clouds']:null;
 values.cloud_mid=finite(r['Mid Clouds'])?r['Mid Clouds']:null;
 values.cloud_high=finite(r['High Clouds'])?r['High Clouds']:null;
 return {at:previewValidEpoch(r.Datetime),values};
}
function selectUnifiedTime(t){
 if(!unifiedState)return;
 const {rows,models,now,start,end}=unifiedState;
 const isObs=t<=now,points=isObs?rows:models;
 const match=points.reduce((best,s)=>!best||Math.abs(s.at-t)<Math.abs(best.at-t)?s:best,null);
 const tolerance=isObs?180000:1800000;
 selectedTime=match&&Math.abs(match.at-t)<=tolerance?match.at:t;
 const scrub=el('unified-time-slider');if(scrub){scrub.value=String(Math.max(0,Math.min(Number(scrub.max),Math.round((selectedTime-start)/60000))));scrub.setAttribute('aria-valuetext',titanPreviewLabel(selectedTime));}
 const out=el('unified-readout');if(!out)return;out.replaceChildren();
 const add=(v)=>{const span=document.createElement('span');span.textContent=v;out.append(span);};
 if(!match||Math.abs(match.at-t)>tolerance)add('No '+(isObs?'observation':'model hour')+' near '+titanPreviewLabel(t));
 else{
  add((isObs?'AWOS · ':'Model · ')+titanPreviewLabel(match.at));
  const fields=[['T','temperature_c','°C'],['Td','dewpoint_c','°C'],['Wind','wind_speed_kt','kt'],['Gust','gust_kt','kt'],['Bearing','direction','°']];
  for(const [label,k,unit] of fields){const v=k==='direction'?(isObs?direction(match):match.values.direction):match.values[k];add(label+' '+(finite(v)?v.toFixed(k==='direction'?0:1)+' '+unit:'—'));}
  if(!isObs&&typeof terminalState!=='undefined'){const idx=terminalState.rows.findIndex(r=>previewValidEpoch(r.Datetime)===match.at);if(idx>=0&&terminalState.index!==idx)selectTerminalHour(idx);}
 }
 for(const svg of document.querySelectorAll('#awos-history-meteogram svg[data-start]')){
  const c=svg.querySelector('.unified-cursor'),a=Number(svg.dataset.start),b=Number(svg.dataset.end),l=Number(svg.dataset.left),r=Number(svg.dataset.right),x=l+(selectedTime-a)/(b-a)*(r-l);
  if(c){c.setAttribute('x1',x);c.setAttribute('x2',x);c.setAttribute('visibility',x>=l&&x<=r?'visible':'hidden');}
 }
}
function renderMeteogram(force=false){
 const mount=el('awos-history-meteogram');if(!mount||!el('tab-meteogram')?.classList.contains('active'))return;
 const now=Date.now(),past=Number(el('unified-history-hours')?.value||el('awos-history-hours').value),forecast=titanPreviewSettings().hours;
 const eligible=typeof obsContext!=='undefined'&&previewIsCurrent(obsContext.manifest,now);
 const history=data&&now-data.generated<=600000&&data.hours===past?data:null;
 // Stable axis until next minute, with forecast horizon synchronized to the existing selector.
 const clock=Math.floor(now/60000)*60000,start=clock-past*HOUR,end=Math.floor(clock/HOUR)*HOUR+forecast*HOUR;
 const signature=[clock,history?.generated,eligible?obsContext.manifest.expires_at_utc:'none',past,forecast,titanPreviewSettings().zone,window.innerWidth<=660].join('|');
 if(!force&&signature===unifiedSignature)return;unifiedSignature=signature;
 const rows=(history?.rows||[]).filter(s=>s.at>=start&&s.at<=now).map(s=>({...s,values:{...s.values,spread:finite(s.values.temperature_c)&&finite(s.values.dewpoint_c)?s.values.temperature_c-s.values.dewpoint_c:null}}));
 const models=eligible?obsContext.rows.map(modelSample).filter(s=>s.at>=now&&s.at<end).sort((a,b)=>a.at-b.at):[];
 unifiedState={rows,models,now,start,end};
 const scrub=el('unified-time-slider');if(scrub){scrub.max=String(Math.floor((end-start)/60000));scrub.disabled=!rows.length&&!models.length;}
 mount.replaceChildren();
 const status=el('unified-status');if(status)status.textContent=(history?rows.length+' AWOS samples':'AWOS history unavailable')+' · '+(models.length?models.length+' eligible future model hours':'No eligible future model guidance')+' · '+titanPreviewSettings().zone;
 const core=[
 [tracks[0],rows,models,'Solid: AWOS · dashed: model'],
 [tracks[1],rows,models,'AWOS 2-min speed / source gust vs model output; intervals differ'],
 [['Wind direction (°)',[['direction','AWOS raw bearing / model bearing','wind']],true],rows,models,'Green: AWOS raw bearing (reference unverified); cyan: model bearing; no angular comparison'],
 [['Temperature − dewpoint (°C)',[['spread','T − Td','dew']]],rows,models,'Solid: AWOS · dashed: model'],
 [tracks[4],rows,[],'Observed only; model RH not supplied by this payload'],
 [tracks[5],rows,[],'AWOS source current-hour amount; not summed'],
 [['Model precipitation (mm/output h)',[['model_rain','Model rainfall','rain']]],[],models,'Model output-hour accumulation; distinct from AWOS'],
 [tracks[3],rows,[],'AWOS altimeter setting; not confirmed QNH'],
 [['Model pressure (hPa)',[['model_pressure','Model pressure','pressure']]],[],models,'Definition not reconciled with AWOS; separate scales'],
 [['Model cloud cover (%) · not ceiling',[['cloud_low','Low','rain'],['cloud_mid','Mid','pressure'],['cloud_high','High','wind']]],[],models,'Model only']
 ];
 for(const [track,o,m,note] of core)mount.append(plot(o,start,end,track,m,{now,note}));
 selectUnifiedTime(selectedTime!==null&&selectedTime>=start&&selectedTime<=end?selectedTime:rows.length?rows[rows.length-1].at:models.length?models[0].at:now);
}
root.refreshUnifiedMeteogram=()=>renderMeteogram(true);
function paint(){
 if(!data)return;
 if(!el('tab-observations')?.classList.contains('active')){renderMeteogram();return;}
 const mount=el('awos-history-tracks');mount.replaceChildren();
 const now=Date.now(),start=now-data.hours*HOUR,rows=data.rows.filter(s=>s.at>=start&&s.at<=now),reports=data.reports.filter(r=>r.at>=start&&r.at<=now);
 const latest=rows[rows.length-1];
 el('awos-history-status').textContent=rows.length?rows.length+' unique samples · last '+stamp(latest.at)+' · '+(now-latest.at>300000?'latest sample stale':'recent sample')+' · '+data.hours+' h window':'No samples collected yet in this window.';
 if(el('tab-observations')?.classList.contains('active'))for(const track of tracks)mount.append(plot(rows,start,now,track));
 const t=el('awos-history-tendency');t.replaceChildren();
 for(const [label,key,minutes,unit] of [['Temperature','temperature_c',30,'°C'],['Altimeter','altimeter_setting_hpa',60,'hPa'],['Wind speed','wind_speed_kt',15,'kt'],['Source bearing shift','direction',15,'°']]){
  const v=tendency(rows,minutes,key,now),item=document.createElement('div');item.className='obs-metric';
  const name=document.createElement('span');name.textContent=label+' · '+minutes+' min';
  const n=document.createElement('strong');n.textContent=v?(v.difference>0?'+':'')+v.difference.toFixed(1)+' '+unit:'—';
  if(v)n.title='Actual elapsed '+v.elapsedMinutes.toFixed(1)+' min';
  item.append(name,n);t.append(item);
 }
 const out=el('awos-history-reports');out.replaceChildren();
 if(!reports.length)out.textContent='No reports in this window.';
 for(const r of reports){const p=document.createElement('pre');p.textContent=stamp(r.at)+' · '+r.text;out.append(p);}
 renderMeteogram();
}
async function refresh(){
 if(!visible()||controller)return;
 const hours=Number(el('awos-history-hours').value);
 const raw=document.querySelector('meta[name="titan-awos-proxy"]')?.content;
 let endpoint;try{const u=new URL(raw,location.origin);if(u.protocol!=='https:'||u.pathname!=='/awos'||u.username||u.password)throw Error('endpoint');u.pathname='/awos-history';u.search='hours='+hours;endpoint=u.href;}catch(_){clear('Hosted AWOS history is not configured.');return;}
 const token=++sequence,abort=new AbortController();controller=abort;lastRequest=Date.now();const deadline=setTimeout(()=>abort.abort(),12000);
 try{
  const response=await fetch(endpoint,{signal:abort.signal,credentials:'omit',cache:'no-store',redirect:'error'});
  if(token!==sequence)return;
  if(response.status===503){clear('Rolling collection is not enabled yet. Live AWOS remains separate.');return;}
  if(!response.ok)throw Error('history unavailable');
  const result=parseHistory(await response.json(),hours,Date.now(),TitanObservation);
  if(token!==sequence||!visible())return;data=result;paint();
 }catch(_){if(token===sequence)clear('AWOS history unavailable — trends withheld.');}
 finally{clearTimeout(deadline);if(token===sequence)controller=null;}
}
function stop(){sequence++;if(controller){controller.abort();lastRequest=0;}controller=null;}
function schedule(){if(!visible()){stop();return;}if(data)paint();else renderMeteogram();if(Date.now()-lastRequest>=300000)refresh();}
function setup(){
 if(!el('awos-history-hours'))return;
 const change=e=>{const v=e.target.value;el('awos-history-hours').value=v;if(el('unified-history-hours'))el('unified-history-hours').value=v;stop();clear('Retrieving selected history window…');refresh();};
 el('awos-history-hours').addEventListener('change',change);el('unified-history-hours')?.addEventListener('change',change);
 el('unified-history-refresh')?.addEventListener('click',()=>{stop();refresh();});
 el('unified-now')?.addEventListener('click',()=>selectUnifiedTime(unifiedState?.rows.at(-1)?.at??Date.now()));
 el('unified-time-slider')?.addEventListener('input',e=>{if(unifiedState)selectUnifiedTime(unifiedState.start+Number(e.target.value)*60000);});
 window.addEventListener('resize',()=>renderMeteogram());
 el('awos-history-refresh').addEventListener('click',()=>{stop();refresh();});
 document.addEventListener('visibilitychange',schedule);
 const watch=new MutationObserver(schedule);for(const id of ['tab-observations','tab-meteogram']){const p=el(id);if(p)watch.observe(p,{attributes:true,attributeFilter:['class']});}
 timer=setInterval(()=>{if(visible()){if(data&&Date.now()-data.generated>600000)clear('History retrieval expired — trends withheld.');else if(data)paint();schedule();}},30000);
 window.addEventListener('pagehide',stop);window.addEventListener('pageshow',schedule);
 schedule();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})(typeof globalThis!=='undefined'?globalThis:this);
