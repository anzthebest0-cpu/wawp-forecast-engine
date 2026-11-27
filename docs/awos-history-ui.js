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
function plot(rows,start,end,track,models=[]){
 const [title,series,bearing]=track;
 const card=document.createElement('section');card.className='awos-history-track';
 const h=document.createElement('h4');h.textContent=title;card.append(h);
 const nums=rows.flatMap(s=>series.map(([k])=>value(s,k))).concat(models.flatMap(s=>series.map(([k])=>value(s,k)))).filter(finite);
 if(!nums.length){const p=document.createElement('p');p.className='section-note';p.textContent='No valid measurements in this window.';card.append(p);return card;}
 let lo=bearing?0:Math.floor(Math.min(...nums)),hi=bearing?360:Math.ceil(Math.max(...nums));
 if(series[0][0]==='wind_speed_kt'||series[0][0]==='rain_current_hour_mm')lo=0;
 if(hi<=lo)hi=lo+1;
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 640 152');svg.setAttribute('role','img');svg.setAttribute('aria-label',title+' over the selected time window, with missing observations left as gaps');
 const add=(tag,attrs,txt)=>{const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(txt!==undefined)e.textContent=txt;svg.append(e);return e;};
 const X=t=>45+(t-start)/(end-start)*575,Y=v=>119-(v-lo)/(hi-lo)*100;
 for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,y=Y(v);add('line',{x1:45,x2:620,y1:y,y2:y,class:'obs-context-grid'});add('text',{x:39,y:y+4,'text-anchor':'end',class:'obs-context-label'},bearing?String(v):v.toFixed(1));}
 for(let i=0;i<=4;i++){const t=start+(end-start)*i/4;add('text',{x:X(t),y:140,'text-anchor':i===0?'start':i===4?'end':'middle',class:'obs-context-label'},new Date(t).toISOString().slice(11,16)+'Z');}
 for(const [key,label,cls] of series){
  let d='',dots='',prev=null;
  for(const s of rows){const v=value(s,key);if(!finite(v)){prev=null;continue;}const x=X(s.at),y=Y(v);
   dots+='M'+(x-.8).toFixed(2)+' '+y.toFixed(2)+'h1.6 ';
   if(!bearing){d+=(prev!==null&&s.at-prev<=180000?'L':'M')+x.toFixed(2)+' '+y.toFixed(2)+' ';prev=s.at;}
  }
  if(d)add('path',{d,class:'awos-history-line '+cls});
  if(dots)add('path',{d:dots,class:'awos-history-line '+cls+' points'});
  let fd='',last=null;
  for(const s of models){const v=value(s,key);if(!finite(v)){last=null;continue;}fd+=(last!==null&&s.at-last===HOUR?'L':'M')+X(s.at).toFixed(2)+' '+Y(v).toFixed(2)+' ';last=s.at;}
  if(fd)add('path',{d:fd,class:'awos-history-line '+cls+' model'});
 }
 card.append(svg);
 const legend=document.createElement('div');legend.className='awos-history-legend';
 for(const [,label,cls] of series){const item=document.createElement('span'),swatch=document.createElement('i');swatch.className=cls;item.append(swatch,document.createTextNode(label));legend.append(item);}
 if(models.length)legend.append(document.createTextNode(' · dashed: model, solid: observations'));
 card.append(legend);return card;
}
let data=null,controller=null,timer=null,sequence=0,lastRequest=0;
function visible(){return document.visibilityState==='visible'&&['tab-observations','tab-meteogram'].some(id=>el(id)?.classList.contains('active'));}
function clear(message){
 data=null;el('awos-history-status').textContent=message;el('awos-history-tracks').replaceChildren();el('awos-history-tendency').replaceChildren();el('awos-history-reports').replaceChildren();
 const context=el('awos-history-meteogram');if(context)context.replaceChildren(document.createTextNode(message));
}
function renderMeteogram(){
 const mount=el('awos-history-meteogram');if(!mount)return;mount.replaceChildren();
 if(!data||Date.now()-data.generated>600000){mount.textContent='Recent history unavailable.';return;}
 const now=Date.now(),past=Math.min(data.hours,6),start=now-past*HOUR,end=now+6*HOUR,rows=data.rows.filter(s=>s.at>=start&&s.at<=now);
 let models=[];
 if(typeof obsContext!=='undefined'&&typeof previewIsCurrent==='function'&&previewIsCurrent(obsContext.manifest,now)){
  models=obsContext.rows.map(r=>({at:previewValidEpoch(r.Datetime),values:{temperature_c:r.Temperature,dewpoint_c:r.Dewpoint}})).filter(s=>s.at>=now&&s.at<=end).sort((a,b)=>a.at-b.at);
 }
 const p=document.createElement('p');p.className='section-note';p.textContent='Past '+past+' hours of available observations and next six hours of eligible temperature/dewpoint guidance. Separate series, no interpolation across the observation–forecast boundary. UTC.';mount.append(p,plot(rows,start,end,tracks[0],models));
}
function paint(){
 const mount=el('awos-history-tracks');mount.replaceChildren();
 if(!data)return;
 const now=Date.now(),start=now-data.hours*HOUR,rows=data.rows.filter(s=>s.at>=start&&s.at<=now),reports=data.reports.filter(r=>r.at>=start&&r.at<=now);
 const latest=rows[rows.length-1];
 el('awos-history-status').textContent=rows.length?rows.length+' unique samples · last '+stamp(latest.at)+' · '+(now-latest.at>300000?'latest sample stale':'recent sample')+' · '+data.hours+' h window':'No samples collected yet in this window.';
 for(const track of tracks)mount.append(plot(rows,start,now,track));
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
function stop(){sequence++;controller?.abort();controller=null;}
function schedule(){if(!visible()){stop();return;}if(Date.now()-lastRequest>=300000)refresh();}
function setup(){
 if(!el('awos-history-hours'))return;
 el('awos-history-hours').addEventListener('change',()=>{stop();clear('Retrieving selected history window…');refresh();});
 el('awos-history-refresh').addEventListener('click',()=>{stop();refresh();});
 document.addEventListener('visibilitychange',schedule);
 const watch=new MutationObserver(schedule);for(const id of ['tab-observations','tab-meteogram']){const p=el(id);if(p)watch.observe(p,{attributes:true,attributeFilter:['class']});}
 timer=setInterval(()=>{if(visible()){if(data&&Date.now()-data.generated>600000)clear('History retrieval expired — trends withheld.');else if(data)paint();schedule();}},30000);
 window.addEventListener('pagehide',stop);window.addEventListener('pageshow',schedule);
 schedule();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})(typeof globalThis!=='undefined'?globalThis:this);
