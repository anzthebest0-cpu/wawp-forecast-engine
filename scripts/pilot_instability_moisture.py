"""Research-only matched-cycle dynamics and instability, with three map scales."""
import json,hashlib,time,urllib.parse
from pathlib import Path
import eccodes as ec
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap
from pilot_pwv_reflectivity import INIT,LEADS,fetch
from pilot_circulation_comparison import crop,MODELS
from datetime import timedelta

OUT=Path("instability-moisture-pilot")
DOMAINS={"regional":(110,132,-10,5),"sulawesi":(119,128,-8,0),"kolaka":(120.5,123.5,-6,-3)}
COMMON={(v,p) for v in ("u","v") for p in (925,850,200)} | {("q",p) for p in (925,850,700)} | {("t",p) for p in (850,700,500)} | {("sp",0)}
BUDGET=180*1024**2
used=0
CODES={(0,2,2):"u",(0,2,3):"v",(0,1,0):"q",(0,0,0):"t",(0,3,0):"sp",(0,7,6):"cape",(0,7,7):"cin"}
def decode_message(data,key,lead,dx):
    """Read single message, verify identity, grid, time, units and crop."""
    var,p=key
    h=ec.codes_new_from_message(data)
    try:
        get=lambda k:ec.codes_get(h,k)
        short=get("shortName");typ=get("typeOfLevel")
        code=tuple(get(k) for k in ("discipline","parameterCategory","parameterNumber"))
        if var=="mucape":
            if short!="mucape":raise ValueError("Not MUCAPE")
        elif short!=var and CODES.get(code)!=var:
            raise ValueError("Wrong field "+str(key)+" "+short)
        if p and (typ!="isobaricInhPa" or get("level")!=p):raise ValueError("Wrong level")
        if not p and typ!=("mostUnstableParcel" if var=="mucape" else "surface"):raise ValueError("Wrong non-pressure level "+typ)
        if get("stepType")!="instant":raise ValueError("Not instantaneous")
        if var in ("u","v") and get("uvRelativeToGrid")!=0:raise ValueError("Grid-relative winds")
        units=get("units")
        valid_units={"u":("m s**-1","m/s"),"v":("m s**-1","m/s"),"t":("K",),"q":("kg kg**-1","kg kg-1","kg/kg"),"sp":("Pa",),"cape":("J kg**-1","J/kg"),"cin":("J kg**-1","J/kg"),"mucape":("J kg**-1","J/kg")}
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
        lo,hi={"u":(-150,150),"v":(-150,150),"q":(0,.06),"t":(180,340),"sp":(300,1100),"cape":(-1,15000),"mucape":(-1,15000),"cin":(-5000,1)}[var]
        if not np.isfinite(arr).any() or np.any(np.isfinite(arr)&((arr<lo)|(arr>hi))):raise ValueError("Range check "+str(key))
        if var=="sp" and not np.isfinite(arr).all():raise ValueError("Incomplete surface pressure")
        return arr,{"shortName":short,"code":code,"level_type":typ,"units":units,"finite":int(np.isfinite(arr).sum()),"min":float(np.nanmin(arr)),"max":float(np.nanmax(arr))}
    finally:ec.codes_release(h)

def read_gfs(lead):
    url="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?"
    q={"dir":"/gfs.20261125/00/atmos","file":f"gfs.t00z.pgrb2.0p25.f{lead:03d}","subregion":"","leftlon":110,"rightlon":132,"toplat":5,"bottomlat":-10,"lev_surface":"on"}
    q.update({f"lev_{p}_mb":"on" for p in (925,850,700,500,200)})
    q.update({f"var_{v}":"on" for v in ("UGRD","VGRD","SPFH","TMP","PRES","CAPE","CIN")})
    url+=urllib.parse.urlencode(q)
    data=fetch(url,8*1024**2);path=OUT/"temp.grib2";path.write_bytes(data)
    fields={};meta={};errors={};want=COMMON|{("cape",0),("cin",0)}
    try:
        with path.open("rb") as f:
            while (h:=ec.codes_grib_new_from_file(f)) is not None:
                try:
                    get=lambda k:ec.codes_get(h,k)
                    var=CODES.get(tuple(get(k) for k in ("discipline","parameterCategory","parameterNumber")))
                    typ=get("typeOfLevel");p=get("level") if typ=="isobaricInhPa" else 0
                    key=(var,p)
                    if key not in want or (not p and typ!="surface"):continue
                    if key in fields or str(key) in errors:raise ValueError("Duplicate "+str(key))
                    try:fields[key],meta[str(key)]=decode_message(ec.codes_get_message(h),key,lead,.25)
                    except Exception as e:errors[str(key)]=str(e)
                finally:ec.codes_release(h)
    finally:path.unlink(missing_ok=True)
    return fields,.25,{"bytes":len(data),"url":url,"sha256":hashlib.sha256(data).hexdigest(),"fields":meta,"errors":errors,"missing":sorted(map(str,want-set(fields)))}

def read_ecmwf(model,res,lead,only_mucape=False):
    global used
    dx=.1 if res=="0p1" else .25
    want={("mucape",0)} if only_mucape else COMMON|({("mucape",0)} if model=="ifs" else set())
    if only_mucape and model!="ifs":raise ValueError("MUCAPE unavailable for this model")
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

def diagnostics(fields,dx):
    d={};meta={}
    if ("sp",0) not in fields:return d,{"error":"No terrain mask"}
    for (v,p),a in fields.items():
        if p:a[fields["sp",0]<p]=np.nan
    # Horizontal specific-humidity flux convergence on pressure surfaces,
    # NOT vertically integrated moisture convergence. Metric terms on sphere.
    phi=np.deg2rad(np.linspace(-10,5,round(15/dx)+1))[:,None];dr=np.deg2rad(dx);radius=6371000.
    for p in (925,850):
        if all((v,p) in fields for v in ("q","u","v")):
            q,u,v=[fields[k,p] for k in ("q","u","v")]
            a=-(np.gradient(q*u,dr,axis=1)+np.gradient(q*v*np.cos(phi),dr,axis=0))/(radius*np.cos(phi))*3.6e6
            # Edges and missing/below-terrain neighbors excluded from derivative.
            a[[0,-1],:]=np.nan;a[:,[0,-1]]=np.nan;a[~np.isfinite(q*u*v)]=np.nan
            d[f"mfc{p}"]=a
            d[f"flux{p}"]=np.hypot(q*u,q*v)*1000
    if all((v,p) in fields for v in ("u","v") for p in (850,200)):
        d["shear"]=np.hypot(fields["u",200]-fields["u",850],fields["v",200]-fields["v",850])
    if all(k in fields for k in (("t",850),("t",700),("t",500),("q",850),("q",700))):
        t850,t700,t500=[fields["t",p]-273.15 for p in (850,700,500)]
        td850,td700=[dewpoint(fields["q",p],p) for p in (850,700)]
        ki=t850-t500+td850-(t700-td700)
        ki[(t850<0)|(t700<0)|(td850>t850+1)|(td700>t700+1)]=np.nan
        d["ki"]=ki
    for var in ("cape","cin","mucape"):
        if (var,0) in fields:d[var]=fields[var,0]
    for name,a in d.items():
        n=int(np.isfinite(a).sum())
        meta[name]={"finite":n,"min":float(np.nanmin(a)) if n else None,"max":float(np.nanmax(a)) if n else None}
    return d,meta

PRODUCTS={
"mfc925":("925 hPa horizontal moisture flux convergence","g kg⁻¹ h⁻¹",[-12,-8,-4,-2,-1,-.5,0,.5,1,2,4,8,12],"RdBu_r"),
"mfc850":("850 hPa horizontal moisture flux convergence","g kg⁻¹ h⁻¹",[-12,-8,-4,-2,-1,-.5,0,.5,1,2,4,8,12],"RdBu_r"),
"flux925":("925 hPa specific-humidity flux magnitude","g kg⁻¹ m s⁻¹",[0,20,40,60,80,100,150,200,300,400],"YlGnBu"),
"shear":("850–200 hPa vector wind shear","m s⁻¹",[0,5,10,15,20,25,30,40,50],"viridis"),
"ki":("K-index (liquid-water dewpoint)","°C",[-20,-10,0,10,20,25,30,35,40,45,50],"YlOrRd"),
"cape":("GFS surface-based CAPE","J kg⁻¹",[0,100,250,500,1000,1500,2000,3000,4000,6000],"YlOrRd"),
"cin":("GFS surface-based CIN","J kg⁻¹",[-400,-200,-100,-75,-50,-25,-10,0],"Blues_r"),
"mucape":("IFS most-unstable CAPE (not SBCAPE)","J kg⁻¹",[0,100,250,500,1000,1500,2000,3000,4000,6000],"YlOrRd")}
def plot(data,lead,product,domain):
    title,unit,levels,cmap=PRODUCTS[product];bounds=DOMAINS[domain]
    labels=[m[0] for m in MODELS if (product not in ("cape","cin","mucape") or (product in ("cape","cin") and m[1] is None) or (product=="mucape" and m[1]=="ifs"))]
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
    OUT.mkdir(exist_ok=False);manifest={"cycle":INIT.isoformat(),"domains":DOMAINS,"leads":{},"notes":"Pressure-surface moisture convergence, not vertically integrated; GFS SBCAPE/CIN distinct from IFS MUCAPE. K-index masks subfreezing lower levels. No inferred AIFS CAPE."};fail=0
    for lead in LEADS:
        rec={};data={}
        for label,model,res in MODELS:
            try:
                fields,dx,meta=read_gfs(lead) if model is None else read_ecmwf(model,res,lead)
                d,qc=diagnostics(fields,dx);meta["diagnostics"]=qc;rec[label]=meta;data[label]=(d,dx)
                expected={"mfc925","mfc850","flux925","shear","ki"}|({"cape","cin"} if model is None else {"mucape"} if model=="ifs" else set())
                missing=expected-{k for k,v in qc.items() if isinstance(v,dict) and v.get("finite",0)>0}
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
def mucape_only():
    OUT.mkdir(exist_ok=False)
    manifest={"cycle":INIT.isoformat(),"scope":"MUCAPE-only repair; previous successful diagnostics not repeated","leads":{},"domains":DOMAINS}
    fail=0
    for lead in LEADS:
        data={};record={}
        for label,model,res in MODELS:
            if model!="ifs":continue
            try:
                fields,dx,meta=read_ecmwf(model,res,lead,only_mucape=True)
                if ("mucape",0) not in fields or meta["errors"] or meta["missing"]:raise ValueError(str(meta))
                arr=fields["mucape",0]
                data[label]=({"mucape":arr},dx);record[label]=meta
                print("PASS_MUCAPE",lead,label,json.dumps(meta["fields"]),flush=True)
            except Exception as e:
                fail+=1;record[label]={"error":str(e)};print("FAIL_MUCAPE",lead,label,str(e),flush=True)
        for domain in DOMAINS:plot(data,lead,"mucape",domain)
        manifest["leads"][str(lead)]=record
        (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2))
        if lead!=LEADS[-1]:time.sleep(10)
    if fail:raise SystemExit(f"{fail} MUCAPE failures")

if __name__=="__main__":
    import sys
    if sys.argv[1:]==["--mucape-only"]:mucape_only()
    elif not sys.argv[1:]:main()
    else:raise SystemExit("Unknown arguments")
