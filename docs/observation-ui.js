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
function liveReport(data,now){
    if(!data||data.schema!=='titan-live-metar-v1'||data.station!=='WAWP'||data.source!=='NOAA AWC')throw Error('Invalid report source');
    if(data.status==='no_recent_report')return null;
    const observed=utc(data.observed_at_utc),received=utc(data.retrieved_at_utc);
    if(!Number.isFinite(observed)||!Number.isFinite(received)||received>now||received<observed||now-received>120000)throw Error('Invalid retrieval clock');
    const r=report(data.report_text,data.observed_at_utc.slice(0,10),now);
    if(r.at!==observed||age(r.at,now,60).state!=='recent')throw Error('Invalid or stale live report');
    return r;
}

// Conservative extraction of explicitly coded groups, not a full METAR decoder.
function reportGroups(raw){
    const empty={visibility:'—',weather:'—',sky:'—',qnh:'—',wind:'—',variable:'—'};
    if(typeof raw!=='string')return empty;
    const tokens=raw.trim().replace(/=$/,'').split(/\s+/);
    const wi=tokens.findIndex(t=>/^(?:\d{3}|VRB)\d{2,3}(?:G\d{2,3})?(?:KT|MPS|KMH)$/.test(t));
    if(wi<0)return empty;
    const out={...empty,wind:tokens[wi]};
    let i=wi+1;
    if(/^\d{3}V\d{3}$/.test(tokens[i]||''))out.variable=tokens[i++];
    else if(out.wind.startsWith('VRB'))out.variable='VRB';
    if(tokens[i]==='CAVOK'){out.visibility='≥ 10 km';out.weather='CAVOK';out.sky='CAVOK';i++;}
    else {
        if(/^\d{4}$/.test(tokens[i]||'')){out.visibility=tokens[i]==='9999'?'≥ 10 km':tokens[i]==='0000'?'< 50 m':Number(tokens[i])+' m';i++;}
        else if(/^\/{3,4}$/.test(tokens[i]||''))i++;
        const weather=[],sky=[];
        for(;i<tokens.length;i++){
            const t=tokens[i];
            if(/^(?:M?\d{2}|\/\/)\/(?:M?\d{2}|\/\/)$/.test(t)||/^(?:Q\d{4}|A\d{4}|NOSIG|TEMPO|BECMG|RMK|RE|WS)$/.test(t))break;
            if(/^(?:(?:FEW|SCT|BKN|OVC)(?:\d{3}|\/{3})(?:CB|TCU|\/{3})?|VV(?:\d{3}|\/{3})|NSC|NCD|SKC|CLR|\/{6})$/.test(t)){if(!/^\/+$/.test(t))sky.push(t);continue;}
            if(/^(?:[+-]|VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)+$/.test(t)||/^(?:VCTS|TS|VCSH|NSW)$/.test(t))weather.push(t);
        }
        out.weather=weather.join(' ')||'—';out.sky=sky.join(' ')||'—';
    }
    // Only the main report, never a TREND or remarks pressure.
    const main=tokens.slice(0,tokens.findIndex(t=>/^(?:NOSIG|TEMPO|BECMG|RMK)$/.test(t))<0?tokens.length:tokens.findIndex(t=>/^(?:NOSIG|TEMPO|BECMG|RMK)$/.test(t)));
    const q=main.filter(t=>/^Q\d{4}$/.test(t));
    if(q.length===1&&Number(q[0].slice(1))>=850&&Number(q[0].slice(1))<=1100)out.qnh=Number(q[0].slice(1))+' hPa';
    return out;
}
function windDisplay(values){
    const speed=values?.wind_speed_kt??null,dir=values?.wind_direction_deg??null;
    if(speed===null)return {speed:'—',bearing:'—',angle:null};
    if(speed===0)return {speed:'Calm',bearing:'—',angle:null};
    return {speed:String(speed)+' kt',bearing:dir===null?'—':String(Math.round(dir)).padStart(3,'0')+'°',angle:dir};
}
const api={utc,snapshot,report,age,liveReport,reportGroups,windDisplay};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.TitanObservation=api;
})(typeof globalThis!=='undefined'?globalThis:this);
function setupObservationPanel(){
    const el=id=>document.getElementById(id);
    if(!el('obs-file'))return;
    let sample=null,metar=null,importVersion=0,sourceError='',metarError='',reportSource='manually entered',sampleSource='manually imported',retrievedAt=null;
    let endpoint=null;try{const raw=document.querySelector('meta[name="titan-awos-proxy"]')?.content;if(raw){const url=new URL(raw,location.origin);const local=url.protocol==='http:'&&url.hostname==='127.0.0.1'&&url.origin===location.origin&&location.hostname==='127.0.0.1';if((url.protocol==='https:'||local)&&!url.username&&!url.password&&!url.search&&!url.hash&&url.pathname==='/awos')endpoint=url.href;}}catch(_){}
    let metarEndpoint=null;
    try{const raw=document.querySelector('meta[name="titan-metar-proxy"]')?.content;if(raw){const u=new URL(raw);if(u.protocol==='https:'&&!u.username&&!u.password&&!u.search&&!u.hash&&u.pathname==='/metar')metarEndpoint=u.href;}}catch(_){}
    let liveMetar=null,liveError='',metarEnabled=!!metarEndpoint,metarTimer=null,metarController=null,metarGeneration=0;
    const metarVisible=()=>metarEnabled&&document.visibilityState==='visible';
    const stopMetar=()=>{metarGeneration++;clearTimeout(metarTimer);metarTimer=null;metarController?.abort();metarController=null;};
    let enabled=!!endpoint, timer=null,controller=null, generation=0;
    const visible=()=>document.visibilityState==='visible'&&enabled;
    const stop=()=>{generation++;clearTimeout(timer);timer=null;controller?.abort();controller=null;};
    const fields=[['wind_speed_kt',' kt'],['wind_direction_deg','°'],['gust_kt',' kt'],['temperature_c',' °C'],['dewpoint_c',' °C'],['qnh_hpa',' hPa'],['altimeter_setting_hpa',' hPa'],['temperature_10min_c',' °C'],['dewpoint_10min_c',' °C'],['relative_humidity_pct','%'],['rain_current_hour_mm',' mm'],['solar_wm2',' W/m²'],['density_altitude_ft',' ft']];
    const stamp=t=>new Date(t).toISOString().slice(0,19)+'Z';
    const render=()=>{
        const now=Date.now(),a=sample?TitanObservation.age(sample.at,now,5):null;
        const autoRecent=liveMetar&&TitanObservation.age(liveMetar.at,now,60).state==='recent';
        const useAuto=autoRecent&&(!metar||liveMetar.at>metar.at);
        const current=useAuto?liveMetar:metar,currentSource=useAuto?'NOAA AWC relay':reportSource,r=current?TitanObservation.age(current.at,now,60):null;
        el('obs-status').textContent=sourceError||(!sample?(endpoint?'Not connected — awaiting verified snapshot':'Not connected — live AWOSnet is not configured'):a.state==='recent'?'Recent '+sampleSource+' snapshot · '+a.minutes+' min old':'Stale or invalid snapshot — values withheld');
        el('obs-time').textContent=sample?'Confirmed source display clock: '+stamp(sample.at):'Sensor observation time: —';
        el('obs-retrieved').textContent=retrievedAt!==null?'Retrieved at: '+stamp(retrievedAt):'';
        el('obs-poll-status').textContent=!endpoint?'Live feed unavailable in TITAN: AWOSnet serves HTTP; a permitted HTTPS relay is required. Open AWOSnet directly or import a local snapshot.':!enabled?'Automatic retrieval paused':document.visibilityState!=='visible'?'Paused while browser tab is hidden':'Automatic retrieval every 60 s while visible';
        el('obs-epoch-note').textContent=sample&&sample.epochOffset!==null?'Raw XML epoch offset from display clock: '+sample.epochOffset+' s. Not used as UTC observation time.':'';
        for(const [key,unit] of fields){
            const v=sample&&a.state==='recent'?sample.values[key]:null;
            el('obs-'+key).textContent=v===null?'—':String(v)+unit;
        }
        const conflict=autoRecent&&metar&&liveMetar.at===metar.at&&liveMetar.text!==metar.text;
        el('obs-metar-status').textContent=conflict?'Same-time report conflict — check original providers':(!current?(metarError||'No manually entered report'):r.state==='recent'?'Recent '+currentSource+' report · '+r.minutes+' min old':'Historical/stale report — not current');
        el('obs-metar-time').textContent=current?'Report observation time: '+stamp(current.at):'Report observation time: —';
        el('obs-metar-text').textContent=current&&r.state==='recent'&&!conflict?current.text:'—';
        // All visual fields share the existing freshness gate; report groups
        // retain their own timestamp and are never promoted to sensor values.
        const recent=sample&&a.state==='recent', validReport=current&&r.state==='recent'&&!conflict;
        const display=TitanObservation.windDisplay(recent?sample.values:null);
        const groups=TitanObservation.reportGroups(validReport?current.text:null);
        const put=(id,value)=>{const e=el(id);if(e)e.textContent=value;};
        put('obs-dial-speed',display.speed);put('obs-dial-bearing',display.bearing);
        const needle=el('obs-dial-needle');if(needle){needle.style.display=display.angle===null?'none':'';needle.setAttribute('transform','rotate('+(display.angle??0)+' 120 120)');}
        put('obs-report-visibility',groups.visibility);put('obs-report-weather',groups.weather);put('obs-report-sky',groups.sky);
        put('obs-report-qnh',groups.qnh);put('obs-report-variable',groups.variable);put('obs-report-wind',groups.wind);
        put('obs-report-group-time',validReport?'Report groups · '+stamp(current.at):'Report groups · unavailable');
        const fresh=el('obs-live-indicator');if(fresh){fresh.dataset.state=recent?'recent':'unavailable';fresh.textContent=recent?'Recent snapshot':'No current snapshot';}
        const state=el('obs-metar-live-status');
        if(state)state.textContent=!metarEndpoint?'Independent METAR relay not configured.':!metarEnabled?'Independent METAR retrieval paused.':document.visibilityState!=='visible'?'Independent METAR retrieval paused while hidden.':liveError|| (autoRecent?'Independent NOAA AWC report received · '+stamp(liveMetar.at):'Awaiting a recent WAWP report.');
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
    const pollMetar=async()=>{
        if(!metarVisible()||metarController)return;
        clearTimeout(metarTimer);const token=++metarGeneration,abort=new AbortController();metarController=abort;
        const deadline=setTimeout(()=>abort.abort(),9000);
        try{
            const response=await fetch(metarEndpoint,{cache:'no-store',credentials:'omit',redirect:'error',signal:abort.signal});
            if(!response.ok)throw Error('relay unavailable');
            const data=await response.json();if(token!==metarGeneration||!metarVisible())return;
            liveMetar=TitanObservation.liveReport(data,Date.now());liveError=liveMetar?'':'No recent WAWP report from NOAA AWC.';
        }catch(_){if(token===metarGeneration){liveMetar=null;liveError='Independent METAR relay unavailable or invalid — live report withheld.';}}
        finally{clearTimeout(deadline);if(token===metarGeneration){metarController=null;render();if(metarVisible())metarTimer=setTimeout(pollMetar,300000);}}
    };
    const pause=()=>{enabled=false;stop();render();};
    const auto=el('obs-metar-auto');
    if(auto){auto.checked=metarEnabled;auto.disabled=!metarEndpoint;el('obs-metar-live-controls').hidden=!metarEndpoint;
        auto.addEventListener('change',()=>{metarEnabled=auto.checked;stopMetar();if(!metarEnabled)liveMetar=null;render();if(metarVisible())pollMetar();});
    }
    el('obs-live-controls').hidden=!endpoint;el('obs-refresh').disabled=!endpoint;el('obs-auto').disabled=!endpoint;
    el('obs-refresh').addEventListener('click',()=>{if(!endpoint)return;++importVersion;enabled=true;el('obs-auto').checked=true;stop();poll();});
    el('obs-auto').checked=enabled;el('obs-auto').addEventListener('change',()=>{enabled=el('obs-auto').checked;if(enabled)++importVersion;stop();render();if(visible())poll();});
    document.addEventListener('visibilitychange',()=>{stop();stopMetar();render();if(visible())poll();if(metarVisible())pollMetar();});
    window.addEventListener('pagehide',()=>{stop();stopMetar();});window.addEventListener('pageshow',e=>{if(e.persisted){if(visible())poll();if(metarVisible())pollMetar();}});
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
    el('obs-clear').addEventListener('click',()=>{metarEnabled=false;stopMetar();liveMetar=null;liveError='';if(auto)auto.checked=false;pause();el('obs-auto').checked=false;++importVersion;sample=null;metar=null;retrievedAt=null;sourceError='';metarError='';el('obs-file').value='';el('obs-metar-input').value='';render();});
    render();setInterval(render,10000);if(visible())poll();if(metarVisible())pollMetar();
}
