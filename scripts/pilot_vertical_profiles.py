"""Matched-cycle model stability and resolved freezing-height research pilot."""
import json,hashlib,time,urllib.parse
from pathlib import Path
import eccodes as ec
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap
from pilot_pwv_reflectivity import INIT,LEADS,fetch
from pilot_circulation_comparison import MODELS
import metpy.calc as mp
from metpy.units import units
from datetime import timedelta

OUT=Path("vertical-profile-pilot")
DOMAINS={"regional":(110,132,-10,5),"sulawesi":(119,128,-8,0),"kolaka":(120.5,123.5,-6,-3)}
LEVELS=(925,850,700,600,500,400)
COMMON={(v,p) for v in ("t","z") for p in LEVELS}|{("q",850),("sp",0),("2t",0),("2d",0)}
BUDGET=230*1024**2
used=0
CODES={(0,1,0):"q",(0,0,0):"t",(0,3,0):"sp",(0,3,5):"z",(0,0,6):"2d"}
def decode_message(data,key,lead,dx):
    """Read single message, verify identity, grid, time, units and crop."""
    var,p=key
    h=ec.codes_new_from_message(data)
    try:
        get=lambda k:ec.codes_get(h,k)
        short=get("shortName");typ=get("typeOfLevel")
        code=tuple(get(k) for k in ("discipline","parameterCategory","parameterNumber"))
        if short!=var and not (var=="z" and (short=="gh" or code==(0,3,5))) and not (var=="2t" and code==(0,0,0)) and CODES.get(code)!=var:
            raise ValueError("Wrong field "+str(key)+" "+short)
        if p and (typ!="isobaricInhPa" or get("level")!=p):raise ValueError("Wrong level")
        if not p and var in ("2t","2d") and (typ!="heightAboveGround" or get("level")!=2):raise ValueError("Wrong 2m level "+typ)
        if not p and var=="sp" and typ!="surface":raise ValueError("Wrong surface level "+typ)
        if get("stepType")!="instant":raise ValueError("Not instantaneous")
        if var in ("u","v") and get("uvRelativeToGrid")!=0:raise ValueError("Grid-relative winds")
        units=get("units")
        valid_units={"t":("K",),"2t":("K",),"2d":("K",),"q":("kg kg**-1","kg kg-1","kg/kg"),"sp":("Pa",),"z":("m**2 s**-2","m2 s-2","gpm","m")}
        if units not in valid_units[var]:raise ValueError("Unexpected units "+units)
        for stamp,dk,tk in [(INIT,"dataDate","dataTime"),(INIT+timedelta(hours=lead),"validityDate","validityTime")]:
            if (get(dk),get(tk))!=(int(stamp.strftime("%Y%m%d")),int(stamp.strftime("%H%M"))):raise ValueError("Wrong time")
        if get("gridType")!="regular_ll" or abs(get("iDirectionIncrementInDegrees")-dx)>1e-6 or abs(get("jDirectionIncrementInDegrees")-dx)>1e-6:raise ValueError("Wrong grid")
        x,y,a=[np.asarray(ec.codes_get_array(h,k),dtype=float) for k in ("longitudes","latitudes","values")]
        if get("bitmapPresent"):a[np.asarray(ec.codes_get_array(h,"bitmap"))==0]=np.nan
        x=x%360
        s=(x>=109.99999)&(x<=132.00001)&(y>=-10.00001)&(y<=5.00001)
        x,y,a=x[s],y[s],a[s];nx,ny=round(22/dx)+1,round(15/dx)+1
        ix=np.rint((x-110)/dx).astype(int);iy=np.rint((y+10)/dx).astype(int)
        if len(a)!=nx*ny or np.any(ix<0) or np.any(ix>=nx) or np.any(iy<0) or np.any(iy>=ny) or len(np.unique(iy*nx+ix))!=len(a):raise ValueError("Incomplete grid")
        if not np.allclose(x,110+ix*dx,atol=1e-5,rtol=0) or not np.allclose(y,-10+iy*dx,atol=1e-5,rtol=0):raise ValueError("Wrong coordinates")
        arr=np.full((ny,nx),np.nan);arr[iy,ix]=a
        if var=="sp":arr/=100
        if var=="z" and units in ("m**2 s**-2","m2 s-2"):arr/=9.80665
        lo,hi={"q":(0,.06),"t":(180,340),"2t":(180,340),"2d":(150,340),"sp":(300,1100),"z":(-1000,20000)}[var]
        if not np.isfinite(arr).any() or np.any(np.isfinite(arr)&((arr<lo)|(arr>hi))):raise ValueError("Range check "+str(key))
        if var=="sp" and not np.isfinite(arr).all():raise ValueError("Incomplete surface pressure")
        return arr,{"shortName":short,"code":code,"level_type":typ,"units":units,"finite":int(np.isfinite(arr).sum()),"min":float(np.nanmin(arr)),"max":float(np.nanmax(arr))}
    finally:ec.codes_release(h)

def read_gfs(lead):
    url="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"
    q={"dir":"/gfs.20261125/00/atmos","file":f"gfs.t00z.pgrb2.0p25.f{lead:03d}","subregion":"","leftlon":110,"rightlon":132,"toplat":5,"bottomlat":-10,"lev_surface":"on","lev_2_m_above_ground":"on"}
    q.update({f"lev_{p}_mb":"on" for p in LEVELS})
    q.update({f"var_{v}":"on" for v in ("SPFH","TMP","PRES","HGT","DPT")})
    url+=urllib.parse.urlencode(q)
    data=fetch(url,8*1024**2);path=OUT/"temp.grib2";path.write_bytes(data)
    fields={};meta={};errors={};want=COMMON
    try:
        with path.open("rb") as f:
            while (h:=ec.codes_grib_new_from_file(f)) is not None:
                try:
                    get=lambda k:ec.codes_get(h,k)
                    var=CODES.get(tuple(get(k) for k in ("discipline","parameterCategory","parameterNumber")))
                    typ=get("typeOfLevel");p=get("level") if typ=="isobaricInhPa" else 0
                    if typ=="heightAboveGround" and get("level")==2 and var=="t":var="2t"
                    key=(var,p)
                    if key not in want or (not p and typ not in ("surface","heightAboveGround")):continue
                    if not p and var in ("2t","2d") and (typ!="heightAboveGround" or get("level")!=2):continue
                    if not p and var=="sp" and typ!="surface":continue
                    if key in fields or str(key) in errors:raise ValueError("Duplicate "+str(key))
                    try:fields[key],meta[str(key)]=decode_message(ec.codes_get_message(h),key,lead,.25)
                    except Exception as e:errors[str(key)]=str(e)
                finally:ec.codes_release(h)
    finally:path.unlink(missing_ok=True)
    return fields,.25,{"bytes":len(data),"url":url,"sha256":hashlib.sha256(data).hexdigest(),"fields":meta,"errors":errors,"missing":sorted(map(str,want-set(fields)))}

def read_ecmwf(model,res,lead):
    global used
    dx=.1 if res=="0p1" else .25
    want=COMMON
    entries={}
    for suffix in ("-pl","-sfc") if res=="0p1" else ("",):
        url=f"https://data.ecmwf.int/forecasts/20261125/00z/{model}/{res}/oper/20261125000000-{lead}h-oper-fc{suffix}.grib2"
        for line in fetch(url.removesuffix(".grib2")+".index",4*1024**2).splitlines():
            if not line.strip():continue
            row=json.loads(line);lev=row.get("levtype")
            if lev not in ("pl","sfc"):continue
            key=(row.get("param"),int(row["levelist"]) if lev=="pl" else 0)
            if key not in want:continue
            if key in entries:raise ValueError("Duplicate index "+str(key))
            start,size=row["_offset"],row["_length"]
            if not isinstance(start,int) or start<0 or not isinstance(size,int) or not 0<size<=8*1024**2:raise ValueError("Bad index")
            entries[key]=(url,start,size)
    planned=sum(v[2] for v in entries.values())
    if used+planned>BUDGET:raise ValueError("Transfer budget")
    fields={};meta={};errors={}
    for key,(url,start,size) in sorted(entries.items()):
        used+=size
        try:
            data=fetch(url,size,(start,size))
            fields[key],info=decode_message(data,key,lead,dx)
            meta[str(key)]={**info,"bytes":size,"url":url,"offset":start,"sha256":hashlib.sha256(data).hexdigest()}
        except Exception as e:errors[str(key)]=str(e)
    return fields,dx,{"bytes":planned,"fields":meta,"errors":errors,"missing":sorted(map(str,want-set(fields)))}

def dewpoint(q,p):
    e=q*p/(.621981+(1-.621981)*q)
    log=np.log(np.where(e>0,e/6.112,np.nan))
    return 243.5*log/(17.67-log)


def parcel500(p,t,td):
    """Vectorized dry ascent and liquid pseudoadiabatic ascent; MetPy LCL."""
    p,t,td=np.broadcast_arrays(p,t,td)
    out=np.full(p.shape,np.nan)
    good=np.isfinite(p+t+td)&(p>500)&(t>=180)&(t<=340)&(td>=150)&(td<=t+0.5)
    if not good.any():return out
    p,t,td=p[good],t[good],np.minimum(td[good],t[good])
    pl,tl=mp.lcl(p*units.hPa,t*units.K,td*units.K)
    pl,tl=pl.to("hPa").m,tl.to("K").m
    dry=t*(500/p)**(287.04749/1004.6662)
    moist=pl>500
    if moist.any():
        P=pl[moist];T=tl[moist].copy();h=(np.log(500)-np.log(P))/80;logp=np.log(P)
        def deriv(x,y):
            # MetPy liquid-water saturation vapor pressure and constant Lv.
            es=mp.saturation_vapor_pressure(y*units.K,phase="liquid").to("hPa").m
            rs=.62195691*es/(np.exp(x)-es)
            return (287.04749*y+2.50084e6*rs)/(1004.6662+2.50084e6**2*rs*.62195691/(287.04749*y*y))
        for _ in range(80):
            k1=deriv(logp,T);k2=deriv(logp+h/2,T+h*k1/2)
            k3=deriv(logp+h/2,T+h*k2/2);k4=deriv(logp+h,T+h*k3)
            T+=h*(k1+2*k2+2*k3+k4)/6;logp+=h
        dry[moist]=T
    out[good]=dry
    # Independent scalar MetPy reference on deterministic actual grid samples.
    err=[]
    # p/t/td now contain the valid subset in flattened order.
    flat=dry
    for i in np.linspace(0,len(flat)-1,min(12,len(flat)),dtype=int):
        expected=mp.parcel_profile(np.array([p[i],500])*units.hPa,t[i]*units.K,td[i]*units.K)[-1].to("K").m
        err.append(abs(float(expected-flat[i])))
    if max(err)>0.25:raise ValueError("Parcel solver differs from MetPy by "+str(max(err))+" K")
    print("PARCEL_REFERENCE_QC",len(err),max(err),flush=True)
    return out

def diagnostics(fields,dx):
    d={};meta={}
    if ("sp",0) not in fields:return d,{"error":"No surface pressure"}
    sp=fields["sp",0]
    for (v,p),a in fields.items():
        if p:a[sp<p]=np.nan
    if ("t",500) in fields:
        t500=fields["t",500]
        if all(k in fields for k in (("2t",0),("2d",0))):
            d["li"]=t500-parcel500(sp,fields["2t",0],fields["2d",0])
        if all(k in fields for k in (("t",850),("q",850))):
            t850=fields["t",850];td850=dewpoint(fields["q",850],850)+273.15
            d["si"]=t500-parcel500(np.full(t850.shape,850.),t850,td850)
    if ("z",500) in fields:d["z500"]=fields["z",500]
    if all((v,p) in fields for v in ("t","z") for p in LEVELS):
        # First *resolved* crossing between adjacent pressure levels. Mask
        # columns without a warm first usable level or missing interior levels.
        t=np.stack([fields["t",p]-273.15 for p in LEVELS]);z=np.stack([fields["z",p] for p in LEVELS])
        valid=np.isfinite(t)&np.isfinite(z)
        anyvalid=valid.any(axis=0);first=np.argmax(valid,axis=0)
        warm=anyvalid&(np.take_along_axis(t,first[None],axis=0)[0]>=0)
        shape=sp.shape;out=np.full(shape,np.nan);intact=warm.copy()
        for i in range(len(LEVELS)-1):
            intact&=((first>i)|valid[i])
            good=intact&(first<=i)&valid[i]&valid[i+1]&(t[i]>=0)&(t[i+1]<0)&(z[i+1]>z[i])&~np.isfinite(out)
            frac=np.divide(t[i],t[i]-t[i+1],out=np.zeros(shape),where=good)
            out[good]=(z[i]+frac*(z[i+1]-z[i]))[good]
        d["freeze"]=out
    for name,a in d.items():
        n=int(np.isfinite(a).sum())
        meta[name]={"finite":n,"min":float(np.nanmin(a)) if n else None,"max":float(np.nanmax(a)) if n else None}
    return d,meta

PRODUCTS={
"li":("Approximate surface-based Lifted Index (2 m parcel at surface pressure)","K",[-15,-10,-8,-6,-4,-2,0,2,4,6,10,15],"RdBu"),
"si":("Showalter Index (850 hPa parcel)","K",[-15,-10,-8,-6,-4,-2,0,2,4,6,10,15],"RdBu"),
"freeze":("Lowest resolved warm-to-cold 0°C crossing, geopotential height MSL","m",[1000,2000,3000,3500,4000,4500,4750,5000,5250,5500,6000,7000],"viridis"),
"z500":("500 hPa geopotential height","gpm",list(range(5500,6051,25)),"viridis")}
def plot(data,lead,product,domain):
    title,unit,levels,cmap=PRODUCTS[product];bounds=DOMAINS[domain]
    labels=[m[0] for m in MODELS]
    fig,axes=plt.subplots(1 if len(labels)<=2 else 2,min(2,len(labels)),figsize=(8*min(2,len(labels)),5.6*(1 if len(labels)<=2 else 2)),layout="constrained",squeeze=False)
    for ax,label in zip(axes.flat,labels):
        ax.set_title(label)
        m=Basemap(projection="cyl",llcrnrlon=bounds[0],urcrnrlon=bounds[1],llcrnrlat=bounds[2],urcrnrlat=bounds[3],resolution="i",ax=ax)
        ax.set_facecolor("#d0d0d0")
        if label in data and product in data[label][0]:
            d,dx=data[label];x=np.linspace(110,132,round(22/dx)+1);y=np.linspace(-10,5,round(15/dx)+1)
            fill=ax.contourf(x,y,np.ma.masked_invalid(d[product]),levels=levels,cmap=cmap,extend="both")
            fig.colorbar(fill,ax=ax,orientation="horizontal",shrink=.85,label=unit)
        else:ax.text(.5,.5,"UNAVAILABLE",ha="center",va="center",transform=ax.transAxes)
        m.drawcoastlines(linewidth=.7);m.drawcountries(linewidth=.4)
        step=5 if domain=="regional" else 2 if domain=="sulawesi" else 1
        m.drawparallels(np.arange(-10,6,step),labels=[1,0,0,0],fontsize=8,linewidth=.3)
        m.drawmeridians(np.arange(110,133,step),labels=[0,0,0,1],fontsize=8,linewidth=.3)
        ax.set_xlim(bounds[:2]);ax.set_ylim(bounds[2:]);ax.plot(121.525,-4.338,"k*",ms=8);ax.annotate("WAWP",(121.525,-4.338),xytext=(4,5),textcoords="offset points",fontsize=8)
    valid=INIT+timedelta(hours=lead)
    fig.suptitle(f"{title} | {domain} | H+{lead} valid {valid:%d %b %Y %HZ}\nInit {INIT:%d %b %Y %HZ} · NOAA/NCEP and ECMWF · Research only · Zoom does not improve resolution",fontsize=13)
    fig.savefig(OUT/f"{product}_{domain}_f{lead:03d}.png",dpi=115);plt.close(fig)

def main():
    OUT.mkdir(exist_ok=False);manifest={"cycle":INIT.isoformat(),"domains":DOMAINS,"leads":{},"notes":"Approximate 2m surface parcel LI; 850hPa SI; liquid pseudoadiabatic integration checked against sampled MetPy scalar solutions; freezing is a resolved pressure-level crossing, not exact first crossing or icing forecast; geopotential height MSL. No observed profiles."};fail=0
    for lead in LEADS:
        rec={};data={}
        for label,model,res in MODELS:
            try:
                fields,dx,meta=read_gfs(lead) if model is None else read_ecmwf(model,res,lead)
                d,qc=diagnostics(fields,dx);meta["diagnostics"]=qc;rec[label]=meta;data[label]=(d,dx)
                missing=set(PRODUCTS)-{k for k,v in qc.items() if isinstance(v,dict) and v.get("finite",0)>0}
                if missing or meta["errors"] or meta["missing"]:fail+=1
                print("RESULT",lead,label,meta["bytes"],"missing",sorted(missing),meta["missing"],"errors",meta["errors"],"diag",json.dumps(qc),flush=True)
            except Exception as e:fail+=1;rec[label]={"error":str(e)};print("FAIL",lead,label,str(e),flush=True)
        for product in PRODUCTS:
            for domain in DOMAINS:plot(data,lead,product,domain)
        manifest["leads"][str(lead)]=rec
        (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2))
        if lead!=LEADS[-1]:time.sleep(10)
    print("ECMWF attempted bytes",used,flush=True)
    if fail:raise SystemExit(f"{fail} incomplete model/lead results")
if __name__=="__main__":main()
