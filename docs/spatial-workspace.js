/* Isolated spatial preview: same-origin historical artifacts only; no operational state mutation. */
(function(){
'use strict';
document.addEventListener('DOMContentLoaded',()=>{
 const root=document.getElementById('titan-spatial'),tab=document.getElementById('tab-spatial');if(!root||!tab)return;
 const $=id=>document.getElementById('spatial-'+id);
 const base=new URL('spatial-preview/',document.baseURI);
 let state='idle',entries=[],products=[];
 function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
 function local(path){if(typeof path!=='string'||!/^sources\/[a-z]+\/[A-Za-z0-9_.-]+$/.test(path))throw Error('Unexpected chart path');return new URL(path,base).href}
 function link(href,text){const a=node('a',text);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a}
 function panel(product){
  const p=products.find(e=>e.product===product),d=$('domain').value,h=Number($('lead').value);
  const card=node('article',undefined,'spatial-card');if(!p)return card;
  card.append(node('h3',p.label),node('p',p.note,'section-note'));
  card.append(node('p','25 Nov 2026 · H+'+h+' · '+String(h).padStart(2,'0')+':00 UTC / '+String(h+8).padStart(2,'0')+':00 WITA · '+d,'section-note'));
  const a=entries.find(e=>e.product===product&&e.domain===d&&e.lead===h);
  if(!a){card.append(node('p','Not available for this lead. No substitution or zero filling.','spatial-empty'));return card}
  const url=local(a.image),img=node('img');img.alt=p.label+' '+d+' H+'+h;img.src=url;img.tabIndex=0;img.setAttribute('role','button');img.setAttribute('aria-label','Enlarge '+p.label);
  const zoom=()=>{$('zoom-image').src=url;$('zoom').showModal()};img.onclick=zoom;img.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();zoom()}};
  img.onerror=()=>img.replaceWith(node('p','Chart unavailable. Historical preview asset could not be loaded.','spatial-empty'));
  const links=node('div',undefined,'analysis-actions');links.append(link(url,'Full size'),link(local('sources/'+a.source+'/manifest.json'),'Source manifest'),link('https://github.com/anzthebest0-cpu/wawp-forecast-engine/actions/runs/'+a.run,'Original experiment'));
  card.append(img,links);return card
 }
 function render(){const other=$('other').value;const host=$('panels');host.replaceChildren(panel($('product').value));if(other)host.append(panel(other));host.classList.toggle('two',!!other);$('prev').disabled=$('lead').value==='0';$('next').disabled=$('lead').value==='12'}
 async function load(){
  if(state!=='idle')return;state='loading';$('status').textContent='Loading fixed-cycle historical spatial catalog…';
  try{
   const res=await fetch(new URL('catalog.json',base),{cache:'no-store'});if(!res.ok)throw Error('Catalog unavailable');
   const c=await res.json();if(c.cycle!=='2026-11-25 00 UTC'||!Array.isArray(c.entries)||c.entries.length!==216)throw Error('Unexpected preview cycle or coverage');
   for(const e of c.entries){local(e.image);if(!['regional','sulawesi','kolaka'].includes(e.domain)||![0,6,12].includes(e.lead)||!/^\d+$/.test(e.run)||!/^[a-z]+$/.test(e.source))throw Error('Invalid catalog entry')}
   entries=c.entries;products=[...new Map(entries.map(e=>[e.product,e])).values()];if(products.length!==25)throw Error('Unexpected product count');
   for(const id of ['product','other']){const groups=new Map();for(const p of products){if(!groups.has(p.category)){const g=node('optgroup');g.label=p.category;groups.set(p.category,g);$(id).append(g)}const o=node('option',p.label);o.value=p.product;groups.get(p.category).append(o)}}
   $('product').value='wind925';$('controls').hidden=false;$('status').textContent='HISTORICAL RESEARCH ONLY · '+c.cycle+' initialization · 216 figures / 25 products. Not live guidance. Spatial forecast skill not verified.';state='ready';render()
  }catch(e){state='failed';$('controls').hidden=true;$('status').textContent='Historical spatial preview is unavailable. The existing TITAN point forecast and official source links remain independent.'}
 }
 for(const id of ['product','domain','lead','other'])$(id).onchange=render;
 $('prev').onclick=()=>{$('lead').value=String(Number($('lead').value)-6);render()};$('next').onclick=()=>{$('lead').value=String(Number($('lead').value)+6);render()};
 $('close').onclick=()=>$('zoom').close();
 new MutationObserver(()=>{if(tab.classList.contains('active'))load()}).observe(tab,{attributes:true,attributeFilter:['class']});
 if(tab.classList.contains('active'))load();
});
})();
