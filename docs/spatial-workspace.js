/* Full spatial research preview; same-origin immutable artifacts, independent of operational data. */
(function(){
'use strict';
document.addEventListener('DOMContentLoaded',()=>{
 const root=document.getElementById('titan-spatial'),tab=document.getElementById('tab-spatial');if(!root||!tab)return;
 const $=id=>document.getElementById('spatial-'+id),base=new URL('spatial-preview/',document.baseURI);
 const labels={'gfs':'GFS 0.25°','ifs-025':'IFS 0.25°','ifs-010':'IFS 0.10°','aifs-025':'AIFS 0.25°','icon':'ICON'};
 let state='idle',entries=[],products=[],times=[];
 function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
 function local(path){if(typeof path!=='string'||!/^sources\/[a-z]+\/[A-Za-z0-9_.-]+$/.test(path))throw Error('Unexpected chart path');return new URL(path,base).href}
 function link(href,text){const a=node('a',text);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a}
 function time(t,zone){return new Intl.DateTimeFormat('en-GB',{timeZone:zone,day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(t))}
 function stamp(t){return time(t,'UTC')+' UTC / '+time(t,'Asia/Makassar')+' WITA'}
 function panel(product){
  const p=products.find(e=>e.product===product),d=$('domain').value,v=$('lead').value;
  const card=node('article',undefined,'spatial-card');if(!p)return card;
  card.append(node('h3',p.label),node('p','Valid '+stamp(v)+' · '+d,'section-note'));
  const matches=entries.filter(e=>e.product===product&&e.domain===d&&e.valid_utc===v).sort((a,b)=>String(a.window_utc[0]||'').localeCompare(String(b.window_utc[0]||'')));
  if(!matches.length){card.append(node('p','Not available at this valid time. No substitution or zero filling.','spatial-empty'));return card}
  for(const a of matches){
   if(a.window_utc.length){const [s,end]=a.window_utc;card.append(node('h4','Window: '+time(s,'UTC')+' – '+time(end,'UTC')+' UTC ('+((Date.parse(end)-Date.parse(s))/3600000)+' h)'))}
   const models=a.models.map(m=>labels[m.model]+' · Init '+time(m.init_utc,'UTC')+' UTC · H+'+m.lead);
   card.append(node('p',models.join(' | '),'section-note'));
   card.append(node('p','Only available model panels are shown; absent models are not estimated.','section-note'));
   const url=local(a.image),img=node('img');img.alt=p.label+' '+d+' '+stamp(v);img.src=url;img.tabIndex=0;img.setAttribute('role','button');img.setAttribute('aria-label','Enlarge '+p.label);
   const zoom=()=>{$('zoom-image').src=url;$('zoom').showModal()};img.onclick=zoom;img.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();zoom()}};
   img.onerror=()=>img.replaceWith(node('p','Chart unavailable. Archived asset could not be loaded.','spatial-empty'));
   const links=node('div',undefined,'analysis-actions');links.append(link(url,'Full size'),link(local('sources/'+a.group+'/catalog.json'),'Source manifest'),link('https://github.com/anzthebest0-cpu/wawp-forecast-engine/actions/runs/45721468170','Original experiment'));
   card.append(img,links)
  }return card
 }
 function render(){const other=$('other').value,host=$('panels'),i=$('lead').selectedIndex;host.replaceChildren(panel($('product').value));if(other)host.append(panel(other));host.classList.toggle('two',!!other);$('prev').disabled=i<=0;$('next').disabled=i>=times.length-1}
 async function load(){
  if(state!=='idle')return;state='loading';$('status').textContent='Loading archived multi-model spatial catalog…';
  try{
   const res=await fetch(new URL('catalog.json',base),{cache:'no-store'});if(!res.ok)throw Error('Catalog unavailable');const c=await res.json();
   if(c.schema!==2||c.mode!=='research_preview'||c.run!=='45721468170'||c.entries.length!==1563||c.products.length!==25)throw Error('Unexpected catalog');
   for(const e of c.entries){local(e.image);if(!['regional','sulawesi','kolaka'].includes(e.domain)||!Number.isFinite(Date.parse(e.valid_utc))||!Array.isArray(e.models)||!Array.isArray(e.window_utc))throw Error('Invalid entry')}
   entries=c.entries;products=c.products;times=[...new Set(entries.map(e=>e.valid_utc))].sort((a,b)=>Date.parse(a)-Date.parse(b));
   for(const v of times){const o=node('option',stamp(v));o.value=v;$('lead').append(o)}
   for(const id of ['product','other']){const groups=new Map();for(const p of products){if(!groups.has(p.category)){const g=node('optgroup');g.label=p.category;groups.set(p.category,g);$(id).append(g)}const o=node('option',p.label);o.value=p.product;groups.get(p.category).append(o)}}
   $('product').value='wind925';$('lead').value=times.find(v=>Date.parse(v)>=Date.parse(c.discovery_utc)&&entries.some(e=>e.product==='wind925'&&e.valid_utc===v))||times[0];
   $('controls').hidden=false;$('status').textContent='HISTORICAL RESEARCH ONLY · 25 Nov 2026 UTC initializations · 1,563 figures / 25 products · up to H+96. Not live guidance; spatial forecast skill not verified.';state='ready';render()
  }catch(e){state='failed';$('controls').hidden=true;$('status').textContent='Historical spatial preview is unavailable. Existing TITAN forecasts remain independent.'}
 }
 for(const id of ['product','domain','lead','other'])$(id).onchange=render;
 $('prev').onclick=()=>{if($('lead').selectedIndex>0)$('lead').selectedIndex--;render()};$('next').onclick=()=>{if($('lead').selectedIndex<times.length-1)$('lead').selectedIndex++;render()};
 $('close').onclick=()=>$('zoom').close();
 new MutationObserver(()=>{if(tab.classList.contains('active'))load()}).observe(tab,{attributes:true,attributeFilter:['class']});if(tab.classList.contains('active'))load();
});
})();
