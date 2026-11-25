"""Matched-cycle circulation/RH/MSLP comparisons; independent research artifact."""
import hashlib,json,time
from pathlib import Path
import eccodes as ec
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap
import pilot_regional_gfs as gfs
from pilot_pwv_reflectivity import fetch,INIT,LEADS

OUT=Path("circulation-comparison")
MODELS=[("GFS 0.25°",None,None),("IFS 0.25°","ifs","0p25"),("IFS 0.1°","ifs","0p1"),("AIFS Single 0.25°","aifs-single","0p25")]
REQUIRED={("u",925),("v",925),("u",850),("v",850),("r",850),("r",700),("sp",0),("msl",0)}
UNITS={"u":("m s**-1","m/s"),"v":("m s**-1","m/s"),"r":("%",),"t":("K",),"q":("kg kg**-1","kg kg-1","kg/kg"),"sp":("Pa",),"msl":("Pa",)}
BUDGET=110*1024**2
used=0

def crop(data,k,lead,spacing):
    path=OUT/"temporary.grib2"
    path.write_bytes(data)
    try:
        with path.open("rb") as f:
            h=ec.codes_grib_new_from_file(f)
            if h is None:raise ValueError("No GRIB")
            try:
                get=lambda name:ec.codes_get(h,name)
                var,p=k
                if get("shortName")!=var or get("stepType")!="instant":raise ValueError("Wrong field "+str(k))
                if p and (get("typeOfLevel")!="isobaricInhPa" or get("level")!=p):raise ValueError("Wrong level")
                if not p and get("typeOfLevel")!=("surface" if var=="sp" else "meanSea"):raise ValueError("Wrong pressure level")
                if get("units") not in UNITS[var]:raise ValueError("Wrong units "+get("units"))
                if var in ("u","v") and get("uvRelativeToGrid")!=0:raise ValueError("Grid-relative wind")
                for stamp,dk,tk in [(INIT,"dataDate","dataTime"),(INIT+gfs.timedelta(hours=lead),"validityDate","validityTime")]:
                    if (get(dk),get(tk))!=(int(stamp.strftime("%Y%m%d")),int(stamp.strftime("%H%M"))):raise ValueError("Wrong time")
                if get("gridType")!="regular_ll" or abs(get("iDirectionIncrementInDegrees")-spacing)>1e-6 or abs(get("jDirectionIncrementInDegrees")-spacing)>1e-6:raise ValueError("Wrong grid")
                x,y,v=[np.asarray(ec.codes_get_array(h,n),dtype=float) for n in ("longitudes","latitudes","values")]
                if get("bitmapPresent"):v[np.asarray(ec.codes_get_array(h,"bitmap"))==0]=np.nan
                x=x%360
                sel=(x>=109.99999)&(x<=132.00001)&(y>=-10.00001)&(y<=5.00001)
                x,y,v=x[sel],y[sel],v[sel]
                nx,ny=round(22/spacing)+1,round(15/spacing)+1
                ix=np.rint((x-110)/spacing).astype(int);iy=np.rint((y+10)/spacing).astype(int)
                if len(v)!=nx*ny or np.any(ix<0) or np.any(ix>=nx) or np.any(iy<0) or np.any(iy>=ny) or len(np.unique(iy*nx+ix))!=len(v):raise ValueError("Wrong crop")
                if not np.allclose(x,110+ix*spacing,atol=1e-5,rtol=0) or not np.allclose(y,-10+iy*spacing,atol=1e-5,rtol=0):raise ValueError("Wrong coordinates")
                arr=np.full((ny,nx),np.nan);arr[iy,ix]=v
                if var in ("sp","msl"):arr/=100
                lo,hi={"sp":(300,1100),"msl":(850,1100),"r":(-1,110),"t":(180,340),"q":(0,.06),"u":(-150,150),"v":(-150,150)}[var]
                if not np.isfinite(arr).any() or np.any(np.isfinite(arr)&((arr<lo)|(arr>hi))):raise ValueError("Range QC "+str(k))
                if var in ("sp","msl") and not np.isfinite(arr).all():raise ValueError("Incomplete pressure")
            finally:ec.codes_release(h)
            extra=ec.codes_grib_new_from_file(f)
            if extra is not None:
                ec.codes_release(extra);raise ValueError("Multiple messages in one range")
        return arr
    finally:path.unlink(missing_ok=True)

def ecmwf(model,res,lead):
    global used
    spacing=.1 if res=="0p1" else .25
    keys=set(REQUIRED)
    if model=="aifs-single":
        keys-={("r",850),("r",700)}
        keys|={(v,p) for v in ("q","t") for p in (850,700)}
    urls={}
    for suff in ("-pl","-sfc") if res=="0p1" else ("",):
        url=f"https://data.ecmwf.int/forecasts/20261125/00z/{model}/{res}/oper/20261125000000-{lead}h-oper-fc{suff}.grib2"
        index=url.removesuffix(".grib2")+".index"
        for line in fetch(index,4*1024**2).splitlines():
            if not line.strip():continue
            row=json.loads(line)
            lev=row.get("levtype")
            if lev not in ("pl","sfc"):continue
            k=(row.get("param"),int(row["levelist"]) if lev=="pl" else 0)
            if k not in keys:continue
            if k in urls:raise ValueError("Duplicate index")
            offset,size=row["_offset"],row["_length"]
            if not isinstance(offset,int) or offset<0 or not isinstance(size,int) or not 0<size<=8*1024**2:raise ValueError("Range budget")
            urls[k]=(url,offset,size)
    if set(urls)!=keys:raise ValueError("Missing "+str(keys-set(urls)))
    planned=sum(x[2] for x in urls.values())
    if used+planned>BUDGET:raise ValueError("Cumulative transfer budget exceeded")
    out={};records=[]
    for k,(url,offset,size) in sorted(urls.items()):
        used+=size
        data=fetch(url,size,(offset,size))
        out[k]=crop(data,k,lead,spacing)
        records.append({"field":str(k),"url":url,"offset":offset,"bytes":size,"sha256":hashlib.sha256(data).hexdigest()})
    if model=="aifs-single":
        for p in (850,700):
            q,T=out["q",p],out["t",p]
            # Approximate liquid-water RH, only above freezing; not native AIFS RH.
            tc=T-273.15
            e=q*p/(.621981+(1-.621981)*q)
            es=6.112*np.exp(17.67*tc/(tc+243.5))
            rh=100*e/es
            rh[(tc<0)|(rh<0)|(rh>110)]=np.nan
            out["r",p]=rh
    for (var,p),arr in out.items():
        if p:arr[out["sp",0]<p]=np.nan
    return out,spacing,{"bytes":planned,"fields":records,"rh":"derived liquid-water RH, subfreezing points masked" if model=="aifs-single" else "native"}

def load_gfs(lead):
    path=OUT/"gfs-temp.grib2"
    try:
        meta=gfs.download(gfs.url_for(INIT,lead),path)
        fields,qc=gfs.read_fields(path,INIT,lead)
        fields["msl",0]=fields.pop(("mslp",0))
        for p in (850,700):fields["r",p]=fields.pop(("rh",p))
        return fields,.25,{**meta,**qc,"rh":"native"}
    finally:path.unlink(missing_ok=True)

def plot(data,lead,product,p):
    from spatial_v3_style import DOMAINS,figure,base,finish,raster,save
    if product=="wind":title=f"{p} hPa circulation";unit="m/s";levels=[0,2,4,6,8,10,12,16,20,25,35];cmap="YlGnBu";extend="max"
    elif product=="rh":title=f"{p} hPa relative humidity";unit="%";levels=list(range(0,111,10));cmap="BrBG";extend="both"
    else:title="Mean sea-level pressure and 1 hPa isobars";unit="hPa";levels=list(range(990,1021,2));cmap="RdBu_r";extend="both"
    for domain,b in DOMAINS.items():
        fig,axes,norm=figure(len(MODELS),title,domain,lead,INIT,"NOAA/NCEP and ECMWF open data",unit,levels,cmap,extend)
        for ax,(label,_,__) in zip(axes,MODELS):
            m=base(ax,domain);ax.set_title(label+(" · derived RH" if label.startswith("AIFS") and product=="rh" else ""),fontsize=11)
            if label in data:
                d,dx=data[label];x=np.linspace(110,132,round(22/dx)+1);y=np.linspace(-10,5,round(15/dx)+1)
                if product=="wind":
                    u,v=d["u",p],d["v",p];raster(ax,x,y,np.hypot(u,v),norm,cmap)
                    sx=(x>=b[0]-dx)&(x<=b[1]+dx);sy=(y>=b[2]-dx)&(y<=b[3]+dx)
                    ax.streamplot(x[sx],y[sy],np.ma.masked_invalid(u[np.ix_(sy,sx)]),np.ma.masked_invalid(v[np.ix_(sy,sx)]),density=.9,color="#253440",linewidth=.65,arrowsize=.7)
                elif product=="rh":raster(ax,x,y,d["r",p],norm,cmap)
                else:
                    a=d["msl",0];raster(ax,x,y,a,norm,cmap)
                    lev=np.arange(np.ceil(np.nanmin(a)),np.floor(np.nanmax(a))+1,1)
                    if len(lev):ax.clabel(ax.contour(x,y,a,levels=lev,colors="#333333",linewidths=.55),fmt="%.0f",fontsize=7)
            else:ax.text(.5,.5,"UNAVAILABLE",transform=ax.transAxes,ha="center")
            finish(ax,m,domain)
        save(fig,OUT/f"{product}{p or ''}_{domain}_f{lead:03d}.png")

def main():
    OUT.mkdir(exist_ok=False);manifest={"cycle":INIT.isoformat(),"leads":{}};failures=0
    for lead in LEADS:
        data={};rec={}
        for label,model,res in MODELS:
            try:
                fields,spacing,meta=load_gfs(lead) if model is None else ecmwf(model,res,lead)
                data[label]=(fields,spacing);rec[label]=meta
                print("PASS",lead,label,meta["bytes"],flush=True)
            except Exception as e:
                rec[label]={"error":str(e)};failures+=1;print("FAIL",lead,label,str(e),flush=True)
        for product,p in (("wind",925),("wind",850),("rh",850),("rh",700),("mslp",0)):plot(data,lead,product,p)
        manifest["leads"][str(lead)]=rec
        (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2))
        if lead!=LEADS[-1]:time.sleep(10)
    print("ECMWF attempted field bytes",used,flush=True)
    if failures:raise SystemExit(f"{failures} failures")
if __name__=="__main__":main()
