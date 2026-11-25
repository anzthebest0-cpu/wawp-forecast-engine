/* Historical presentation only. No score calculation, ranking or operational validation. */
(function(root){
'use strict';
const numeric=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
const mean=arr=>{const values=(arr||[]).map(numeric).filter(v=>v!==null);return values.length?values.reduce((a,b)=>a+b,0)/values.length:null;};
const peak=arr=>{let index=null,max=-Infinity;(arr||[]).forEach((v,i)=>{if(numeric(v)!==null&&v>max){index=i;max=v;}});return index;};
const metricText=v=>numeric(v)===null?'—':v.toFixed(3);
const significance=p=>numeric(p)===null||p<0||p>1?'P-value unavailable; no conclusion.':p<0.05?'Equal-loss null rejected at nominal 5% level. Direction of improvement is not established by p-value alone.':'Equal-loss null not rejected at nominal 5% level; this does not establish equivalence.';
const api={numeric,mean,peak,metricText,significance};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.TitanResearch=api;
})(typeof globalThis!=='undefined'?globalThis:this);
function renderHistoricalScorecard(metrics){
    const root=document.getElementById('historical-scorecard');if(!root)return;
    root.replaceChildren();
    const table=document.createElement('table');table.className='historical-score-table';
    const append=(row,tag,v)=>{const cell=document.createElement(tag);cell.textContent=v;row.appendChild(cell);};
    const head=table.createTHead().insertRow();['Parameter','Model / method','MAE','RMSE','Bias','HSS','CSI','Paired N'].forEach(x=>append(head,'th',x));
    const body=table.createTBody();let count=0;
    for(const [parameter,unit] of [['Temperature','°C'],['Dewpoint','°C'],['Pressure','hPa'],['Wind Speed','kt'],['Rainfall','mm']]){
        for(const [model,m] of Object.entries(metrics?.[parameter]?.overall||{}).sort(([a],[b])=>a.localeCompare(b))){
            if(!m)continue;const row=body.insertRow();count++;
            append(row,'th',parameter+' ('+unit+')');append(row,'td',model);
            ['MAE','RMSE','Bias','HSS','CSI'].forEach(k=>append(row,'td',TitanResearch.metricText(m[k])));
            append(row,'td','Not exported');
        }
    }
    if(count)root.append(table);else root.textContent='No archived scores for this lookback.';
}
function renderDirectionTable(clim){
    const root=document.getElementById('chart-wind-rose');if(!root)return;root.replaceChildren();
    const sectors=clim.wind_rose?.sectors||[],rows=clim.wind_rose?.all||[];
    if(!rows.length||!sectors.length){root.textContent='All-condition wind direction distribution unavailable.';return;}
    const table=document.createElement('table');table.className='historical-score-table';
    const cell=(row,tag,v)=>{const e=document.createElement(tag);e.textContent=v;row.appendChild(e);};
    const head=table.createTHead().insertRow();['Sector','Frequency (%)','Mean wind (kt)','Gust event (%)'].forEach(v=>cell(head,'th',v));
    const body=table.createTBody();sectors.forEach((sector,i)=>{const r=body.insertRow(),d=rows[i]||{};cell(r,'th',sector);['freq_pct','mean_speed_kt','gust_event_pct'].forEach(k=>cell(r,'td',TitanResearch.numeric(d[k])===null?'—':d[k].toFixed(2)));});
    root.append(table);
}
