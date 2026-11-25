"""Bounded, same-cycle GFS PWV/REFC and IFS native PWV comparison."""
import json, hashlib, re, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import eccodes as ec
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap

INIT=datetime(2026,11,25,0,tzinfo=timezone.utc)
LEADS=(0,6,12)
ROOT="https://data.ecmwf.int/forecasts/20261125/00z/ifs"
OUT=Path("pwv-reflectivity-pilot")
MAX_FIELD=4*1024*1024
UA="TITAN-WAWP-research-pilot/1.0"

def fetch(url, cap, interval=None):
    headers={"User-Agent":UA,"Accept-Encoding":"identity"}
    if interval:
        start,size=interval
        headers["Range"]=f"bytes={start}-{start+size-1}"
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=60) as resp:
        if urllib.parse.urlsplit(resp.geturl()).hostname != urllib.parse.urlsplit(url).hostname:
            raise ValueError("Unexpected redirect")
        if interval:
            expected=f"bytes {start}-{start+size-1}/"
            if resp.status!=206 or not resp.headers.get("Content-Range","").startswith(expected):
                raise ValueError("Range not honored")
        data=resp.read(cap+1)
    if len(data)>cap or (interval and len(data)!=size):
        raise ValueError("Payload size mismatch")
    return data

def decode(data, specs, lead, spacing, global_grid):
    path=OUT/"temporary.grib2"
    if not data.startswith(b"GRIB") or not data.endswith(b"7777"):
        raise ValueError("Invalid GRIB")
    path.write_bytes(data)
    result={}; meta={}
    lon=np.round(np.arange(110,132+spacing/2,spacing),6)
    lat=np.round(np.arange(-10,5+spacing/2,spacing),6)
    try:
        with path.open("rb") as f:
            while (g:=ec.codes_grib_new_from_file(f)) is not None:
                try:
                    get=lambda k: ec.codes_get(g,k)
                    name=None
                    for label,validator in specs.items():
                        if validator(get): name=label;break
                    if name is None: continue
                    if name in result:raise ValueError("Duplicate field "+name)
                    valid=INIT+timedelta(hours=lead)
                    for stamp,dk,tk in [(INIT,"dataDate","dataTime"),(valid,"validityDate","validityTime")]:
                        if (get(dk),get(tk))!=(int(stamp.strftime("%Y%m%d")),int(stamp.strftime("%H%M"))):
                            raise ValueError("Time mismatch")
                    if get("stepType")!="instant":raise ValueError("Not instantaneous")
                    units=get("units")
                    if name=="pwv" and units not in ("kg m**-2","kg m-2","kg/m^2","kg m^-2"):
                        raise ValueError("PWV units "+units)
                    if name=="refc" and units.lower() not in ("db","dbz"):
                        raise ValueError("REFC units "+units)
                    if get("gridType")!="regular_ll" or abs(get("iDirectionIncrementInDegrees")-spacing)>1e-6 or abs(get("jDirectionIncrementInDegrees")-spacing)>1e-6:
                        raise ValueError("Unexpected grid")
                    x,y,v=[np.asarray(ec.codes_get_array(g,k),dtype=float) for k in ("longitudes","latitudes","values")]
                    if global_grid and len(v)<1000000:raise ValueError("Expected global field")
                    if get("bitmapPresent"):v[np.asarray(ec.codes_get_array(g,"bitmap"))==0]=np.nan
                    x=x%360
                    sel=(x>=109.99999)&(x<=132.00001)&(y>=-10.00001)&(y<=5.00001)
                    x,y,v=x[sel],y[sel],v[sel]
                    ix=np.rint((x-110)/spacing).astype(int);iy=np.rint((y+10)/spacing).astype(int)
                    if len(v)!=len(lon)*len(lat) or np.any(ix<0) or np.any(ix>=len(lon)) or np.any(iy<0) or np.any(iy>=len(lat)) or len(np.unique(iy*len(lon)+ix))!=len(v):
                        raise ValueError("Crop extent mismatch")
                    if not np.allclose(x,lon[ix],atol=1e-5,rtol=0) or not np.allclose(y,lat[iy],atol=1e-5,rtol=0):raise ValueError("Coordinate mismatch")
                    arr=np.full((len(lat),len(lon)),np.nan);arr[iy,ix]=v
                    lo,hi=(-100,100) if name=="refc" else (0,120)
                    if not np.isfinite(arr).any() or np.any(np.isfinite(arr)&((arr<lo)|(arr>hi))):raise ValueError("Field out of physical QC range")
                    result[name]=(lon,lat,arr)
                    meta[name]={"units":units,"shortName":get("shortName"),"typeOfLevel":get("typeOfLevel"),"parameter":[get(k) for k in ("discipline","parameterCategory","parameterNumber")],"stepType":get("stepType"),"finite":int(np.isfinite(arr).sum()),"min":float(np.nanmin(arr)),"max":float(np.nanmax(arr))}
                finally:ec.codes_release(g)
    finally:path.unlink(missing_ok=True)
    if set(result)!=set(specs):raise ValueError("Missing fields "+str(set(specs)-set(result)))
    return result,meta

def gfs(lead):
    q={"dir":INIT.strftime("/gfs.%Y%m%d/%H/atmos"),"file":f"gfs.t00z.pgrb2.0p25.f{lead:03d}","subregion":"","leftlon":110,"rightlon":132,"toplat":5,"bottomlat":-10,"lev_entire_atmosphere":"on","lev_entire_atmosphere_(considered_as_a_single_layer)":"on","var_PWAT":"on","var_REFC":"on"}
    url="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"+urllib.parse.urlencode(q)
    data=fetch(url,MAX_FIELD)
    code=lambda get:tuple(get(k) for k in ("discipline","parameterCategory","parameterNumber"))
    specs={"pwv":lambda get:code(get)==(0,1,3) and get("typeOfFirstFixedSurface") in (10,200),
           "refc":lambda get:code(get)==(0,16,196) and get("typeOfFirstFixedSurface") in (10,200)}
    result,meta=decode(data,specs,lead,.25,False)
    return result,{"url":url,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"fields":meta}

def ifs(lead,res):
    suffix="-sfc" if res=="0p1" else ""
    url=f"{ROOT}/{res}/oper/20261125000000-{lead}h-oper-fc{suffix}.grib2"
    index=url.removesuffix(".grib2")+".index"
    rows=[json.loads(line) for line in fetch(index,MAX_FIELD).splitlines() if line.strip()]
    matches=[x for x in rows if x.get("param")=="tcwv" and x.get("levtype")=="sfc"]
    if len(matches)!=1:raise ValueError("Missing/duplicate tcwv")
    row=matches[0];offset,size=row["_offset"],row["_length"]
    if not isinstance(offset,int) or offset<0 or not isinstance(size,int) or not 0<size<=MAX_FIELD:raise ValueError("Bad range")
    data=fetch(url,size,(offset,size))
    result,meta=decode(data,{"pwv":lambda get:get("shortName")=="tcwv" and get("typeOfLevel")=="surface"},lead,.1 if res=="0p1" else .25,True)
    return result,{"url":url,"index":index,"range":[offset,size],"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"fields":meta}

def plot(panels,lead):
    fig,axes=plt.subplots(2,2,figsize=(15,9),layout="constrained")
    for ax,(label,kind,data) in zip(axes.flat,panels):
        m=Basemap(projection="cyl",llcrnrlon=110,urcrnrlon=132,llcrnrlat=-10,urcrnrlat=5,resolution="l",ax=ax)
        ax.set_title(label)
        if data is None:
            ax.text(.5,.5,"UNAVAILABLE",ha="center",va="center",transform=ax.transAxes);continue
        x,y,v=data
        if kind=="pwv":
            fill=ax.contourf(x,y,v,levels=np.arange(10,81,5),cmap="YlGnBu",extend="both")
            units="PWV (mm)"
        else:
            from matplotlib.colors import BoundaryNorm, ListedColormap
            # Native values retained in QC; < 5 dBZ is weak/below display threshold, not missing.
            levels=[-100,5,10,20,30,40,50,60,100]
            cmap=ListedColormap(["#f2f2f2","#9ed7ff","#4292c6","#41ab5d","#fed976","#fd8d3c","#e31a1c","#88419d"])
            fill=ax.pcolormesh(x,y,v,shading="nearest",cmap=cmap,norm=BoundaryNorm(levels,cmap.N))
            units="Simulated composite reflectivity (dBZ)"
        fig.colorbar(fill,ax=ax,orientation="horizontal",shrink=.83,label=units)
        m.drawcoastlines(linewidth=.7);m.drawcountries(linewidth=.4)
        m.drawparallels(np.arange(-10,6,5),labels=[1,0,0,0],fontsize=8,linewidth=.3)
        m.drawmeridians(np.arange(110,133,5),labels=[0,0,0,1],fontsize=8,linewidth=.3)
        ax.plot(121.525,-4.338,"k*",ms=9);ax.annotate("WAWP",(121.525,-4.338),xytext=(4,4),textcoords="offset points",fontsize=8)
        ax.set_facecolor("#bdbdbd")
    valid=INIT+timedelta(hours=lead)
    fig.suptitle(f"TITAN research pilot | init {INIT:%d %b %Y %HZ} | H+{lead} | valid {valid:%d %b %Y %HZ}\nGFS and ECMWF IFS (two resolutions, one model family) | simulated radar is NOT observed radar",fontsize=14)
    fig.savefig(OUT/f"comparison_f{lead:03d}.png",dpi=145);plt.close(fig)

def main():
    OUT.mkdir(exist_ok=False)
    manifest={"cycle":INIT.isoformat(),"leads":{}}
    failures=0
    for lead in LEADS:
        if lead:time.sleep(10)
        record={};panels=[]
        for label,fn,kinds in [("GFS 0.25°",lambda:gfs(lead),["pwv","refc"]),("IFS 0.25°",lambda:ifs(lead,"0p25"),["pwv"]),("IFS 0.1°",lambda:ifs(lead,"0p1"),["pwv"])]:
            try:
                data,meta=fn();record[label]=meta
                for k in kinds:panels.append((label+" · "+("PWV" if k=="pwv" else "simulated radar"),k,data[k]))
                print("PASS",lead,label,meta["bytes"],json.dumps(meta["fields"]),flush=True)
            except Exception as e:
                failures+=1;record[label]={"error":str(e)}
                for k in kinds:panels.append((label+" · "+k,k,None))
                print("FAIL",lead,label,str(e),flush=True)
        plot(panels,lead);manifest["leads"][str(lead)]=record
    (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2))
    if failures:raise SystemExit(f"{failures} source/lead failures; see diagnostic artifact")
if __name__=="__main__":main()
