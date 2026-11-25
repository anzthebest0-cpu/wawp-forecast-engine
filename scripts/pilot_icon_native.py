"""Bounded native ICON source and grid probe; research, not operational integration."""
import json,hashlib,urllib.request,urllib.parse,bz2,io
from pathlib import Path
import numpy as np
import eccodes as ec
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap
from datetime import datetime,timedelta
from pilot_pwv_reflectivity import INIT,fetch
ROOT="https://opendata.dwd.de/weather/nwp/"
OUT=Path("icon-native-pilot")
CYCLE="2026-11-25T00%3A00"
CAP=16*1024**2
BUDGET=120*1024**2
used=0
FIELDS={"TQV":("PWV","mm"),"PMSL":("Mean sea-level pressure","hPa"),"CAPE_ML":("Native ICON mixed-layer CAPE","J/kg")}
DOMAINS={"regional":(110,132,-10,5),"sulawesi":(119,128,-8,0),"kolaka":(120.5,123.5,-6,-3)}
def request(url):
    global used
    if used+CAP>BUDGET:raise ValueError("Transfer budget preflight")
    data=fetch(url,CAP)
    used+=len(data)
    if data.startswith(b"BZh"):
        data=bz2.BZ2Decompressor().decompress(data,max_length=40*1024**2+1)
        if len(data)>40*1024**2:raise ValueError("Decompressed size")
    if not data.startswith(b"GRIB") or not data.endswith(b"7777"):raise ValueError("Not GRIB")
    return data
def candidates(param,lead):
    new=f"{ROOT}v1/m/icon/p/{param}/r/{CYCLE}/s/PT{lead:03d}H00M.grib2"
    # Constants have a different publication convention; try only documented tree
    # variants, record actual success rather than assuming URL availability.
    legacy=f"{ROOT}icon/grib/00/{param.lower()}/icon_global_icosahedral_single-level_2026112500_{lead:03d}_{param}.grib2.bz2"
    if param in ("CLAT","CLON"):
        return [new,f"{ROOT}v1/m/icon/p/{param}/r/{CYCLE}/s/PT000H00M.grib2",f"{ROOT}icon/grib/00/{param.lower()}/icon_global_icosahedral_time-invariant_2026112500_{param}.grib2.bz2",legacy]
    return [new,legacy]
def read(param,lead):
    errors=[]
    for url in dict.fromkeys(candidates(param,lead)):
        try:
            data=request(url)
            g=ec.codes_new_from_message(data)
            try:
                get=lambda k:ec.codes_get(g,k)
                grid=get("gridType")
                if grid!="unstructured_grid":raise ValueError("Not native ICON grid")
                uuid=get("uuidOfHGrid")
                count=get("numberOfDataPoints")
                vals=np.asarray(ec.codes_get_array(g,"values"),float)
                if len(vals)!=count or count<1000000:raise ValueError("Unexpected grid count")
                if get("bitmapPresent"):vals[np.asarray(ec.codes_get_array(g,"bitmap"))==0]=np.nan
                keys=("shortName","paramId","units","typeOfLevel","stepType","dataDate","dataTime","validityDate","validityTime")
                meta={k:get(k) for k in keys};meta.update({"gridType":grid,"uuid":uuid,"count":count,"url":url,"sha256":hashlib.sha256(data).hexdigest(),"decoded_bytes":len(data),"failed_attempts":errors})
                if param not in ("CLAT","CLON"):
                    for stamp,dk,tk in [(INIT,"dataDate","dataTime"),(INIT+timedelta(hours=lead),"validityDate","validityTime")]:
                        if (get(dk),get(tk))!=(int(stamp.strftime("%Y%m%d")),int(stamp.strftime("%H%M"))):raise ValueError("Wrong time")
                    if get("stepType")!="instant":raise ValueError("Not instantaneous")
                print("ICON_FIELD",param,lead,json.dumps(meta),flush=True)
                return vals,meta
            finally:ec.codes_release(g)
        except Exception as e:errors.append({"url":url,"error":str(e)})
    raise ValueError(json.dumps(errors))
def coord(v,meta,axis):
    u=meta["units"].lower()
    if u in ("rad","radian","radians"):v=np.degrees(v)
    elif u not in ("degree","degrees","degrees_east","degrees_north","deg","deg e","deg n","degree e","degree n"):raise ValueError("Unknown coordinate units "+u)
    if axis=="lat" and (meta["shortName"]!="tlat" or meta["paramId"]!=250003 or u in ("degree e","deg e","degrees_east")):raise ValueError("Latitude identity mismatch")
    if axis=="lon" and (meta["shortName"]!="tlon" or meta["paramId"]!=250004 or u in ("degree n","deg n","degrees_north")):raise ValueError("Longitude identity mismatch")
    if not np.isfinite(v).all():raise ValueError("Missing coordinate")
    if axis=="lat" and (v.min()<-90 or v.max()>90 or v.min()>-80 or v.max()<80):raise ValueError("Latitude bounds")
    if axis=="lon" and (v.min()<-360 or v.max()>360):raise ValueError("Longitude bounds")
    return v if axis=="lat" else v%360
def normalize(param,v,m):
    u=m["units"];short=m["shortName"].lower()
    if param=="TQV":
        if short!="tqv" or m["paramId"]!=500038 or m["typeOfLevel"]!="surface" or u not in ("kg m**-2","kg m-2","kg/m^2"):raise ValueError("PWV identity/units "+str(m))
        lo,hi=0,120
    elif param=="PMSL":
        if short not in ("msl","pmsl","prmsl") or m["paramId"]!=260074 or m["typeOfLevel"]!="meanSea" or u!="Pa":raise ValueError("MSLP identity/units")
        v=v/100;lo,hi=850,1100
    else:
        if short!="cape_ml" or m["paramId"]!=500153 or u not in ("J kg**-1","J kg-1","J/kg"):raise ValueError("CAPE identity/units "+str(m))
        lo,hi=-.1,15000
    if not np.isfinite(v).any() or np.any(np.isfinite(v)&((v<lo)|(v>hi))):raise ValueError("Field range")
    return v
def plot(param,x,y,v,lead):
    for name,b in DOMAINS.items():
        fig,ax=plt.subplots(figsize=(10,7),layout="constrained")
        m=Basemap(projection="cyl",llcrnrlon=b[0],urcrnrlon=b[1],llcrnrlat=b[2],urcrnrlat=b[3],resolution="i",ax=ax)
        s=(x>=b[0])&(x<=b[1])&(y>=b[2])&(y<=b[3])&np.isfinite(v)
        if s.sum()<10:raise ValueError("Insufficient native cells")
        lo,hi,cmap={"TQV":(10,80,"YlGnBu"),"PMSL":(980,1032,"RdBu_r"),"CAPE_ML":(0,4000,"YlOrRd")}[param]
        # Cell-center visualization, no false regular-grid or invented cell geometry.
        p=ax.scatter(x[s],y[s],c=v[s],s=5 if name=="regional" else 12 if name=="sulawesi" else 28,marker=".",cmap=cmap,vmin=lo,vmax=hi,linewidths=0)
        fig.colorbar(p,ax=ax,orientation="horizontal",shrink=.85,label=FIELDS[param][1])
        m.drawcoastlines(linewidth=.7);m.drawcountries(linewidth=.4)
        step=5 if name=="regional" else 2 if name=="sulawesi" else 1
        m.drawparallels(np.arange(-10,6,step),labels=[1,0,0,0],fontsize=8);m.drawmeridians(np.arange(110,133,step),labels=[0,0,0,1],fontsize=8)
        ax.plot(121.525,-4.338,"k*",ms=9);ax.annotate("WAWP",(121.525,-4.338),xytext=(4,4),textcoords="offset points",fontsize=8)
        ax.set_xlim(b[:2]);ax.set_ylim(b[2:])
        fig.suptitle(f"DWD ICON native cells · {FIELDS[param][0]} · {name}\nInit {INIT:%d %b %Y %HZ} · H+{lead} · valid {INIT+timedelta(hours=lead):%d %b %Y %HZ} · research only")
        fig.savefig(OUT/f"{param}_{name}_f{lead:03d}.png",dpi=120);plt.close(fig)
def main():
    OUT.mkdir(exist_ok=False)
    manifest={"cycle":INIT.isoformat(),"fields":{},"notes":"DWD attribution; new v1 preferred, legacy only diagnostic fallback. Native cell-centre markers, no remapping. No production or storage change."}
    fail=0
    try:
        lat,lm=read("CLAT",0);lon,om=read("CLON",0)
        manifest["coords"]={"lat":lm,"lon":om}
        if (lm["uuid"],lm["count"])!=(om["uuid"],om["count"]):raise ValueError("Coordinate grid mismatch")
        lat,lon=coord(lat,lm,"lat"),coord(lon,om,"lon")
        region=(lon>=110)&(lon<=132)&(lat>=-10)&(lat<=5)
        if region.sum()<1000:raise ValueError("Unexpected regional cells")
        manifest["regional_cells"]=int(region.sum())
        for lead in (0,6,12):
            for param in FIELDS:
                key=f"{param}/{lead}"
                try:
                    vals,meta=read(param,lead)
                    if (meta["uuid"],meta["count"])!=(lm["uuid"],lm["count"]):raise ValueError("Field grid UUID mismatch")
                    v=normalize(param,vals[region],meta)
                    meta.update({"regional_min":float(np.nanmin(v)),"regional_max":float(np.nanmax(v)),"finite":int(np.isfinite(v).sum())})
                    manifest["fields"][key]=meta
                    plot(param,lon[region],lat[region],v,lead)
                    print("PASS",key,meta["finite"],flush=True)
                except Exception as e:fail+=1;manifest["fields"][key]={"error":str(e)};print("FAIL",key,str(e),flush=True)
    except Exception as e:fail+=1;manifest["coordinate_error"]=str(e);print("FAIL_COORDS",str(e),flush=True)
    finally:
        manifest["download_bytes"]=used
        (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2))
    if fail:raise SystemExit(f"{fail} source or field errors")
if __name__=="__main__":main()
