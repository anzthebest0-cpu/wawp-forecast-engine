"""Sparse 24/48/96h test; preserve model/lead identity and decoded QC."""
import json,sys,time,hashlib
from datetime import datetime,timedelta
from pathlib import Path
import numpy as np
from PIL import Image,ImageChops
import pilot_pwv_reflectivity as pwv
import pilot_regional_gfs as gfs
import pilot_circulation_comparison as circ
import pilot_instability_moisture as dyn
import pilot_vertical_profiles as vert
import pilot_rain_gust as rain
import pilot_icon_native as icon
from shared_spatial_fetch import SharedFetch
from spatial_rain_windows import interval
from spatial_v3_style import figure,base,finish,raster,save
ROOT=Path("extended-horizon-pilot")
LEADS=(24,48,96)
def render(a,dx,model,c,h,key,title,unit,levels,cmap):
    if not np.isfinite(a).any():raise ValueError("Empty chart "+key)
    x=np.linspace(110,132,a.shape[1]);y=np.linspace(-10,5,a.shape[0])
    fig,axes,norm=figure(1,title,"kolaka",h,c,model,unit,levels,cmap)
    ax=axes[0];m=base(ax,"kolaka");raster(ax,x,y,a,norm,cmap);finish(ax,m,"kolaka")
    save(fig,ROOT/f"{key}_f{h:03d}.png")
def compress():
    results=[]
    for p in sorted(ROOT.glob("*.png")):
        dest=p.with_suffix(".webp")
        with Image.open(p) as im:
            rgba=im.convert("RGBA")
            if rgba.getchannel("A").getextrema()!=(255,255):raise ValueError("Unexpected transparency")
            rgb=rgba.convert("RGB");rgb.save(dest,format="WEBP",lossless=True,method=6)
            with Image.open(dest) as b:
                if b.size!=rgb.size or ImageChops.difference(rgb,b.convert("RGB")).getbbox():raise ValueError("Pixel mismatch")
        results.append({"file":dest.name,"png_bytes":p.stat().st_size,"webp_bytes":dest.stat().st_size,"sha256":hashlib.sha256(dest.read_bytes()).hexdigest()})
        p.unlink()
    return results
def run_icon(c):
    icon.INIT=c;icon.OUT=ROOT;icon.CYCLE=c.strftime("%Y-%m-%dT%H")+"%3A00"
    icon.candidates=lambda p,h:[f"{icon.ROOT}v1/m/icon/p/{p}/r/{icon.CYCLE}/s/PT{h:03d}H00M.grib2"]
    lat,lm=icon.read("CLAT",0);lon,om=icon.read("CLON",0)
    if (lm["uuid"],lm["count"])!=(om["uuid"],om["count"]):raise ValueError("Coordinate mismatch")
    lat,lon=icon.coord(lat,lm,"lat"),icon.coord(lon,om,"lon")
    region=(lon>=110)&(lon<=132)&(lat>=-10)&(lat<=5)
    if region.sum()<1000:raise ValueError("Unexpected crop")
    for h in LEADS:
        record={"lead":h,"valid_utc":(c+timedelta(hours=h)).isoformat(),"fields":{}}
        for p in icon.FIELDS:
            v,m=icon.read(p,h)
            if (m["uuid"],m["count"])!=(lm["uuid"],lm["count"]):raise ValueError("Field grid mismatch")
            v=icon.normalize(p,v[region],m);icon.plot(p,lon[region],lat[region],v,h)
            record["fields"][p]={"meta":m,"min":float(np.nanmin(v)),"max":float(np.nanmax(v))}
        yield record
def run_regular(model,c):
    isg=model=="gfs";family="aifs-single" if model=="aifs-025" else "ifs";res="0p1" if model=="ifs-010" else "0p25"
    original=pwv.fetch
    for m in (pwv,circ,dyn,vert,rain):m.INIT=c;m.OUT=ROOT
    for h in LEADS:
        cache=None
        if not isg:
            cache=SharedFetch(original,cycle_prefix=f"/forecasts/{c:%Y%m%d}/{c:%H}z/")
            for m in (pwv,circ,dyn,vert,rain):m.fetch=cache
        circ.used=dyn.used=vert.used=rain.used=0
        a,dx,m1=circ.load_gfs(h) if isg else circ.ecmwf(family,res,h)
        b,dx,m2=dyn.read_gfs(h) if isg else dyn.read_ecmwf(family,res,h)
        v,dx,m3=vert.read_gfs(h) if isg else vert.read_ecmwf(family,res,h)
        if any(m["errors"] or m["missing"] for m in (m2,m3)):raise ValueError("Required field missing "+str([m2["missing"],m3["missing"],m2["errors"],m3["errors"]]))
        d,q2=dyn.diagnostics(b,dx);x,q3=vert.diagnostics(v,dx)
        required={"mfc925","mfc850","flux925","shear","ki"}|({"cape","cin"} if isg else {"mucape"} if family=="ifs" else set())
        if any(not q2.get(k,{}).get("finite",0) for k in required) or any(not q3.get(k,{}).get("finite",0) for k in vert.PRODUCTS):raise ValueError("Incomplete diagnostics")
        p,mp=(pwv.gfs(h) if isg else pwv.ifs(h,res)) if isg or family=="ifs" else ({},None)
        # At H24 use 3h except native six-hour AIFS; H48 six hours; H96 twelve.
        start=h-(6 if h==24 and family=="aifs-single" and not isg else 3 if h==24 else 6 if h==48 else 12)
        times=sorted({start,h}|({h-6} if h==96 else set()))
        records=[];gust=[];rain_meta=[]
        for t in times:
            data,meta=rain.gfs(t) if isg else rain.ecmwf(family,res,t)
            records.extend(data["rain"]);rain_meta.append({"lead":t,**meta})
            if t==h:gust=data["gust"]
        rr=interval(records,start,h)
        # Optional unavailable interval remains explicit, never mislabel a native period.
        record={"lead":h,"valid_utc":(c+timedelta(hours=h)).isoformat(),"circulation":m1,"dynamics":m2,"vertical":m3,"dynamics_qc":q2,"vertical_qc":q3,"pwv":mp,"rain_source":rain_meta,"rain_window":[start,h],"rain":rain.describe(rr),"gust":[rain.describe(z) for z in gust],"rain_status":"available" if rr is not None else "unavailable_exact_interval"}
        render(np.hypot(a["u",925],a["v",925]),dx,model,c,h,"wind925","925 hPa wind speed","m/s",[0,2,4,6,8,10,12,16,20,25,35],"YlGnBu")
        for name,data in (("mfc925",d),("li",x),("freeze",x)):
            spec=(dyn.PRODUCTS if name=="mfc925" else vert.PRODUCTS)[name]
            render(data[name],dx,model,c,h,name,*spec)
        if rr is not None:render(rr["array"],dx,model,c,h,"rain",f"Accumulated rain H+{start}–H+{h}","mm",[0,.1,1,2,5,10,20,30,50,75,100,150],"YlGnBu")
        if cache:record.update(network_payload_bytes=cache.bytes,cache_hits=sum(e["hit"] for e in cache.events))
        else:record["network_payload_bytes"]=sum(m["bytes"] for m in (m1,m2,m3,mp))+sum(m["bytes"] for m in rain_meta)
        yield record
def main():
    model=sys.argv[1];ROOT.mkdir(exist_ok=False)
    start=time.monotonic();r={"model":model,"scope":"sparse 24/48/96h pilot","results":[]}
    try:
        discovery=json.loads(Path("cycle-discovery.json").read_text())
        info=next(x for x in discovery["results"] if x["model"]==model)
        if not info["selected"] or not set(LEADS)<=set(info["available_leads"]):raise ValueError("No validated discovery leads")
        c=datetime.fromisoformat(info["selected"]);r["cycle"]=c.isoformat()
        for record in run_icon(c) if model=="icon" else run_regular(model,c):
            r["results"].append(record)
            print("EXTENDED_LEAD",model,record["lead"],record.get("rain_status","not_in_scope"),flush=True)
        r["charts"]=compress();r["passed"]=True
        if model=="icon":r["network_payload_bytes"]=icon.used
    except Exception as e:r["passed"]=False;r["error"]=str(e)[:1500]
    r["elapsed_seconds"]=round(time.monotonic()-start,2)
    (ROOT/"report.json").write_text(json.dumps(r,indent=2))
    print("EXTENDED_HORIZON",json.dumps({k:v for k,v in r.items() if k not in ("results","charts")}|{"completed_leads":[x["lead"] for x in r["results"]],"webp_count":len(r.get("charts",[])),"webp_bytes":sum(x["webp_bytes"] for x in r.get("charts",[])),"rain_status":[x.get("rain_status") for x in r["results"]]}),flush=True)
    if not r["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
