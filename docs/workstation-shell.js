/* Navigation, local notes and read-only display of existing gated point rows. */
const WORKSPACE_GROUPS=[
 {primary:'tab-intel',views:[['tab-intel','Briefing']]},
 {primary:'tab-observations',views:[['tab-observations','Observations']]},
 {primary:'tab-meteogram',views:[['tab-meteogram','Meteogram'],['tab-spread','Model disagreement'],['tab-matrix','Hourly matrix'],['tab-models','Individual model']]},
 {primary:'tab-spatial',views:[['tab-spatial','Spatial forecast'],['tab-regional','Atmospheric analysis']]},
 {primary:'tab-climatology',views:[['tab-climatology','Climatology'],['tab-verify','Historical verification'],['tab-persistency','Observation archive']]},
 {primary:'tab-health',views:[['tab-health','System health'],['tab-weighter','Archived weights']]}
];
function updateWorkspaceNavigation(tabId){
 const group=WORKSPACE_GROUPS.find(g=>g.views.some(v=>v[0]===tabId)),sub=document.getElementById('workspace-subnav');
 if(!group||!sub)return;
 document.body.classList.toggle('point-active',group.primary==='tab-meteogram');
 document.body.classList.toggle('utility-active',group.primary==='tab-health');
 document.body.classList.toggle('reference-active',group.primary==='tab-climatology');
 const primary=document.querySelector('.nav-links li[data-tab="'+group.primary+'"]');
 if(primary){primary.classList.add('active');primary.setAttribute('aria-current','page');}
 sub.replaceChildren();sub.hidden=group.views.length<2||tabId==='tab-spatial';
 if(!sub.hidden)for(const [id,label] of group.views){const b=document.createElement('button');b.type='button';b.dataset.workspaceView=id;b.textContent=label;b.setAttribute('aria-pressed',String(id===tabId));b.onclick=()=>switchTab(id);sub.append(b);}
}
function renderBriefHourly(rows,manifest){
 const root=document.getElementById('brief-hourly');if(!root)return;root.replaceChildren();
 if(!manifest||!previewIsCurrent(manifest)||!rows.length){root.textContent='Current point guidance unavailable. Review the source status and observations.';return;}
 const wrap=document.createElement('div');wrap.className='preview-scroll';const table=document.createElement('table');table.className='brief-table';
 const first=rows.slice(0,8),head=table.createTHead().insertRow();previewCell(head,'th','Parameter');
 for(const row of first){const t=previewValidEpoch(row.Datetime);const cell=previewCell(head,'th',TitanPreview.label(t,'UTC',true)+'Z');const local=document.createElement('small');local.textContent=TitanPreview.label(t,'WITA',true)+' WITA';cell.append(local);}
 for(const [name,key] of [['Wind (kt)','Wind'],['Gust (kt)','Gust'],['Rain (mm/output h)','Rain'],['Temp (°C)','Temperature'],['Td (°C)','Dewpoint'],['Models (row count)','Model_Count']]){const r=table.createTBody().insertRow();previewCell(r,'th',name);for(const row of first){const v=TitanPreview.numeric(row[key]);previewCell(r,'td',v===null?'—':key==='Model_Count'?String(v):v.toFixed(1));}}
 wrap.append(table);root.append(wrap);
}
function setupWorkstationShell(){
 const el=id=>document.getElementById(id),dialog=el('handover-dialog'),notes=document.querySelector('.shift-notes'),placeholder=document.createComment('handover original location');
 notes.before(placeholder);
 el('global-handover').onclick=()=>{el('handover-mount').append(notes);notes.open=true;dialog.showModal();};
 el('handover-close').onclick=()=>dialog.close();
 dialog.addEventListener('close',()=>placeholder.after(notes));
 const read=id=>el(id)?.textContent?.trim()||'—';
 function sync(){
  const report=read('obs-metar-text'),metarStatus=read('obs-metar-status'),awos=read('obs-status');
  el('aero-metar').textContent=report==='—'?'METAR: '+metarStatus:report;
  el('aero-metar').title=metarStatus+' · '+read('obs-metar-time');
  const wind=read('obs-wind_speed_kt'),qnh=read('obs-qnh_hpa');
  el('aero-awos').textContent=wind==='—'?'AWOS: '+awos:'AWOS '+read('obs-wind_direction_deg')+' / '+wind+' · QNH '+qnh;
  el('aero-awos').title=awos+' · '+read('obs-time');
  el('brief-source-status').textContent=metarStatus+' · '+awos;
 }
 const watch=new MutationObserver(sync);for(const id of ['obs-metar-text','obs-metar-status','obs-status','obs-wind_speed_kt','obs-wind_direction_deg','obs-qnh_hpa','obs-time','obs-metar-time'])if(el(id))watch.observe(el(id),{childList:true,characterData:true,subtree:true});
 function clock(){const now=Date.now();el('aero-clock').textContent=TitanPreview.label(now,'UTC',true)+'Z · '+TitanPreview.label(now,'WITA',true)+' WITA';}
 sync();clock();setInterval(clock,30000);
 updateWorkspaceNavigation(document.querySelector('.tab-pane.active')?.id||'tab-intel');
}

function renderTerminalTimeline(rows,models,manifest){
 const root=document.getElementById('terminal-timeline');if(!root)return;root.replaceChildren();
 if(!manifest||!previewIsCurrent(manifest)||!rows.length){root.textContent='Current point guidance unavailable.';return;}
 const specs=[['Wind','Wind','Wind Speed','kt'],['Gust','Gust','Wind Gust','kt'],['Rain','Rain','Rainfall','mm/output h'],['Temperature','Temperature','Temperature','°C'],['Dewpoint','Dewpoint','Dewpoint','°C'],['Pressure','Pressure','Pressure','hPa']];
 const wrap=document.createElement('div');wrap.className='preview-scroll terminal-scroll';wrap.tabIndex=0;wrap.setAttribute('aria-label','Time-aligned raw model rows; scroll horizontally');
 const table=document.createElement('table');table.className='terminal-table';const head=table.createTHead().insertRow();previewCell(head,'th','Valid UTC / WITA');
 const body=table.createTBody(),readout=document.createElement('div');readout.className='terminal-readout';const details=document.createElement('p');details.className='section-note';const jump=document.createElement('button');jump.type='button';jump.textContent='Open spatial rain at selected valid time';let chosen=null;
 const num=v=>v===null?'—':v.toFixed(1);
 function extent(row,key){const vals=Object.values(models?.[key]||{}).map(m=>TitanPreview.numeric(m?.[row.Datetime])).filter(v=>v!==null);return vals.length?{min:Math.min(...vals),max:Math.max(...vals),n:vals.length}:null;}
 function select(i){chosen=rows[i];table.querySelectorAll('[data-hour]').forEach(e=>e.classList.toggle('chosen',Number(e.dataset.hour)===i));details.textContent=TitanPreview.label(previewValidEpoch(chosen.Datetime),'UTC')+' / '+TitanPreview.label(previewValidEpoch(chosen.Datetime),'WITA')+' · '+specs.map(([label,key,modelKey,unit])=>{const v=TitanPreview.numeric(chosen[key]),r=extent(chosen,modelKey);return label+': '+num(v)+' '+unit+(r?' (range '+num(r.min)+'–'+num(r.max)+', n='+r.n+')':' (individual range unavailable)');}).join(' | ');}
 rows.forEach((row,i)=>{const t=previewValidEpoch(row.Datetime),th=previewCell(head,'th','');th.dataset.hour=i;const b=document.createElement('button');b.type='button';b.textContent=TitanPreview.label(t,'UTC',true)+'Z';b.onclick=()=>select(i);const local=document.createElement('small');local.textContent=TitanPreview.label(t,'WITA',true)+' WITA';b.append(local);th.append(b);});
 for(const [label,key,modelKey,unit] of specs){const tr=body.insertRow();previewCell(tr,'th',label+' ('+unit+')');rows.forEach((row,i)=>{const cell=previewCell(tr,'td',num(TitanPreview.numeric(row[key])));cell.dataset.hour=i;const r=extent(row,modelKey),range=document.createElement('small');range.textContent=r?num(r.min)+'–'+num(r.max)+' · n='+r.n:'range —';cell.append(range);});}
 jump.onclick=()=>{if(chosen&&previewIsCurrent(manifest))openSpatialDiagnostic('rain','kolaka',new Date(previewValidEpoch(chosen.Datetime)).toISOString());};readout.append(details,jump);wrap.append(table);root.append(wrap,readout);select(0);
}

function setPointAvailability(available,manifest){
 const summary=document.getElementById('point-source-summary');
 if(summary)summary.textContent=available?'Point preview: '+Object.keys(manifest.models||{}).length+' eligible model collections · expires '+manifest.expires_at_utc+' · collection times are not provider initialization times.':'No eligible current point collection. Spatial guidance has a separate freshness policy. Check the point-refresh workflow.';
 for(const id of ['tab-meteogram','tab-spread','tab-matrix','tab-models']){
  const pane=document.getElementById(id);if(!pane)continue;
  pane.dataset.pointState=available?'available':'unavailable';
  const note=pane.querySelector('.point-empty');
  if(note){note.hidden=available;note.textContent=available?'':'Current point forecast unavailable — no eligible unexpired model data. No stale values are displayed. Check source collection status or use the separately labeled spatial research preview.';}
 }
}
function setIndividualAvailability(available){
 for(const id of ['tab-spread','tab-models']){
  const pane=document.getElementById(id);if(!pane)continue;
  pane.dataset.pointState=available?'available':'unavailable';
  const note=pane.querySelector('.point-empty');if(note){note.hidden=available;note.textContent=available?'':'Matching individual-model payload unavailable. No model ranges or individual values are displayed.';}
 }
}
