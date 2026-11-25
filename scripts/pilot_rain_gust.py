"""Same-cycle spatial rainfall and gust pilot, with interval-aware QC."""
import json,hashlib,time,urllib.parse
from datetime import timedelta
from pathlib import Path
import eccodes as ec
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap
from pilot_pwv_reflectivity import fetch,INIT

OUT=Path("rain-gust-pilot")
MODELS=[("GFS 0.25°",None,None),("IFS 0.25°","ifs","0p25"),("IFS 0.1°","ifs","0p1"),("AIFS 0.25°","aifs-single","0p25")]
DOMAINS={"regional":(110,132,-10,5),"sulawesi":(119,128,-8,0),"kolaka":(120.5,123.5,-6,-3)}
GUST_NAMES={"10fg","10fg3","10fg6","10fg24","gust","fg10"}
LEADS=(6,12)
MAX_FIELD=8*1024**2
TOTAL_BUDGET=48*1024**2
used=0

def decode(h,kind,dx,lead):
    get=lambda k:ec.codes_get(h,k)
    typ=get("typeOfLevel");short=get("shortName")
    if kind=="rain" and typ!="surface":raise ValueError("Rain not surface")
    if kind=="gust" and not (typ=="surface" or (typ=="heightAboveGround" and get("level")==10)):raise ValueError("Gust not surface/10m")
    step=get("stepType");units=get("units")
    if step not in (("accum",) if kind=="rain" else ("max","instant")):raise ValueError("Wrong step type")
    if get("stepUnits") not in (1,"h"):raise ValueError("Step not hours")
    start,end=int(get("startStep")),int(get("endStep"))
    if not 0<=start<=end==lead or (kind=="rain" and start==end) or (step=="max" and start==end):raise ValueError("Wrong interval")
    for t,dk,tk in [(INIT,"dataDate","dataTime"),(INIT+timedelta(hours=lead),"validityDate","validityTime")]:
        if (get(dk),get(tk))!=(int(t.strftime("%Y%m%d")),int(t.strftime("%H%M"))):raise ValueError("Time mismatch")
    if get("gridType")!="regular_ll" or abs(get("iDirectionIncrementInDegrees")-dx)>1e-6 or abs(get("jDirectionIncrementInDegrees")-dx)>1e-6:raise ValueError("Grid mismatch")
    x,y,a=[np.asarray(ec.codes_get_array(h,k),dtype=float) for k in ("longitudes","latitudes","values")]
    if get("bitmapPresent"):a[np.asarray(ec.codes_get_array(h,"bitmap"))==0]=np.nan
    x=x%360;s=(x>=109.99999)&(x<=132.00001)&(y>=-10.00001)&(y<=5.00001)
    x,y,a=x[s],y[s],a[s];nx,ny=round(22/dx)+1,round(15/dx)+1
    ix=np.rint((x-110)/dx).astype(int);iy=np.rint((y+10)/dx).astype(int)
    if len(a)!=nx*ny or np.any(ix<0) or np.any(ix>=nx) or np.any(iy<0) or np.any(iy>=ny) or len(np.unique(iy*nx+ix))!=len(a):raise ValueError("Incomplete crop")
    if not np.allclose(x,110+ix*dx,rtol=0,atol=1e-5) or not np.allclose(y,-10+iy*dx,rtol=0,atol=1e-5):raise ValueError("Wrong coordinates")
    arr=np.full((ny,nx),np.nan);arr[iy,ix]=a
    if kind=="rain":
        if units=="m":arr*=1000
        elif units not in ("kg m**-2","kg m-2","kg/m^2","mm"):raise ValueError("Rain units "+units)
    else:
        if units not in ("m s**-1","m/s"):raise ValueError("Gust units "+units)
        arr*=1.9438444924
    if not np.isfinite(arr).any() or np.any(np.isfinite(arr)&((arr<-.001)|(arr>(2000 if kind=="rain" else 200)))):raise ValueError("Range QC")
    arr=np.where((arr<0)&(arr>=-.001),0,arr)
    packing_error=None
    if kind=="rain":
        try:
            pe=float(get("packingError"))*(1000 if units=="m" else 1)
            if np.isfinite(pe) and 0<=pe<=1:packing_error=pe
        except Exception:pass
    return {"packing_error_mm":packing_error,"array":arr,"start":start,"end":end,"step":step,"short":short,"units":units,"dx":dx,"level":typ,"code":[get(k) for k in ("discipline","parameterCategory","parameterNumber")]}

def gfs(lead):
    q={"dir":INIT.strftime("/gfs.%Y%m%d/%H/atmos"),"file":f"gfs.t{INIT:%H}z.pgrb2.0p25.f{lead:03d}","subregion":"","leftlon":110,"rightlon":132,"toplat":5,"bottomlat":-10,"lev_surface":"on","lev_10_m_above_ground":"on","var_APCP":"on","var_GUST":"on"}
    url="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"+urllib.parse.urlencode(q)
    data=fetch(url,MAX_FIELD);p=OUT/"tmp.grib2";p.write_bytes(data);out={"rain":[],"gust":[]};errors=[]
    try:
        with p.open("rb") as f:
            while (h:=ec.codes_grib_new_from_file(f)) is not None:
                try:
                    code=tuple(ec.codes_get(h,k) for k in ("discipline","parameterCategory","parameterNumber"))
                    kind="rain" if code==(0,1,8) else "gust" if code==(0,2,22) else None
                    if kind:
                        try:out[kind].append(decode(h,kind,.25,lead))
                        except Exception as e:errors.append(str(e))
                finally:ec.codes_release(h)
    finally:p.unlink(missing_ok=True)
    return out,{"url":url,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"errors":errors}

def ecmwf(model,res,lead):
    global used
    dx=.1 if res=="0p1" else .25
    suffix="-sfc" if res=="0p1" else ""
    url=f"https://data.ecmwf.int/forecasts/{INIT:%Y%m%d}/{INIT:%H}z/{model}/{res}/oper/{INIT:%Y%m%d%H}0000-{lead}h-oper-fc{suffix}.grib2"
    rows=[json.loads(x) for x in fetch(url.removesuffix(".grib2")+".index",4*1024**2).splitlines() if x.strip()]
    out={"rain":[],"gust":[]};meta={"url":url,"fields":[],"errors":[]}
    selected=[x for x in rows if x.get("levtype")=="sfc" and (x.get("param")=="tp" or x.get("param") in GUST_NAMES)]
    for row in selected:
        kind="rain" if row["param"]=="tp" else "gust"
        start,size=row["_offset"],row["_length"]
        if not isinstance(start,int) or not isinstance(size,int) or start<0 or not 0<size<=MAX_FIELD or used+size>TOTAL_BUDGET:raise ValueError("Range budget")
        used+=size
        try:
            data=fetch(url,size,(start,size))
            if not data.startswith(b"GRIB") or not data.endswith(b"7777"):raise ValueError("Bad message")
            h=ec.codes_new_from_message(data)
            try:
                name=ec.codes_get(h,"shortName")
                if name!="tp" and kind=="rain" or kind=="gust" and name not in GUST_NAMES:raise ValueError("Wrong parameter")
                out[kind].append(decode(h,kind,dx,lead))
            finally:ec.codes_release(h)
            meta["fields"].append({"param":row["param"],"bytes":size,"sha256":hashlib.sha256(data).hexdigest(),"offset":start})
        except Exception as e:meta["errors"].append(f"{row['param']}: {e}")
    return out,meta

def unique(candidates):
    if not candidates:return None
    a=candidates[0]
    if len(candidates)==1:return a
    keys=("start","end","step","short","units","dx","level","code")
    for b in candidates[1:]:
        if any(a[k]!=b[k] for k in keys) or not np.array_equal(a["array"],b["array"],equal_nan=True):
            print("DUPLICATE_CONFLICT",json.dumps({"candidate_count":len(candidates),"start":a["start"],"end":a["end"]}),flush=True)
            return None
    print("IDENTICAL_DUPLICATES",len(candidates),a["start"],a["end"],flush=True)
    return {**a,"identical_duplicate_count":len(candidates)}

def rain_window(current,previous,lead):
    direct=unique([x for x in current if x["start"]==lead-6 and x["end"]==lead])
    if direct:return {**direct,"method":"native 6h accumulation"}
    if lead==12:
        a=unique([x for x in current if x["start"]==0 and x["end"]==12])
        b=unique([x for x in previous if x["start"]==0 and x["end"]==6])
        if a and b and a["dx"]==b["dx"] and a["short"]==b["short"]:
            delta=a["array"]-b["array"]
            finite=np.isfinite(delta)
            if not finite.any():return None
            # Each independently packed cumulative field can differ by its own
            # quantization error. Do not use a guessed blanket tolerance.
            ea,eb=a.get("packing_error_mm"),b.get("packing_error_mm")
            tolerance=(ea+eb+1e-8) if ea is not None and eb is not None else 1e-8
            bad=finite&(delta < -tolerance)
            near=finite&(delta<0)&~bad
            qc={"minimum_difference_mm":float(np.nanmin(delta)),"negative_cells":int((finite&(delta<0)).sum()),"roundoff_cells":int(near.sum()),"beyond_packing_bound_cells":int(bad.sum()),"finite_cells":int(finite.sum()),"packing_bound_mm":tolerance,"packing_errors_mm":[ea,eb]}
            if bad.any():
                iy,ix=np.where(bad)
                qc["example_coordinates"]=[[float(110+x*a["dx"]),float(-10+y*a["dx"]),float(delta[y,x])] for y,x in zip(iy[:5],ix[:5])]
            print("RAIN_DELTA_QC",json.dumps(qc),flush=True)
            if bad.any():return None
            return {**a,"array":np.where(near,0,delta),"start":6,"method":"0–12h minus 0–6h same-cycle cumulative fields; negative roundoff bounded by GRIB packing error","difference_qc":qc}
    return None

def describe(record):
    if record is None:return None
    arr=record["array"];return {**{k:v for k,v in record.items() if k!="array"},"finite":int(np.isfinite(arr).sum()),"min":float(np.nanmin(arr)),"max":float(np.nanmax(arr))}

def plot(data,kind,lead,domain):
    from spatial_v3_style import figure,base,finish,raster,save
    labels=[label for label,_,__ in MODELS if label in data]
    if not labels:return
    levels=[0,.1,1,2,5,10,20,30,50,75,100,150] if kind=="rain" else [0,5,10,15,20,25,30,35,40,50,60]
    cmap="YlGnBu" if kind=="rain" else "YlOrRd"
    title={"rain":"6-hour accumulated rainfall","gust_max":"6-hour maximum forecast gust","gust_1h":"Previous-hour maximum forecast gust","gust_instant":"Instantaneous forecast gust"}[kind]
    fig,axes,norm=figure(len(labels),title,domain,lead,INIT,"NOAA/NCEP and ECMWF open data","mm" if kind=="rain" else "kt",levels,cmap,"max")
    for ax,label in zip(axes,labels):
        m=base(ax,domain);rec=data[label];dx=rec["dx"];x=np.linspace(110,132,round(22/dx)+1);y=np.linspace(-10,5,round(15/dx)+1)
        window=f"H+{rec['start']}–H+{rec['end']}" if rec["step"]!="instant" else f"instant H+{lead}"
        ax.set_title(label+" | "+window,fontsize=11);raster(ax,x,y,rec["array"],norm,cmap);finish(ax,m,domain)
    save(fig,OUT/f"{kind}_{domain}_f{lead:03d}.png")

def main():
    OUT.mkdir(exist_ok=False);prev={};manifest={"cycle":INIT.isoformat(),"domains":DOMAINS,"leads":{}}
    count=0
    for lead in LEADS:
        results={"rain":{},"gust_max":{},"gust_1h":{},"gust_instant":{}};records={}
        for label,model,res in MODELS:
            try:
                fields,meta=gfs(lead) if model is None else ecmwf(model,res,lead)
                rain=rain_window(fields["rain"],prev.get(label,[]),lead)
                prev[label]=fields["rain"]
                maxgust=unique([x for x in fields["gust"] if x["step"]=="max" and x["start"]==lead-6 and x["end"]==lead])
                instant=unique([x for x in fields["gust"] if x["step"]=="instant" and x["end"]==lead])
                onehour=unique([x for x in fields["gust"] if x["step"]=="max" and x["start"]==lead-1 and x["end"]==lead])
                for key,val in [("rain",rain),("gust_max",maxgust),("gust_1h",onehour),("gust_instant",instant)]:
                    if val is not None:results[key][label]=val;count+=1
                records[label]={**meta,"candidate_rain":[describe(x) for x in fields["rain"]],"candidate_gust":[describe(x) for x in fields["gust"]],"rain":describe(rain),"gust_max":describe(maxgust),"gust_1h":describe(onehour),"gust_instant":describe(instant)}
                print("RESULT",lead,label,json.dumps(records[label]),flush=True)
            except Exception as e:records[label]={"error":str(e)};print("FAIL",lead,label,str(e),flush=True)
        manifest["leads"][str(lead)]=records
        for key,data in results.items():
            if data:
                for domain in DOMAINS:plot(data,key,lead,domain)
        (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2))
        if lead==6:time.sleep(10)
    print("VERIFIED_OUTPUTS",count,"ECMWF field bytes attempted",used,flush=True)
    # A green execution does not mean all products available; fail if core rain comparison is incomplete.
    if any(not manifest["leads"][str(l)].get(m[0],{}).get("rain") for l in LEADS for m in MODELS):raise SystemExit("Incomplete core rainfall comparison; inspect manifest")
if __name__=="__main__":main()
