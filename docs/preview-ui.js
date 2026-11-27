/* TITAN preview presentation helpers. No forecast generation or data acquisition. */
(function(root) {
'use strict';
const HOUR = 3600000;
function epoch(value) {
    const s = String(value ?? '').trim().replace(' ', 'T');
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(s)) return NaN;
    const t = Date.parse(s + '+08:00');
    if (!Number.isFinite(t)) return NaN;
    return new Date(t + 8*HOUR).toISOString().slice(0,19) === (s.length===16?s+':00':s) ? t : NaN;
}
function label(t, zone='WITA', short=false) {
    if (t===null || t===undefined || !Number.isFinite(Number(t))) return '—';
    const d = new Date(Number(t)+(zone==='UTC'?0:8*HOUR));
    if (!Number.isFinite(d.getTime())) return '—';
    const s=d.toISOString();
    return short?s.slice(5,10)+' '+s.slice(11,16):s.slice(0,10)+' '+s.slice(11,16)+' '+zone;
}
function settings(search='') {
    const p=new URLSearchParams(search);
    const hours=Number(p.get('hours'));
    return {zone:p.get('tz')==='UTC'?'UTC':'WITA',hours:[12,24,48,96].includes(hours)?hours:24};
}
function windowRows(rows, now, hours) {
    const start=Math.floor(now/HOUR)*HOUR;
    return rows.filter(row=>{const t=epoch(row.Datetime);return t>=start && t<start+hours*HOUR;})
        .sort((a,b)=>epoch(a.Datetime)-epoch(b.Datetime));
}
function numeric(v) {return typeof v==='number' && Number.isFinite(v)?v:null;}
function rainClass(v) {
    v=numeric(v);
    return v===null||v<0?'missing':v===0?'zero':v<1?'r1':v<3?'r2':v<6?'r3':v<12?'r4':'r5';
}
function cumulative(rows, key='Rain') {
    let sum=0,broken=false,last=null;
    return rows.map(row=>{
        const t=epoch(row.Datetime),v=numeric(row[key]);
        if(!Number.isFinite(t)||v===null||v<0||(last!==null&&t-last!==HOUR)) broken=true;
        last=t;
        if(broken)return [t,null];
        sum+=v;return [t,Math.round(sum*1000)/1000];
    });
}
function barbParts(speed) {
    if(numeric(speed)===null||speed<0||speed>250)return null;
    let rounded=Math.round(speed/5)*5;
    const flags=Math.floor(rounded/50);rounded%=50;
    return {flags,full:Math.floor(rounded/10),half:rounded%10===5,ring:speed<2.5};
}
const api={epoch,label,settings,windowRows,numeric,rainClass,cumulative,barbParts};
if(typeof module!=='undefined'&&module.exports)module.exports=api;
else root.TitanPreview=api;
})(typeof globalThis!=='undefined'?globalThis:this);

function titanPreviewSettings(){return TitanPreview.settings(window.location.search);}
function titanPreviewLabel(t,short=false){return TitanPreview.label(t,titanPreviewSettings().zone,short);}
function setupPreviewControls(){
    const zone=document.getElementById('preview-zone'),hours=document.getElementById('preview-hours');
    if(!zone||!hours)return;
    const s=titanPreviewSettings();zone.value=s.zone;hours.value=String(s.hours);
    const update=()=>{const url=new URL(window.location.href);url.searchParams.set('tz',zone.value);url.searchParams.set('hours',hours.value);window.location.assign(url.href);};
    zone.addEventListener('change',update);hours.addEventListener('change',update);
    document.querySelectorAll('[data-preview-zone]').forEach(el=>el.textContent=s.zone+(s.zone==='WITA'?' (UTC+8)':''));
}
function previewCell(row,tag,value,cls){
    const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;row.appendChild(el);return el;
}
function renderRainHeatmap(models,times){
    const root=document.getElementById('spread-rain');if(!root)return;
    root.replaceChildren();
    const title=document.createElement('h3');title.textContent='Individual model precipitation (mm per output hour)';root.appendChild(title);
    const note=document.createElement('p');note.className='section-note';note.textContent='Alternative forecasts, never added together. Native model time steps may differ. — = unavailable; 0 = dry model value. Descriptive color bins, not warning thresholds. Scroll horizontally to see later hours; model names stay fixed.';root.appendChild(note);
    const entries=Object.entries(models?.Rainfall||{});if(!entries.length||!times.length){root.append('No matching model values.');return;}
    const wrap=document.createElement('div');wrap.className='preview-scroll';wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Individual model rainfall — scroll horizontally');const table=document.createElement('table');table.className='rain-model-table';
    const head=table.createTHead().insertRow();previewCell(head,'th','Model');
    times.forEach(t=>previewCell(head,'th',titanPreviewLabel(TitanPreview.epoch(t),true)));
    const body=table.createTBody();
    const labels={CMA_GRAPES_GLOBAL:'CMA GRAPES',ECMWF_HRES:'IFS 0.25°',ECMWF_IFS_9KM:'IFS 9 km',GEM_GLOBAL:'GEM',GFS_GLOBAL:'GFS',ICON_SEAMLESS:'ICON',JMA_GSM:'JMA GSM',METEOFRANCE_ARPEGE_WORLD:'ARPEGE',UKMO_GLOBAL_10KM:'UKMO 10 km'};
    entries.forEach(([name,values])=>{const row=body.insertRow();const th=previewCell(row,'th',labels[name]||name);th.scope='row';th.title=name;
        times.forEach(t=>{const v=TitanPreview.numeric(values?.[t]);const cls=TitanPreview.rainClass(v);const cell=previewCell(row,'td',cls==='missing'?'—':v.toFixed(1),'rain-'+cls);cell.title=name+' | '+titanPreviewLabel(TitanPreview.epoch(t))+' | '+(cls==='missing'?'Unavailable':v+' mm');});
    });
    wrap.appendChild(table);root.appendChild(wrap);
    const legend=document.createElement('div');legend.className='rain-legend';[['zero','0'],['r1','>0–<1'],['r2','1–<3'],['r3','3–<6'],['r4','6–<12'],['r5','≥12'],['missing','Missing']].forEach(([c,v])=>previewCell(legend,'span',v,'rain-'+c));root.appendChild(legend);
}
function renderWindBarbs(rows){
    const root=document.getElementById('chart-wind-dir');if(!root)return;root.replaceChildren();
    const title=document.createElement('h3');title.textContent='Model wind direction and speed';root.appendChild(title);
    const note=document.createElement('p');note.className='section-note';note.textContent='Barbs point toward the direction wind comes from; symbols round speed to 5 kt. A ring means <2.5 kt, not necessarily exactly calm. Raw model values, not runway observations.';root.appendChild(note);
    const wrap=document.createElement('div');wrap.className='preview-scroll';const strip=document.createElement('div');strip.className='wind-barb-strip';
    const ns='http://www.w3.org/2000/svg';
    const svgEl=(tag,attrs)=>{const el=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));return el;};
    for(const [i,row] of rows.entries()){
        const cell=document.createElement('button');cell.type='button';cell.className='wind-barb-cell';cell.dataset.cursorHour=String(i);cell.onclick=()=>selectTerminalHour(i);
        const speed=TitanPreview.numeric(row.Wind),dir=TitanPreview.numeric(row['Wind Dir.']);const parts=TitanPreview.barbParts(speed);
        const svg=svgEl('svg',{viewBox:'0 0 78 78',width:'64',height:'64','aria-hidden':'true'});
        const validDir=dir!==null&&dir>=0&&dir<=360;
        if(parts&&(parts.ring||validDir)){
            if(parts.ring)svg.appendChild(svgEl('circle',{cx:39,cy:39,r:7,fill:'none',stroke:'currentColor','stroke-width':2}));
            else {
                const group=svgEl('g',{transform:'translate(39 39) rotate('+dir+')',stroke:'currentColor','stroke-width':1.8,fill:'none','stroke-linecap':'round'});
                group.appendChild(svgEl('line',{x1:0,y1:0,x2:0,y2:-31}));
                let y=-31;
                for(let i=0;i<parts.flags;i++){group.appendChild(svgEl('path',{d:'M 0 '+y+' L 12 '+(y+6)+' L 0 '+(y+11)+' Z',fill:'currentColor'}));y+=11;}
                for(let i=0;i<parts.full;i++){group.appendChild(svgEl('line',{x1:0,y1:y,x2:12,y2:y+6}));y+=5;}
                if(parts.half)group.appendChild(svgEl('line',{x1:0,y1:y,x2:6,y2:y+3}));
                svg.appendChild(group);svg.appendChild(svgEl('circle',{cx:39,cy:39,r:2,fill:'currentColor'}));
            }
            cell.appendChild(svg);
        }else previewCell(cell,'span','—','wind-missing');
        previewCell(cell,'strong',speed===null?'—':speed.toFixed(1)+' kt');
        previewCell(cell,'span',validDir?String(Math.round(dir)).padStart(3,'0')+'°':'—');
        previewCell(cell,'small',titanPreviewLabel(TitanPreview.epoch(row.Datetime),true));strip.appendChild(cell);
    }
    wrap.appendChild(strip);root.appendChild(wrap);if(typeof updateTerminalCrosshairs==='function')updateTerminalCrosshairs();
}
