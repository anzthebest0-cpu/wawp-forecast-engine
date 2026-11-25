"""Full native-lead regional cache pilot; no deployment, no database writes."""
import json,sys,os,time,hashlib
from pathlib import Path
from datetime import datetime,timedelta
import numpy as np
from scipy.spatial import cKDTree
import pilot_pwv_reflectivity as pwv
import pilot_circulation_comparison as circ
import pilot_instability_moisture as dyn
import pilot_vertical_profiles as vert
import pilot_rain_gust as rain
import pilot_icon_native as icon
from shared_spatial_fetch import SharedFetch
from spatial_rain_windows import interval,unique
from pilot_cycle_discovery import LEADS
ROOT=Path("regional-cache")
TOTAL_CAP=2*1024**3
def stamp(c,h):return (c+timedelta(hours=h)).isoformat()
def write(c,h,arrays,meta,windows,dx):
    shape=(round(15/dx)+1,round(22/dx)+1)
    if any(a.shape!=shape or not np.isfinite(a).any() or np.isinf(a).any() for a in arrays.values()):raise ValueError("Invalid regional field")
    f=ROOT/f"f{h:03d}.npz";np.savez_compressed(f,**arrays)
    return {"lead":h,"valid_utc":stamp(c,h),"dx":dx,"file":f.name,"sha256":hashlib.sha256(f.read_bytes()).hexdigest(),"products":sorted(arrays),"windows":windows,"provenance":meta}
def regular(model,c,leads):
    isg=model=="gfs";family="aifs-single" if model=="aifs-025" else "ifs";res="0p1" if model=="ifs-010" else "0p25"
    upstream=pwv.fetch;total=0;previous=0;prior_rain=[]
    for m in (pwv,circ,dyn,vert,rain):m.INIT=c;m.OUT=ROOT
    for h in leads:
        cache=None
        if not isg:
            cache=SharedFetch(upstream,max_bytes=min(256*1024**2,TOTAL_CAP-total),cycle_prefix=f"/forecasts/{c:%Y%m%d}/{c:%H}z/")
            for m in (pwv,circ,dyn,vert,rain):m.fetch=cache
        circ.used=dyn.used=vert.used=rain.used=0
        a,dx,m1=circ.load_gfs(h) if isg else circ.ecmwf(family,res,h)
        b,dx,m2=dyn.read_gfs(h) if isg else dyn.read_ecmwf(family,res,h)
        v,dx,m3=vert.read_gfs(h) if isg else vert.read_ecmwf(family,res,h)
        if any(m["errors"] or m["missing"] for m in (m2,m3)):raise ValueError("Required fields missing")
        d,q2=dyn.diagnostics(b,dx);x,q3=vert.diagnostics(v,dx)
        required={"mfc925","mfc850","flux925","shear","ki"}|({"cape","cin"} if isg else {"mucape"} if family=="ifs" else set())
        if not required<=set(d) or not set(vert.PRODUCTS)<=set(x):raise ValueError("Missing diagnostics")
        arrays={k:d[k] for k in required};arrays.update({k:x[k] for k in vert.PRODUCTS})
        for p in (925,850):
            arrays[f"wind{p}"]=np.hypot(a["u",p],a["v",p]);arrays[f"u{p}"]=a["u",p];arrays[f"v{p}"]=a["v",p]
        arrays.update(rh850=a["r",850],rh700=a["r",700],mslp=a["msl",0])
        pp,mp=(pwv.gfs(h) if isg else pwv.ifs(h,res)) if isg or family=="ifs" else ({},None)
        arrays.update({k:z[2] for k,z in pp.items()})
        windows={};rain_meta=[];gust=[]
        if h:
            # At the 12h steps GFS may supply two native 6h intervals.
            records=list(prior_rain)
            times=[h] if h-previous<=6 else [h-6,h]
            current=[]
            for t in times:
                data,meta=rain.gfs(t) if isg else rain.ecmwf(family,res,t)
                rain_meta.append({"lead":t,**meta})
                records.extend(data["rain"])
                if t==h:current=data["rain"];gust=data["gust"]
            rr=interval(records,previous,h)
            if rr is not None:
                arrays["rain"]=rr["array"];windows["rain"]=[stamp(c,previous),stamp(c,h)]
            for key,sel in (("gust_instant",lambda z:z["step"]=="instant"),("gust_1h",lambda z:z["step"]=="max" and z["start"]==h-1)):
                g=unique([z for z in gust if sel(z)])
                if g is not None:arrays[key]=g["array"];windows[key]=[stamp(c,g["start"]),stamp(c,h)]
            prior_rain=current
        received=cache.bytes if cache else sum(m["bytes"] for m in (m1,m2,m3,mp))+sum(m["bytes"] for m in rain_meta)
        total+=received
        if total>TOTAL_CAP:raise ValueError("Cycle payload budget")
        meta={"circulation":m1,"dynamics":m2,"vertical":m3,"dynamics_qc":q2,"vertical_qc":q3,"pwv":mp,"rain_source":rain_meta,"rain_status":"available" if "rain" in arrays else "not_applicable" if not h else "unavailable_exact_interval","native_gust":[rain.describe(z) for z in gust],"network_payload_bytes":received,"cache_hits":sum(e["hit"] for e in cache.events) if cache else 0}
        yield write(c,h,arrays,meta,windows,dx)
        previous=h
def icon_run(c,leads):
    icon.INIT=c;icon.OUT=ROOT;icon.CYCLE=c.strftime("%Y-%m-%dT%H")+"%3A00";icon.BUDGET=320*1024**2
    icon.candidates=lambda p,h:[f"{icon.ROOT}v1/m/icon/p/{p}/r/{icon.CYCLE}/s/PT{h:03d}H00M.grib2"]
    lat,lm=icon.read("CLAT",0);lon,om=icon.read("CLON",0)
    if (lm["uuid"],lm["count"])!=(om["uuid"],om["count"]):raise ValueError("Coordinate mismatch")
    lat,lon=icon.coord(lat,lm,"lat"),icon.coord(lon,om,"lon")
    region=(lon>=110)&(lon<=132)&(lat>=-10)&(lat<=5)
    if region.sum()<1000:raise ValueError("Unexpected crop")
    scale=np.cos(np.deg2rad(-2.5));coords=np.column_stack((lon[region]*scale,lat[region]));tree=cKDTree(coords)
    xx,yy=np.meshgrid(np.linspace(110,132,441),np.linspace(-10,5,301))
    dist,idx=tree.query(np.column_stack((xx.ravel()*scale,yy.ravel())))
    nd,ni=tree.query(coords[::137])
    for h in leads:
        old=icon.used;arrays={};meta={}
        for p in icon.FIELDS:
            v,m=icon.read(p,h)
            if (m["uuid"],m["count"])!=(lm["uuid"],lm["count"]):raise ValueError("Field grid mismatch")
            v=icon.normalize(p,v[region],m)
            if np.any(nd>1e-10) or not np.array_equal(v[ni],v[::137],equal_nan=True):raise ValueError("Native identity")
            a=v[idx].reshape(xx.shape);a[dist.reshape(xx.shape)>.16]=np.nan
            if not np.isfinite(a).any() or np.nanmin(a)<np.nanmin(v) or np.nanmax(a)>np.nanmax(v):raise ValueError("Remap QC")
            arrays[p]=a;meta[p]=m
        yield write(c,h,arrays,{"fields":meta,"coords":{"lat":lm,"lon":om},"remap":"nearest native centre, 0.05 degree display only","network_payload_bytes":icon.used-old+(old if h==0 else 0)},{},.05)
def main():
    ROOT.mkdir(exist_ok=False);model=sys.argv[1];start=time.monotonic()
    d=json.loads(Path("cycle-discovery.json").read_text());info=next(x for x in d["results"] if x["model"]==model)
    expected=tuple(h for h in LEADS if model!="aifs-025" or h%6==0)
    manifest={"model":model,"cycle":info["selected"],"discovery_utc":d["as_of_utc"],"code_sha":os.environ.get("GITHUB_SHA"),"expected_leads":expected,"entries":[]}
    try:
        if not info["selected"] or tuple(info["available_leads"])!=expected:raise ValueError("Incomplete native schedule; do not publish")
        c=datetime.fromisoformat(info["selected"])
        for rec in icon_run(c,expected) if model=="icon" else regular(model,c,expected):
            manifest["entries"].append(rec)
            print("FULL_LEAD",model,rec["lead"],len(rec["products"]),rec["provenance"].get("rain_status"),flush=True)
        manifest["passed"]=True
    except Exception as e:manifest["passed"]=False;manifest["error"]=str(e)[:1500]
    manifest["elapsed_seconds"]=round(time.monotonic()-start,2)
    (ROOT/"manifest.json").write_text(json.dumps(manifest,indent=2,allow_nan=False))
    print("FULL_CACHE",json.dumps({k:v for k,v in manifest.items() if k!="entries"}|{"completed_leads":len(manifest["entries"]),"network_payload_bytes":sum(x["provenance"]["network_payload_bytes"] for x in manifest["entries"])}),flush=True)
    if not manifest["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
