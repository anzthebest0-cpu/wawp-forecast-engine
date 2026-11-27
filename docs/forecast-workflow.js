/* Forecaster review tools. No scoring, forecast calculation, or operational promotion. */
(function(root){
'use strict';
const steps=[
 {id:'observe',title:'1 · Observe',desc:'METAR, AWOS, satellite, radar, lightning',tab:'tab-observations'},
 {id:'point',title:'2 · Airport guidance',desc:'Raw model timing and disagreement',tab:'tab-meteogram'},
 {id:'spatial',title:'3 · Compare maps',desc:'Native model cycles and exact forecast windows',tab:'tab-spatial'},
 {id:'diagnose',title:'4 · Diagnose if needed',desc:'Global drivers, circulation, instability and moisture',tab:'tab-regional'},
 {id:'verify',title:'5 · Review limits',desc:'Verification readiness and uncertainty before a decision',tab:'tab-verify'}
];
const p=(id,label)=>({id,label});
const diagnostics=[
 {scale:'synoptic',domain:'regional',title:'Pressure and low-level circulation',prompt:'Locate pressure gradients, troughs and wind confluence. MSLP products are model-specific; inspect valid time.',products:[p('mslp','MSLP'),p('PMSL','ICON MSLP'),p('wind925','925 hPa wind'),p('wind850','850 hPa wind'),p('z500','500 hPa height')]},
 {scale:'synoptic',domain:'regional',title:'Moisture supply and transport',prompt:'Compare precipitable water, low-level humidity and moisture flux. MFC sign, native grids and terrain masking matter.',products:[p('pwv','PWV'),p('TQV','ICON TQV'),p('rh850','850 hPa RH'),p('rh700','700 hPa RH'),p('flux925','925 hPa moisture flux'),p('mfc925','925 hPa MFC'),p('mfc850','850 hPa MFC')]},
 {scale:'meso',domain:'sulawesi',title:'Instability and convective inhibition',prompt:'Compare model definitions; CAPE alone does not prove convection. These are model forecasts, not soundings.',products:[p('cape','CAPE'),p('CAPE_ML','ICON ML CAPE'),p('cin','CIN'),p('mucape','MU CAPE'),p('ki','K index'),p('li','Lifted index'),p('si','Showalter index')]},
 {scale:'meso',domain:'sulawesi',title:'Vertical structure and simulated echoes',prompt:'Assess 850–200 hPa shear and freezing height. Reflectivity is simulated, not observed radar. Shear is not aviation LLWS.',products:[p('shear','Deep-layer shear'),p('freeze','Freezing height'),p('refc','Simulated reflectivity')]},
 {scale:'local',domain:'kolaka',title:'Rain and wind impact guidance',prompt:'Compare identical rain accumulation windows and gust definitions; verify with observations. No warning is generated.',products:[p('rain','Accumulated rain'),p('gust_1h','Hourly maximum gust'),p('gust_instant','Instantaneous gust')]}
];
const api={steps,diagnostics};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.TitanWorkflow=api;
})(typeof globalThis!=='undefined'?globalThis:this);
function setupForecastWorkflow(){
 const el=id=>document.getElementById(id),root=el('workflow-steps');if(!root)return;
 const reviewed=new Set(),fields=['period','evidence','drivers','models','uncertainty'];
 const text=(tag,value,cls)=>{const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;};
 const update=()=>el('workflow-count').textContent=reviewed.size+' / '+TitanWorkflow.steps.length+' reviewed';
 for(const s of TitanWorkflow.steps){const card=text('article','','workflow-step');card.append(text('h4',s.title),text('p',s.desc));const b=text('button','Open workspace');b.type='button';b.dataset.workflowStep=s.id;b.onclick=()=>switchTab(s.tab);const l=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.dataset.review=s.id;c.onchange=()=>{c.checked?reviewed.add(s.id):reviewed.delete(s.id);update();};l.append(c,document.createTextNode('Manually reviewed'));card.append(b,l);root.append(card);}
 el('shift-reset').onclick=()=>{reviewed.clear();root.querySelectorAll('input').forEach(x=>x.checked=false);fields.forEach(k=>el('shift-'+k).value='');update();el('shift-note-status').textContent='Review and notes cleared.';};
 el('shift-export').onclick=()=>{const note=['WAWP / TITAN — manual review notes (not an issued forecast)','Exported UTC: '+new Date().toISOString(),'Reviewed: '+TitanWorkflow.steps.filter(s=>reviewed.has(s.id)).map(s=>s.title).join(', '),...fields.map(k=>'\n'+k.toUpperCase()+':\n'+el('shift-'+k).value),'\nAutomated TAF withheld. Verify sources and validity independently.'].join('\n');const url=URL.createObjectURL(new Blob([note],{type:'text/plain;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='WAWP-shift-review-'+new Date().toISOString().slice(0,10)+'.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);el('shift-note-status').textContent='Local text export requested; nothing was uploaded.';};
 const stats=(target,items)=>{const grid=text('div','','evidence-status-grid');for(const [name,value] of items){const card=text('div','','evidence-stat');card.append(text('span',name),text('strong',value==null?'Unavailable':String(value)));grid.append(card);}target.append(grid);};
 const run=(target,name,r)=>{const row=text('div','','evidence-run');row.append(text('span',name));if(r&&Number.isSafeInteger(r.id)&&r.id>0){const a=text('a',r.status+(r.conclusion?' · '+r.conclusion:''));a.href='https://github.com/anzthebest0-cpu/wawp-forecast-engine/actions/runs/'+r.id;a.target='_blank';a.rel='noopener noreferrer';row.append(a);}else row.append(text('span','Not observed'));target.append(row);};
 async function status(){
  const health=el('workflow-health'),research=el('prospective-readiness');
  try{const response=await fetch('data/workflow-readiness.json',{cache:'no-store'});if(!response.ok)throw Error('unavailable');const data=await response.json();if(data.schema!==1)throw Error('schema');
   health.replaceChildren();research.replaceChildren();
   health.append(text('p','Evidence captured: '+data.as_of_utc+' · publication-time snapshot','section-note'));
   run(health,'Point-model refresh',data.runs.point_refresh);run(health,'Latest spatial refresh',data.runs.rolling);run(health,'Scheduled spatial refresh',data.runs.scheduled);run(health,'Numerical archive',data.runs.archive);run(health,'Numerical restore proof (mode/result must be inspected)',data.runs.restore);run(health,'Previous public deployment',data.runs.publication);run(health,'Prospective shadow research',data.runs.shadow);
   health.append(text('p','A successful workflow is not necessarily an advancing forecast or complete numerical archive. Inspect linked reports. Permanent Drive recovery and restore require separate proof.','section-note'));
   const s=data.shadow;if(!s){research.append(text('p','Counts-only artifact unavailable. Current skill cannot be established.','section-note'));return;}
   research.append(text('p','Research initialization: '+s.initialization_utc+' · source run '+s.run,'section-note'));
   const observed=Date.parse(s.latest_training_observation_utc||'');const age=(Date.now()-observed)/3600000;
   research.append(text('p',Number.isFinite(age)&&age>=0?'Latest training observation in this immutable research run: '+TitanPreview.label(observed,'UTC')+' / '+TitanPreview.label(observed,'WITA')+' · '+age.toFixed(1)+' h before browser time. This is not live AWOS status.':'Latest training observation time unavailable for this research run.','section-note'));
   if(!s.awos_freshness_gate_passed)research.append(text('p','Training freshness gate did not pass at issuance. Update the authorized monthly hourly AWOS source; rerun research without changing already issued forecasts. Missing observations must remain pending.','section-note'));
   stats(research,[['Source models',s.source_models],['Raw rows (current cycle)',s.raw_rows],['Candidate rows (current cycle)',s.candidate_rows],['AWOS freshness gate',s.awos_freshness_gate_passed?'Passed':'Not passed'],['Pending / unobserved rows',s.pending_or_unobserved_rows],['Scored groups',s.scored_groups],['Verified integrated vectors',s.integrated?.verified_vectors],['All-fits active days',s.integrated?.distinct_all_fits_active_days],['All-fits active wet times',s.integrated?.distinct_all_fits_active_wet_times]]);
   research.append(text('p','Review minima: 30 distinct verified days and 20 wet valid times with all three fits active. '+(s.integrated?.review_minimum_sample_met?'Sample minimum reported met; independent skill review still required.':'Sample minimum not established. No promotion.'),'section-note'));
  }catch(_){for(const e of [health,research])if(e)e.textContent='Workflow status unavailable. Check GitHub Actions directly; no readiness inferred.';}
 }
 status();
}
