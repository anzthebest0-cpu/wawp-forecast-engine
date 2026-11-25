/* Supplemental observation viewer. On-demand proxy only when configured; no upload or persistence. */
(function(root){
'use strict';
const limits={wind_speed_kt:[0,200],wind_direction_deg:[0,360],gust_kt:[0,250],temperature_c:[-50,65],dewpoint_c:[-80,65],qnh_hpa:[850,1100],altimeter_setting_hpa:[850,1100],temperature_10min_c:[-50,65],dewpoint_10min_c:[-80,65],relative_humidity_pct:[0,100],rain_current_hour_mm:[0,500],solar_wm2:[0,1600],density_altitude_ft:[-5000,30000]};
function utc(s){
    if(typeof s!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(s))return NaN;
    const t=Date.parse(s);
    return Number.isFinite(t)&&new Date(t).toISOString().slice(0,19)+'Z'===s?t:NaN;
}
function snapshot(data,now){
    if(!data||data.schema!=='titan-local-awos-v1'||data.station!=='WAWP'||data.source!=='AWOSnet'||data.wind_average_minutes!==2||data.wind_reference!=='true'||data.units_confirmed!==true)throw Error('Station, schema, source, 2-minute wind, true-north reference and confirmed units required.');
    const t=utc(data.observed_at_utc);
    if(!Number.isFinite(t)||t>now)throw Error('Missing, invalid or future observation timestamp.');
    const values={};
    for(const [key,[lo,hi]] of Object.entries(limits)){
        const v=data[key]??null;
        if(v!==null&&(typeof v!=='number'||!Number.isFinite(v)||v<lo||v>hi))throw Error('Invalid field: '+key);
        values[key]=v;
    }
    if(Object.values(values).every(v=>v===null))throw Error('No sensor values.');
    if(values.temperature_c!==null&&values.dewpoint_c!==null&&values.dewpoint_c>values.temperature_c+0.5)throw Error('Dewpoint exceeds temperature by more than 0.5°C.');
    if(values.temperature_10min_c!==null&&values.dewpoint_10min_c!==null&&values.dewpoint_10min_c>values.temperature_10min_c+0.5)throw Error('10-minute dewpoint exceeds temperature.');
    const offset=data.source_epoch_offset_seconds;
    return {at:t,values,epochOffset:Number.isSafeInteger(offset)?offset:null};
}
function report(raw,date,now){
    if(typeof raw!=='string'||raw.length>400||/[\x00-\x1f\x7f]/.test(raw.trim()))throw Error('Enter one METAR/SPECI line only.');
    const text=raw.trim().toUpperCase().replace(/\s+/g,' ').replace(/=$/,'').trim();
    const m=/^(METAR|SPECI)(?: COR)? WAWP (\d{2})(\d{2})(\d{2})Z(?: |$)/.exec(text);
    if(!m||!/^\d{4}-\d{2}-\d{2}$/.test(date)||date.slice(8)!==m[2])throw Error('WAWP METAR/SPECI and matching full UTC report date required.');
    if(/\bNIL\b/.test(text))throw Error('NIL report has no observation.');
    const at=utc(date+'T'+m[3]+':'+m[4]+':00Z');
    if(!Number.isFinite(at)||at>now)throw Error('Invalid or future report timestamp.');
    return {at,text};
}
function age(at,now,maxMinutes){
    const minutes=(now-at)/60000;
    if(!Number.isFinite(minutes)||minutes<0)return {state:'invalid',minutes:null};
    return {state:minutes<=maxMinutes?'recent':'stale',minutes:Math.floor(minutes)};
}
const api={utc,snapshot,report,age};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.TitanObservation=api;
})(typeof globalThis!=='undefined'?globalThis:this);
function setupObservationPanel(){
    const el=id=>document.getElementById(id);
    if(!el('obs-file'))return;
    let sample=null,metar=null,importVersion=0,sourceError='',metarError='',reportSource='manually entered',sampleSource='manually imported',retrievedAt=null;
    let endpoint=null;try{const raw=document.querySelector('meta[name="titan-awos-proxy"]')?.content;if(raw){const url=new URL(raw);if(url.protocol==='https:'&&!url.username&&!url.password&&!url.search&&!url.hash&&url.pathname==='/awos')endpoint=url.href;}}catch(_){}
    let enabled=!!endpoint, timer=null,controller=null, generation=0;
    const visible=()=>document.visibilityState==='visible'&&enabled;
    const stop=()=>{generation++;clearTimeout(timer);timer=null;controller?.abort();controller=null;};
    const fields=[['wind_speed_kt',' kt'],['wind_direction_deg','°'],['gust_kt',' kt'],['temperature_c',' °C'],['dewpoint_c',' °C'],['qnh_hpa',' hPa'],['altimeter_setting_hpa',' hPa'],['temperature_10min_c',' °C'],['dewpoint_10min_c',' °C'],['relative_humidity_pct','%'],['rain_current_hour_mm',' mm'],['solar_wm2',' W/m²'],['density_altitude_ft',' ft']];
    const stamp=t=>new Date(t).toISOString().slice(0,19)+'Z';
    const render=()=>{
        const now=Date.now(),a=sample?TitanObservation.age(sample.at,now,5):null,r=metar?TitanObservation.age(metar.at,now,60):null;
        el('obs-status').textContent=sourceError||(!sample?'Not connected — no local snapshot loaded':a.state==='recent'?'Recent '+sampleSource+' snapshot · '+a.minutes+' min old':'Stale or invalid snapshot — values withheld');
        el('obs-time').textContent=sample?'Confirmed source display clock: '+stamp(sample.at):'Sensor observation time: —';
        el('obs-retrieved').textContent=retrievedAt!==null?'Retrieved at: '+stamp(retrievedAt):'';
        el('obs-poll-status').textContent=!endpoint?'Automatic retrieval not configured':!enabled?'Automatic retrieval paused':document.visibilityState!=='visible'?'Paused while browser tab is hidden':'Automatic retrieval every 60 s while visible';
        el('obs-epoch-note').textContent=sample&&sample.epochOffset!==null?'Raw XML epoch offset from display clock: '+sample.epochOffset+' s. Not used as UTC observation time.':'';
        for(const [key,unit] of fields){
            const v=sample&&a.state==='recent'?sample.values[key]:null;
            el('obs-'+key).textContent=v===null?'—':String(v)+unit;
        }
        el('obs-metar-status').textContent=metarError||(!metar?'No manually entered report':r.state==='recent'?'Recent '+reportSource+' report · '+r.minutes+' min old':'Historical/stale report — not current');
        el('obs-metar-time').textContent=metar?'Report observation time: '+stamp(metar.at):'Report observation time: —';
        el('obs-metar-text').textContent=metar&&r.state==='recent'?metar.text:'—';
    };
    const ingest=(data,source)=>{sample=TitanObservation.snapshot(data,Date.now());sampleSource=source;retrievedAt=null;metar=null;metarError='';if(source==='proxy'){const t=TitanObservation.utc(data.retrieved_at_utc);if(!Number.isFinite(t)||t>Date.now()||Date.now()-t>120000||t<sample.at)throw Error('Invalid retrieval timestamp');retrievedAt=t;}if(data.metar_text&&data.metar_date_utc){try{metar=TitanObservation.report(data.metar_text,data.metar_date_utc,Math.min(Date.now(),sample.at));reportSource='AWOSnet snapshot';}catch(err){metarError='Embedded report rejected: '+err.message;}}};
    const poll=async()=>{
        if(!visible()||controller)return;
        clearTimeout(timer);timer=null;const token=++generation,abort=new AbortController();controller=abort;
        const deadline=setTimeout(()=>abort.abort(),9000);
        try{const response=await fetch(endpoint,{cache:'no-store',credentials:'omit',redirect:'error',signal:abort.signal});if(!response.ok)throw Error('Proxy unavailable');const data=await response.json();if(token!==generation||!visible())return;ingest(data,'proxy');sourceError='';}
        catch(_){if(token===generation){sample=null;metar=null;retrievedAt=null;sourceError='AWOSnet proxy unavailable — values withheld';}}
        finally{clearTimeout(deadline);if(token===generation){controller=null;render();if(visible())timer=setTimeout(poll,60000);}}
    };
    const pause=()=>{enabled=false;stop();render();};
    el('obs-refresh').disabled=!endpoint;el('obs-auto').disabled=!endpoint;
    el('obs-refresh').addEventListener('click',()=>{if(!endpoint)return;enabled=true;el('obs-auto').checked=true;stop();poll();});
    el('obs-auto').checked=enabled;el('obs-auto').addEventListener('change',()=>{enabled=el('obs-auto').checked;stop();render();if(visible())poll();});
    document.addEventListener('visibilitychange',()=>{stop();render();if(visible())poll();});
    window.addEventListener('pagehide',stop);window.addEventListener('pageshow',e=>{if(e.persisted&&visible())poll();});
    el('obs-file').addEventListener('change',async e=>{
        pause();el('obs-auto').checked=false;const version=++importVersion,f=e.target.files[0];sample=null;retrievedAt=null;sourceError='';metar=null;metarError='';reportSource='manually entered';render();
        if(!f)return;
        try{
            if(f.size>16384)throw Error('Snapshot exceeds 16 KB.');
            const data=JSON.parse(await f.text());
            if(version!==importVersion)return;
            ingest(data,'manually imported');
        }catch(err){if(version!==importVersion)return;sourceError='Import rejected: '+err.message;sample=null;}
        render();
    });
    el('obs-metar-date').value=new Date().toISOString().slice(0,10);
    el('obs-metar-apply').addEventListener('click',()=>{
        metarError='';metar=null;reportSource='manually entered';
        try{metar=TitanObservation.report(el('obs-metar-input').value,el('obs-metar-date').value,Date.now());}
        catch(err){metarError='Report rejected: '+err.message;}
        render();
    });
    el('obs-clear').addEventListener('click',()=>{pause();el('obs-auto').checked=false;++importVersion;sample=null;metar=null;retrievedAt=null;sourceError='';metarError='';el('obs-file').value='';el('obs-metar-input').value='';render();});
    render();setInterval(render,10000);if(visible())poll();
}
