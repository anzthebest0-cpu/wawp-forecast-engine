/* Source catalog only; no data ingestion, scraping, meteorological calculation or proxy. */
(function(root){
'use strict';
const sources=[
  {
    "scale": "global",
    "id": "bmkg-dynamics",
    "title": "BMKG atmospheric dynamics",
    "source": "BMKG",
    "type": "Dasarian assessment",
    "url": "https://www.bmkg.go.id/iklim/dinamika-atmosfer",
    "image": null,
    "prompt": "Review Indonesian ENSO, IOD, MJO, SST and 850 hPa circulation together; use the published assessment period."
  },
  {
    "scale": "global",
    "id": "enso",
    "title": "ENSO — Niño regions",
    "source": "NOAA CPC",
    "type": "Weekly SST index",
    "url": "https://cpc.ncep.noaa.gov/products/precip/CWlink/MJO/enso.shtml",
    "image": "https://cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/ssta_c.gif",
    "prompt": "Check persistence of the Pacific SST anomaly; do not infer airport rainfall timing from ENSO alone."
  },
  {
    "scale": "global",
    "id": "iod",
    "title": "Indian Ocean Dipole",
    "source": "Australian Bureau of Meteorology",
    "type": "Monitoring indices",
    "url": "https://www.bom.gov.au/climate/enso/indices.shtml",
    "image": null,
    "prompt": "Check DMI and its evolution; assess alongside BMKG's regional interpretation."
  },
  {
    "scale": "global",
    "id": "mjo",
    "title": "MJO phase and outlook",
    "source": "Australian Bureau of Meteorology",
    "type": "RMM monitoring",
    "url": "https://www.bom.gov.au/climate/mjo/",
    "image": "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.phase.Last40days.gif",
    "prompt": "Check amplitude, direction of evolution and regional cloud response; the embedded phase graphic is monitoring, not necessarily an outlook."
  },
  {
    "scale": "global",
    "id": "waves",
    "title": "Equatorial waves — Hovmöller",
    "source": "NCICS",
    "type": "Experimental filtered OLR",
    "url": "https://ncics.org/portfolio/monitor/mjo/",
    "image": "https://ncics.org/pub/mjo/v1/hov/hirs.waves.NS.gif",
    "prompt": "Legacy southern narrow band (7.5°S–2.5°S), spanning WAWP latitude. Check timestamp and east/west propagation; use the current NCICS page for updated products."
  },
  {
    "scale": "global",
    "id": "kelvin",
    "title": "Kelvin wave — two-day panels",
    "source": "NCICS",
    "type": "Experimental OLR panels",
    "url": "https://ncics.org/portfolio/monitor/mjo/v1/",
    "image": "https://ncics.org/pub/mjo/v1/panel/hirs.kelvin.2.gif",
    "prompt": "Legacy two-day-interval panels; examine printed dates and forecast limitations before interpreting passage over Sulawesi."
  },
  {
    "scale": "global",
    "id": "sst",
    "title": "SST and anomalies",
    "source": "NOAA CPC",
    "type": "Weekly anomaly map",
    "url": "https://cpc.ncep.noaa.gov/products/precip/CWlink/MJO/enso.shtml",
    "image": "https://cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/ssta-week.gif",
    "prompt": "Examine broad ocean anomalies; for local waters consult NOAA OISST or BMKG and respect coastal grid limitations."
  },
  {
    "scale": "global",
    "id": "oisst",
    "title": "Daily OISST",
    "source": "NOAA NCEI",
    "type": "Daily gridded analysis",
    "url": "https://www.ncei.noaa.gov/products/optimum-interpolation-sst",
    "image": null,
    "prompt": "Use daily SST analyses with their observation dates; 0.25° product is not a bay-scale measurement."
  },
  {
    "scale": "synoptic",
    "id": "surface",
    "title": "Surface pressure and winds",
    "source": "Windy",
    "type": "External model viewer",
    "url": "https://www.windy.com/menu",
    "image": null,
    "prompt": "Select the surface wind and pressure isolines; check model, initialization if available and valid time. Look for convergence and pressure gradients."
  },
  {
    "scale": "synoptic",
    "id": "lowlevel",
    "title": "925 / 850 hPa circulation",
    "source": "BMKG Aviation",
    "type": "RASON analysis viewer",
    "url": "https://web-aviation.bmkg.go.id/",
    "image": null,
    "prompt": "Use the existing BMKG chart viewer below for low-level streamlines; distinguish analysis from forecast."
  },
  {
    "scale": "synoptic",
    "id": "forecastflow",
    "title": "Forecast circulation",
    "source": "Windy",
    "type": "External model viewer",
    "url": "https://www.windy.com/menu",
    "image": null,
    "prompt": "Select wind and the desired pressure level; compare future time steps. This is provider model guidance, not a local TITAN forecast."
  },
  {
    "scale": "synoptic",
    "id": "moisture",
    "title": "Moisture transport and convergence",
    "source": "BMKG Aviation / Windy",
    "type": "Diagnostic checklist",
    "url": "https://www.windy.com/menu",
    "image": null,
    "prompt": "Compare low-level flow with humidity and moisture fields where available. Wind convergence is not automatically moisture-flux convergence; dedicated computed fields are not integrated."
  },
  {
    "scale": "meso",
    "id": "instability",
    "title": "Instability and inhibition",
    "source": "BMKG Aviation",
    "type": "RASON analysis viewer",
    "url": "https://web-aviation.bmkg.go.id/",
    "image": null,
    "prompt": "Use CAPE+, LI, KI, TT and SWEAT in the chart viewer; assess CIN separately where available. None alone establishes thunderstorm occurrence."
  },
  {
    "scale": "meso",
    "id": "shear",
    "title": "Vertical wind shear and humidity",
    "source": "BMKG Aviation",
    "type": "RASON analysis viewer",
    "url": "https://web-aviation.bmkg.go.id/",
    "image": null,
    "prompt": "Compare surface–925 and surface–850 shear with surface, 850 and 700 hPa RH; examine dry-air layers and organization."
  },
  {
    "scale": "meso",
    "id": "ir",
    "title": "Himawari IR enhanced",
    "source": "BMKG",
    "type": "Satellite observation",
    "url": "https://www.bmkg.go.id/cuaca/satelit/himawari-ir-enhanced",
    "image": "https://inderaja.bmkg.go.id/IMAGE/HIMA/H08_EH_Indonesia.png",
    "prompt": "Check cloud-top temperature and temporal evolution; cold tops alone do not prove surface rainfall or lightning."
  },
  {
    "scale": "meso",
    "id": "satellite",
    "title": "Satellite products and animations",
    "source": "BMKG",
    "type": "Official interactive page",
    "url": "https://inderaja.bmkg.go.id/BMKG/",
    "image": null,
    "prompt": "Compare consecutive IR frames, water vapor and other channels. Use the original source timestamp and logo."
  },
  {
    "scale": "local",
    "id": "radar",
    "title": "Radar and lightning layer",
    "source": "BMKG",
    "type": "Published interactive radar",
    "url": "https://www.bmkg.go.id/cuaca/radar",
    "image": null,
    "prompt": "Inspect echo movement and coverage around Kolaka; verify radar timestamp, beam limitations and possible blockage. No raw radar access is assumed."
  },
  {
    "scale": "local",
    "id": "obs",
    "title": "Airport observations",
    "source": "BMKG Aviation",
    "type": "Official source page",
    "url": "https://web-aviation.bmkg.go.id/web/observation.php",
    "image": null,
    "prompt": "Check METAR/SPECI and observation time. Local AWOS feed is not connected to TITAN."
  },
  {
    "scale": "local",
    "id": "awos",
    "title": "WAWP AWOSnet",
    "source": "AWI / station",
    "type": "Unverified endpoint",
    "url": "http://wawp.awosnet.com/",
    "image": null,
    "prompt": "Open directly if authorized and reachable; do not treat this link as proof of a live station connection."
  },
  {
    "scale": "local",
    "id": "clim",
    "title": "Station climatology",
    "source": "TITAN",
    "type": "Historical archive",
    "url": null,
    "image": null,
    "prompt": "Review diurnal wind and rain patterns; climatology is context, not a current observation."
  }
];
const scales=[{id:'global',title:'Global / intraseasonal',question:'Is the large-scale background favorable for convection?'},{id:'synoptic',title:'Synoptic',question:'What circulation and moisture patterns could influence Kolaka?'},{id:'meso',title:'Mesoscale',question:'Is the regional atmosphere supporting organized convection?'},{id:'local',title:'Local / airport',question:'What is actually happening near WAWP?'}];
const forScale=id=>sources.filter(x=>x.scale===id);
const api={sources,scales,forScale};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.TitanAnalysis=api;
})(typeof globalThis!=='undefined'?globalThis:this);
function setupAnalysisWorkspace(){
    const root=document.getElementById('analysis-workspace');if(!root)return;
    const filters=document.getElementById('analysis-scales'),cards=document.getElementById('analysis-cards'),question=document.getElementById('analysis-question');
    const viewer=document.getElementById('analysis-image-viewer'),image=document.getElementById('analysis-image'),status=document.getElementById('analysis-image-status'),source=document.getElementById('analysis-image-source'),title=document.getElementById('analysis-image-title');
    const create=(tag,content,cls)=>{const el=document.createElement(tag);if(content)el.textContent=content;if(cls)el.className=cls;return el;};
    let selected=null,request=0;
    function showImage(item){
        selected=item;const attempt=++request;viewer.hidden=false;title.textContent=item.title;
        source.href=item.url;source.textContent='Open provider: '+item.source;
        status.textContent='Requesting external image. Product validity not verified.';
        image.hidden=true;image.removeAttribute('src');image.alt=item.title+' — '+item.source;
        image.onload=()=>{if(attempt!==request)return;image.hidden=false;status.textContent='Image received at '+new Date().toISOString().slice(0,19)+' UTC. Read the date and valid time printed by the provider; receipt time is not observation or forecast time.';};
        image.onerror=()=>{if(attempt!==request)return;image.hidden=true;status.textContent='External image unavailable. Open the provider directly; no cached image is shown as current.';};
        const u=new URL(item.image);u.searchParams.set('_t',Date.now());image.src=u.href;
    }
    function render(scale){
        const s=TitanAnalysis.scales.find(x=>x.id===scale);if(!s)return;
        question.textContent=s.question;cards.replaceChildren();viewer.hidden=true;++request;image.removeAttribute('src');selected=null;
        filters.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.scale===scale?'true':'false'));
        TitanAnalysis.forScale(scale).forEach(item=>{
            const card=create('article',null,'analysis-card');card.append(create('h4',item.title),create('div',item.source+' · '+item.type,'analysis-meta'),create('p',item.prompt));
            const actions=create('div',null,'analysis-actions');
            if(item.image){const b=create('button','View source chart');b.type='button';b.addEventListener('click',()=>showImage(item));actions.append(b);}
            if(item.url){const a=create('a','Open source');a.href=item.url;a.target='_blank';a.rel='noopener noreferrer';actions.append(a);}
            if(item.id==='clim'){const b=create('button','Open climatology');b.type='button';b.addEventListener('click',()=>switchTab('tab-climatology'));actions.append(b);}
            if(['lowlevel','instability','shear'].includes(item.id)){const b=create('button','Use existing chart viewer');b.type='button';b.addEventListener('click',()=>document.getElementById('regional-chart-type').scrollIntoView({block:'center'}));actions.append(b);}
            card.append(actions);cards.append(card);
        });
    }
    TitanAnalysis.scales.forEach(s=>{const b=create('button',s.title);b.type='button';b.dataset.scale=s.id;b.addEventListener('click',()=>render(s.id));filters.append(b);});
    document.getElementById('analysis-image-refresh').addEventListener('click',()=>{if(selected)showImage(selected);});
    document.getElementById('analysis-image-full').addEventListener('click',()=>{if(selected)window.open(selected.image,'_blank','noopener,noreferrer');});
    render('global');
}
