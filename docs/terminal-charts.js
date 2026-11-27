/* Presentation only: exact existing time rows and original point model values.
 * One selected hour across charts, numerical detail and spatial link.
 */
const TERMINAL_MODEL_STYLE={
 ECMWF_HRES:['IFS 0.25°','#0072b2'],ECMWF_IFS_9KM:['IFS 9 km','#56b4e9'],GFS_GLOBAL:['GFS','#d55e00'],ICON_SEAMLESS:['ICON','#009e73'],GEM_GLOBAL:['GEM','#cc79a7'],CMA_GRAPES_GLOBAL:['CMA GRAPES','#e69f00'],JMA_GSM:['JMA GSM','#8c6bb1'],METEOFRANCE_ARPEGE_WORLD:['ARPEGE','#708090'],UKMO_GLOBAL_10KM:['UKMO 10 km','#a6761d']
};
let terminalState={rows:[],manifest:null,index:0}, terminalMounts=[], traceChart=null, traceGeneration=0;
const terminalEl=id=>document.getElementById(id);
function terminalCurrent(){return terminalState.rows.length>0&&previewIsCurrent(terminalState.manifest);}
function setupTerminalCursor(rows,manifest){
 terminalState={rows,manifest,index:0};const sel=terminalEl('terminal-time'),slider=terminalEl('terminal-slider');if(!sel||!slider)return;
 sel.replaceChildren();for(const [i,r] of rows.entries()){const o=document.createElement('option');o.value=i;o.textContent=titanPreviewLabel(previewValidEpoch(r.Datetime));sel.append(o);}
 slider.max=String(Math.max(0,rows.length-1));slider.disabled=!rows.length;sel.disabled=!rows.length;
 sel.onchange=()=>selectTerminalHour(+sel.value);slider.oninput=()=>selectTerminalHour(+slider.value);
 terminalEl('terminal-prev').onclick=()=>selectTerminalHour(terminalState.index-1);
 terminalEl('terminal-next').onclick=()=>selectTerminalHour(terminalState.index+1);
 terminalEl('terminal-map').onclick=()=>{if(terminalCurrent())openSpatialDiagnostic('rain','kolaka',new Date(previewValidEpoch(terminalState.rows[terminalState.index].Datetime)).toISOString());};
 if(rows.length)selectTerminalHour(0);else terminalEl('terminal-readout').textContent='No eligible forecast hours.';
}
function selectTerminalHour(i){
 if(!terminalCurrent()||!Number.isInteger(i)||i<0||i>=terminalState.rows.length)return;
 terminalState.index=i;const row=terminalState.rows[i],t=previewValidEpoch(row.Datetime);
 terminalEl('terminal-time').value=String(i);terminalEl('terminal-slider').value=String(i);
 terminalEl('terminal-slider').setAttribute('aria-valuetext',titanPreviewLabel(t));terminalEl('terminal-prev').disabled=i===0;terminalEl('terminal-next').disabled=i===terminalState.rows.length-1;
 const root=terminalEl('terminal-readout');root.replaceChildren();
 for(const [label,key,unit] of [['Wind','Wind','kt'],['Direction','Wind Dir.','°'],['Gust','Gust','kt'],['Rain','Rain','mm/output h'],['Temp','Temperature','°C'],['Td','Dewpoint','°C']]){
  const b=document.createElement('span'),v=TitanPreview.numeric(row[key]);b.textContent=label+' '+(v===null?'—':v.toFixed(key==='Wind Dir.'?0:1)+' '+unit);root.append(b);}
 terminalEl('terminal-timeline')?.onterminalselect?.(i);
 updateTerminalCrosshairs();
}
function terminalBounds(rows){const t=rows.map(r=>previewValidEpoch(r.Datetime));return {min:t[0],max:t[t.length-1]===t[0]?t[0]+3600000:t[t.length-1]};}
function gridRect(mount){return mount.querySelector('.apexcharts-grid')?.getBoundingClientRect();}
function updateTerminalCrosshairs(){
 if(!terminalCurrent())return;const rows=terminalState.rows,t=previewValidEpoch(rows[terminalState.index].Datetime),b=terminalBounds(rows);
 for(const m of terminalMounts){if(!m.isConnected)continue;const g=gridRect(m),r=m.getBoundingClientRect(),line=m.querySelector('.terminal-crosshair');if(!g||!g.width||!r.width||!line)continue;
  line.style.left=(g.left-r.left+(t-b.min)/(b.max-b.min)*g.width)+'px';line.style.top=(g.top-r.top)+'px';line.style.height=g.height+'px';line.dataset.epoch=String(t);line.hidden=false;
 }
 document.querySelectorAll('[data-cursor-hour]').forEach(x=>{const active=Number(x.dataset.cursorHour)===terminalState.index;x.classList.toggle('chosen',active);x.setAttribute('aria-pressed',String(active));});
}
window.addEventListener('resize',()=>requestAnimationFrame(updateTerminalCrosshairs));
function attachTerminalCursor(mount,rows){
 if(terminalMounts.includes(mount))return;terminalMounts.push(mount);
 const line=document.createElement('div');line.className='terminal-crosshair';line.hidden=true;line.setAttribute('aria-hidden','true');mount.append(line);
 const pointer=e=>{if(!terminalCurrent())return;const r=gridRect(mount);if(!r||!r.width||e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)return;
  const b=terminalBounds(rows),t=b.min+(e.clientX-r.left)/r.width*(b.max-b.min);let best=0;for(let i=1;i<rows.length;i++)if(Math.abs(previewValidEpoch(rows[i].Datetime)-t)<Math.abs(previewValidEpoch(rows[best].Datetime)-t))best=i;
  if(best!==terminalState.index)selectTerminalHour(best);};
 mount.addEventListener('pointermove',e=>{if(e.pointerType==='mouse')pointer(e);});mount.addEventListener('click',pointer);
 if(typeof ResizeObserver==='function'){const ro=new ResizeObserver(()=>requestAnimationFrame(updateTerminalCrosshairs));ro.observe(mount);}
 updateTerminalCrosshairs();
}
function initAlignedTerminalCharts(data,rows,manifest){
 const bounds=terminalBounds(rows);
 function draw(id,title,series,colors,type,unit,extra={}){
  const m=terminalEl(id);if(!m||m.dataset.rendered||!previewIsCurrent(manifest))return;m.dataset.rendered='1';
  if(!series.some(s=>s.data?.some(p=>TitanPreview.numeric(p[1])!==null))){m.textContent=title+': no model values.';return;}
  const chart=new ApexCharts(m,{...TITAN_BASE,chart:{...TITAN_BASE.chart,id:'aligned-'+id,type,height:176,animations:{enabled:false},toolbar:{show:false},zoom:{enabled:false},redrawOnParentResize:true},title:{text:title,style:{fontSize:'12px',fontWeight:600}},series,colors,
   dataLabels:{enabled:false},stroke:{curve:'straight',width:type==='bar'?0:2,dashArray:series.length===2?[0,4]:0},fill:{type:'solid',opacity:1},dropShadow:{enabled:false},
   grid:{...TITAN_BASE.grid,padding:{left:2,right:12,top:0,bottom:0}},
   xaxis:{...previewXAxis(),min:bounds.min,max:bounds.max, tickAmount:6,tooltip:{enabled:false}},
   yaxis:{tickAmount:4,labels:{minWidth:60,maxWidth:60,formatter:v=>Number.isFinite(v)?v.toFixed(1):'—'},...(type==='bar'||id==='chart-wind'||id==='chart-clouds'?{min:0}:{}),...(id==='chart-clouds'?{max:100}:{})},
   legend:{...TITAN_BASE.legend,show:series.length>1,position:'top'},tooltip:{...TITAN_BASE.tooltip,shared:true,y:{formatter:v=>Number.isFinite(v)?v.toFixed(1)+' '+unit:'—'}},...extra});
  chart.render().then(()=>{if(previewIsCurrent(manifest)&&m.isConnected)attachTerminalCursor(m,rows);}).catch(e=>{m.textContent=title+': chart rendering failed. Use numerical detail.';console.error('Terminal plot failure',id,e);});
 }
 const S=(name,key)=>({name,data:data[key]||[]});
 draw('chart-wind','Wind and gust (kt)',[S('Wind','windData'),S('Gust','gustData')],['#009e73','#e69f00'],'line','kt');
 draw('chart-rain','Hourly model precipitation (mm)',[S('Rain','rainData')],['#0072b2'],'bar','mm',{plotOptions:{bar:{columnWidth:'58%'}}});
 draw('chart-temp','Temperature and dewpoint (°C)',[S('Temperature','tempData'),S('Dewpoint','dewData')],['#d55e00','#0072b2'],'line','°C');
 draw('chart-clouds','Model cloud cover (%) · not ceiling',[S('Low','lowCloudData'),S('Mid','midCloudData'),S('High','highCloudData')],['#0072b2','#8c6bb1','#708090'],'line','%');
 const advanced=terminalEl('advanced-meteogram');
 const extra=()=>{if(!advanced.open||!previewIsCurrent(manifest))return;
 draw('chart-pressure','Model pressure (hPa)',[S('Pressure','pressureData')],['#8c6bb1'],'line','hPa');
 draw('chart-ttd','Temperature − dewpoint (°C)',[S('T − Td','ttdData')],['#009e73'],'line','°C');
 draw('chart-rain-cumulative','Displayed hourly precipitation sum (mm)',[S('Accumulated','cumulativeRain')],['#0072b2'],'line','mm',{stroke:{curve:'stepline',width:2}});
 };
 advanced.addEventListener('toggle',extra);extra();
}
function setupModelTraces(models,times){
 const root=terminalEl('trace-models'),param=terminalEl('trace-param'),mount=terminalEl('trace-chart'),note=terminalEl('trace-note');if(!root||!param||!mount)return;
 const keys=[...new Set(['Wind Speed','Wind Gust','Rainfall','Temperature','Dewpoint','Pressure'].flatMap(k=>Object.keys(models[k]||{})))];
 const ordered=[...Object.keys(TERMINAL_MODEL_STYLE).filter(k=>keys.includes(k)),...keys.filter(k=>!TERMINAL_MODEL_STYLE[k])];
 const selected=new Set(ordered);root.replaceChildren();
 const style=k=>TERMINAL_MODEL_STYLE[k]||[k,'#64748b'];
 for(const k of ordered){const l=document.createElement('label'),c=document.createElement('input'),sw=document.createElement('span');c.type='checkbox';c.checked=true;c.dataset.traceModel=k;sw.className='model-swatch';sw.style.backgroundColor=style(k)[1];l.append(c,sw,document.createTextNode(style(k)[0]));root.append(l);c.onchange=()=>{c.checked?selected.add(k):selected.delete(k);draw();};}
 function draw(){const gen=++traceGeneration;if(traceChart){traceChart.destroy();traceChart=null;}mount.replaceChildren();if(!terminalCurrent())return;
  const k=param.value,series=[],colors=[];
  for(const m of ordered.filter(x=>selected.has(x))){const values=times.map(t=>[previewValidEpoch(t),TitanPreview.numeric(models[k]?.[m]?.[t])]);if(!values.some(x=>x[1]!==null))continue;series.push({name:style(m)[0],data:values});colors.push(style(m)[1]);}
  note.textContent=series.length+' displayed model traces. Missing values remain gaps. Colors identify models, not relative skill.';
  if(!series.length){mount.textContent='No matching values in the selected models.';return;}
  const b=terminalBounds(terminalState.rows);const chart=new ApexCharts(mount,{...TITAN_BASE,chart:{...TITAN_BASE.chart,id:'titan-model-trace',type:'line',height:250,animations:{enabled:false}},series,colors,dataLabels:{enabled:false},stroke:{curve:'straight',width:2,dashArray:series.map((s,i)=>i%3===1?4:0)},xaxis:{...previewXAxis(),min:b.min,max:b.max},yaxis:{labels:{formatter:v=>Number.isFinite(v)?v.toFixed(1):'—'},...(k==='Rainfall'||k.startsWith('Wind')?{min:0}:{})},legend:{show:false},tooltip:{...TITAN_BASE.tooltip,shared:true}});
  traceChart=chart;chart.render().catch(e=>{if(gen===traceGeneration){mount.textContent='Model trace unavailable; use individual model table.';console.error(e);}});
 }
 param.onchange=draw;draw();
}
