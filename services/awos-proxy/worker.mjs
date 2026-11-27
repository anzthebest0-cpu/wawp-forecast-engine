import {METAR_URL,normalizeMetar} from './metar.mjs';
import {SaxesParser} from 'saxes';
const SOURCE='http://wawp.awosnet.com/awiAwosNet.php';
const MAX=131072;
const aliases={
 station:['airportIdentifier','airportID'],date:['date'],time:['time'],
 wind_speed_kt:['twoMinuteWindSpeed','twoMinutewindSpeed'],wind_direction_deg:['twoMinuteWindDirection','twoMinutewindDirection'],
 gust_kt:['windGust'],temperature_c:['airTemperature'],dewpoint_c:['dewPoint'],
 temperature_10min_c:['tenMinuteairTemperature'],dewpoint_10min_c:['tenMinuteDewPoint','tenMinutedewPoint'],
 altimeter_setting_hpa:['altimeterSetting'],relative_humidity_pct:['relativeHumidity'],rain_current_hour_mm:['rainfallCurrentHour'],
 solar_wm2:['solarRadiation'],density_altitude_ft:['densityAltitude'],metar:['METAR']
};
const specs={
 wind_speed_kt:[['knots','kt','kts','knot'],0,200],wind_direction_deg:[['degrees','deg','degree','°','degrees true','deg true'],0,360],
 gust_kt:[['knots','kt','kts','knot'],0,250],temperature_c:[['deg c','°c','c','celsius'],-50,65],
 dewpoint_c:[['deg c','°c','c','celsius'],-80,65],temperature_10min_c:[['deg c','°c','c','celsius'],-50,65],
 dewpoint_10min_c:[['deg c','°c','c','celsius'],-80,65],altimeter_setting_hpa:[['hpa'],850,1100],
 relative_humidity_pct:[['percent','%'],0,100],rain_current_hour_mm:[['mm'],0,500],solar_wm2:[['w/m^2','w/m²'],0,1600],
 density_altitude_ft:[['feet','ft'],-5000,30000]
};
const tags=new Set(Object.values(aliases).flat());
const absent=new Set(['','-','--','/','//','///','////','NA','N/A','M','\\\\']);
const stamp=t=>new Date(t).toISOString().slice(0,19)+'Z';
function clock(date,time){
 let d=/^(\d{2})\/(\d{2})\/(\d{4})$/.exec(date), y,m,day;
 if(d){[,day,m,y]=d;}else{d=/^(\d{4})-(\d{2})-(\d{2})$/.exec(date);if(!d)throw Error('date');[,y,m,day]=d;}
 if(!/^\d{2}:\d{2}(:\d{2})?$/.test(time))throw Error('time');
 const iso=y+'-'+m+'-'+day+'T'+(time.length===5?time+':00':time)+'Z', t=Date.parse(iso);
 if(!Number.isFinite(t)||stamp(t)!==iso)throw Error('clock');return t;
}
export function normalize(xml,now){
 if(typeof xml!=='string'||xml.length>MAX||/<!DOCTYPE|<!ENTITY|\x00/i.test(xml))throw Error('XML');
 let depth=0,root=false;const found=new Map();
 const parser=new SaxesParser({xmlns:false});
 parser.on('error',e=>{throw e;});parser.on('doctype',()=>{throw Error('doctype');});
 parser.on('opentag',tag=>{
   depth++;
   if(depth===1){if(root||tag.name!=='awosnet')throw Error('root');root=true;}
   if(depth===2&&tags.has(tag.name)){
     if(found.has(tag.name))throw Error('repeat');
     const a=tag.attributes;found.set(tag.name,[(a.value||'').trim(),(a.units||'').trim().toLowerCase(),(a.time||'').trim()]);
   }
 });
 parser.on('closetag',()=>depth--);parser.write(xml).close();
 if(!root||depth!==0)throw Error('root');
 const group=k=>{const a=aliases[k].filter(x=>found.has(x)).map(x=>found.get(x));if(a.some(x=>JSON.stringify(x)!==JSON.stringify(a[0])))throw Error('alias');return a[0]||['','',''];};
 if(group('station')[0].toUpperCase()!=='WAWP')throw Error('station');
 const [date,,de]=group('date'),[time,,te]=group('time');if(de&&te&&de!==te)throw Error('clock metadata');
 const t=clock(date,time);if(t>now||now-t>300000)throw Error('freshness');
 const epoch=te||de;let offset=null;if(epoch){if(!/^\d+$/.test(epoch)||!Number.isSafeInteger(Number(epoch)))throw Error('epoch');offset=Number(epoch)-t/1000;}
 const out={schema:'titan-local-awos-v1',station:'WAWP',source:'AWOSnet',observed_at_utc:stamp(t),retrieved_at_utc:stamp(now),wind_average_minutes:2,wind_reference:'true',units_confirmed:true,timestamp_basis:'confirmed_display_clock_utc',source_epoch_offset_seconds:offset,qnh_hpa:null};
 for(const [k,[units,lo,hi]] of Object.entries(specs)){
   const [v,u,e]=group(k);out[k]=null;
   if(absent.has(v.toUpperCase()))continue;
   if(!units.includes(u))throw Error('units');
   if(epoch&&e!==epoch)continue;
   if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(v))throw Error('number');
   const n=Number(v);if(!Number.isFinite(n)||n<lo||n>hi)throw Error('range');out[k]=n;
 }
 if(Object.keys(specs).every(k=>out[k]===null))throw Error('empty');
 for(const [a,b] of [['temperature_c','dewpoint_c'],['temperature_10min_c','dewpoint_10min_c']])if(out[a]!==null&&out[b]!==null&&out[b]>out[a]+.5)throw Error('dewpoint');
 const report=group('metar')[0],r=/^(METAR|SPECI)(?: COR)? WAWP (\d{2})(\d{2})(\d{2})Z(?: |$)/.exec(report);
 if(r&&report.length<=400&&!/\bNIL\b|[\x00-\x1f\x7f]/.test(report)){
   for(const days of [0,1]){
     const d=new Date(t-days*86400000);if(d.getUTCDate()!==Number(r[2])||Number(r[3])>23||Number(r[4])>59)continue;
     const at=d.setUTCHours(Number(r[3]),Number(r[4]),0,0);if(at<=t&&t-at<=86400000){out.metar_text=report;out.metar_date_utc=stamp(at).slice(0,10);break;}
   }
 }
 return out;
}
async function bounded(response){
 const reader=response.body?.getReader();if(!reader)throw Error('body');
 let bytes=0;const decoder=new TextDecoder('utf-8',{fatal:true});let text='';
 try{while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>MAX)throw Error('size');text+=decoder.decode(value,{stream:true});}return text+decoder.decode();}
 finally{await reader.cancel().catch(()=>{});}
}
export function makeHandler(upstream=fetch,now=Date.now){
 return async(request,env)=>{
   const origin=request.headers.get('Origin'), allowed=env.ALLOWED_ORIGIN||'';
   const headers={'Content-Type':'application/json','Cache-Control':'no-store, max-age=0','X-Content-Type-Options':'nosniff','Vary':'Origin'};
   if(origin&&origin===allowed)headers['Access-Control-Allow-Origin']=origin;
   const send=(status,body)=>new Response(JSON.stringify(body),{status,headers});
   const path=new URL(request.url).pathname;
   if(!['/awos','/metar'].includes(path)||request.method!=='GET'||new URL(request.url).search)return send(404,{status:'unavailable'});
   if(path==='/metar'){
     // Independent authorization. No AWOS request, no credentials in browser.
     if(env.PUBLIC_METAR_AUTHORIZED!=='true')return send(503,{status:'disabled'});
     if(!origin||origin!==allowed||!allowed.startsWith('https://'))return send(403,{status:'forbidden'});
     const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),6000);
     try{
       const res=await upstream(METAR_URL,{method:'GET',redirect:'error',headers:{Accept:'application/json','User-Agent':'TITAN-WAWP-observation-research/1.0'},signal:controller.signal});
       if(res.status===204)return send(200,{schema:'titan-live-metar-v1',station:'WAWP',source:'NOAA AWC',status:'no_recent_report'});
       if(!res.ok)throw Error('upstream');
       const report=normalizeMetar(JSON.parse(await bounded(res)),now());
       return send(200,report||{schema:'titan-live-metar-v1',station:'WAWP',source:'NOAA AWC',status:'no_recent_report'});
     }catch(_){return send(502,{status:'unavailable'});}finally{clearTimeout(timer);}
   }
   // CORS is not authentication. Only enable if public redistribution is permitted.
   if(env.PUBLIC_FEED_AUTHORIZED!=='true'||env.DISPLAY_CLOCK_UTC_CONFIRMED!=='true'||env.WIND_TRUE_CONFIRMED!=='true')return send(503,{status:'disabled'});
   if(!origin||origin!==allowed||!allowed.startsWith('https://'))return send(403,{status:'forbidden'});
   const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),6000);
   try{
     const res=await upstream(SOURCE+'?t='+now(),{method:'GET',redirect:'error',cache:'no-store',headers:{'Accept':'application/xml,text/xml','Cache-Control':'no-cache'},signal:controller.signal});
     if(!res.ok)throw Error('upstream');
     const xml=await bounded(res);return send(200,normalize(xml,now()));
   }catch(_){return send(502,{status:'unavailable'});}finally{clearTimeout(timer);}
 };
}
export default {fetch:makeHandler()};
