"""Assemble comparable same-valid-time maps from temporary regional caches."""
import json,hashlib,sys,os
from pathlib import Path
from datetime import datetime,timedelta
from collections import defaultdict
import numpy as np
from PIL import Image,ImageChops
from spatial_v3_style import DOMAINS,figure,base,finish,raster,save
from pilot_instability_moisture import PRODUCTS as DYN
from pilot_vertical_profiles import PRODUCTS as VERT
MODELS=("gfs","ifs-025","ifs-010","aifs-025","icon")
LABELS=dict(zip(MODELS,("GFS 0.25°","IFS 0.25°","IFS 0.1°","AIFS 0.25°","ICON Global")))
GROUPS={"pwv":("pwv","refc"),"circulation":("wind925","wind850","rh850","rh700","mslp"),"dynamics":tuple(DYN),"vertical":tuple(VERT),"rain":("rain","gust_1h","gust_instant"),"icon":("TQV","PMSL","CAPE_ML")}
WIND=[0,2,4,6,8,10,12,16,20,25,35];RH=list(range(0,111,10));MSLP=list(range(990,1021,2));PWV=[10,20,30,35,40,45,50,55,60,65,70,80];CAPE=[0,100,250,500,1000,1500,2000,3000,4000,6000];GUST=[0,5,10,15,20,25,30,40,50,60]
SPECS={**DYN,**VERT,"pwv":("Precipitable water","mm",PWV,"YlGnBu"),"refc":("GFS simulated composite reflectivity — not observed radar","dBZ",[-20,5,10,20,30,40,50,60,80],"turbo"),"mslp":("Mean sea-level pressure","hPa",MSLP,"RdBu_r"),"rain":("Accumulated rainfall","mm",[0,.1,1,2,5,10,20,30,50,75,100,150],"YlGnBu"),"gust_1h":("Previous-hour maximum gust","kt",GUST,"YlOrRd"),"gust_instant":("Instantaneous gust","kt",GUST,"YlOrRd"),"TQV":("ICON precipitable water","mm",PWV,"YlGnBu"),"PMSL":("ICON mean sea-level pressure","hPa",MSLP,"RdBu_r"),"CAPE_ML":("ICON native mixed-layer CAPE","J/kg",CAPE,"YlOrRd")}
for p in (925,850):SPECS[f"wind{p}"]=(f"{p} hPa circulation","m/s",WIND,"YlGnBu")
for p in (850,700):SPECS[f"rh{p}"]=(f"{p} hPa relative humidity","%",RH,"BrBG")
SPECS["freeze"]=("Resolved freezing height, geopotential MSL","m",list(range(4000,6001,200)),"viridis")
SPECS["z500"]=("500 hPa geopotential height","gpm",list(range(5820,5961,10)),"viridis")
def load_catalog():
    catalog=defaultdict(list);sources={}
    for model in MODELS:
        folder=Path("cache-inputs")/f"titan-full-cache-{model}"
        m=json.loads((folder/"manifest.json").read_text())
        if not m["passed"] or m["model"]!=model or m["code_sha"]!=os.environ["GITHUB_SHA"]:raise ValueError("Unvalidated cache")
        cycle=datetime.fromisoformat(m["cycle"]);sources[model]=m["cycle"]
        if [e["lead"] for e in m["entries"]]!=m["expected_leads"]:raise ValueError("Incomplete cache")
        for e in m["entries"]:
            valid=datetime.fromisoformat(e["valid_utc"])
            if cycle+timedelta(hours=e["lead"])!=valid:raise ValueError("Valid time mismatch")
            path=folder/e["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest()!=e["sha256"]:raise ValueError("Numeric checksum mismatch")
            for p in e["products"]:
                if p in SPECS:
                    window=tuple(e["windows"].get(p,[]))
                    if p in ("rain","gust_1h","gust_instant") and (len(window)!=2 or window[1]!=e["valid_utc"]):raise ValueError("Window mismatch")
                    catalog[(p,e["valid_utc"],window)].append((model,m["cycle"],e,path))
    return catalog,sources
def render(out,p,valid,window,panels,domain):
    title,unit,levels,cmap=SPECS[p]
    if window and p=="rain":title+=f" ({(datetime.fromisoformat(window[1])-datetime.fromisoformat(window[0])).total_seconds()/3600:g} h)"
    t=datetime.fromisoformat(valid)
    fig,axes,norm=figure(len(panels),title,domain,0,t,"NOAA/NCEP, ECMWF, DWD | model-specific initialization shown",unit,levels,cmap)
    fig.suptitle(title+"\n"+domain.capitalize()+f" | Valid {t:%d %b %Y %HZ}",fontsize=14,weight="semibold",y=.98)
    for ax,(model,init,e,path) in zip(axes,panels):
        with np.load(path,allow_pickle=False) as f:
            a=f[p];x=np.linspace(110,132,a.shape[1]);y=np.linspace(-10,5,a.shape[0]);m=base(ax,domain)
            label=LABELS[model]+f" · Init {datetime.fromisoformat(init):%d %HZ} · H+{e['lead']}"
            if model=="aifs-025" and p.startswith("rh"):label+=" · derived RH"
            if model=="icon":label+=" · native-centre display remap"
            ax.set_title(label,fontsize=9);raster(ax,x,y,a,norm,cmap)
            if p.startswith("wind"):
                pressure=p[4:];u,v=f["u"+pressure],f["v"+pressure];b=DOMAINS[domain];dx=e["dx"]
                sx=(x>=b[0]-dx)&(x<=b[1]+dx);sy=(y>=b[2]-dx)&(y<=b[3]+dx)
                ax.streamplot(x[sx],y[sy],np.ma.masked_invalid(u[np.ix_(sy,sx)]),np.ma.masked_invalid(v[np.ix_(sy,sx)]),density=.9,color="#253440",linewidth=.65,arrowsize=.7)
            if p in ("mslp","z500"):
                step=1 if p=="mslp" else 10;lev=np.arange(np.ceil(np.nanmin(a)/step)*step,np.floor(np.nanmax(a)/step)*step+1,step)
                if len(lev):ax.clabel(ax.contour(x,y,a,levels=lev,colors="#253440",linewidths=.5),fmt="%.0f",fontsize=7)
            finish(ax,m,domain)
    suffix=("_"+datetime.fromisoformat(window[0]).strftime("%Y%m%dT%H%MZ")) if window else ""
    name=f"{p}_{domain}_{t:%Y%m%dT%H%MZ}{suffix}"
    png=out/(name+".png");webp=out/(name+".webp");save(fig,png)
    with Image.open(png) as im:
        rgba=im.convert("RGBA")
        if rgba.getchannel("A").getextrema()!=(255,255):raise ValueError("Unexpected alpha")
        rgb=rgba.convert("RGB");rgb.save(webp,format="WEBP",lossless=True,method=4)
        with Image.open(webp) as b:
            if b.size!=rgb.size or ImageChops.difference(rgb,b.convert("RGB")).getbbox():raise ValueError("Pixel mismatch")
    png.unlink()
    return {"product":p,"domain":domain,"valid_utc":valid,"window_utc":window,"models":[{"model":m,"init_utc":c,"lead":e["lead"],"numeric_sha256":e["sha256"]} for m,c,e,_ in panels],"file":webp.name,"bytes":webp.stat().st_size,"sha256":hashlib.sha256(webp.read_bytes()).hexdigest()}
def main():
    group=sys.argv[1];out=Path("rendered");out.mkdir(exist_ok=False);catalog,sources=load_catalog();entries=[]
    for (p,valid,window),panels in sorted(catalog.items()):
        if p not in GROUPS[group]:continue
        for domain in DOMAINS:entries.append(render(out,p,valid,window,panels,domain))
    manifest={"group":group,"source_cycles":sources,"entries":entries,"code_sha":os.environ["GITHUB_SHA"],"mode":"research_preview"}
    (out/"catalog.json").write_text(json.dumps(manifest,indent=2))
    print("FULL_RENDER",json.dumps({"group":group,"products":sorted({e["product"] for e in entries}),"charts":len(entries),"bytes":sum(e["bytes"] for e in entries)}),flush=True)
    if not entries:raise SystemExit("Empty group")
if __name__=="__main__":main()
