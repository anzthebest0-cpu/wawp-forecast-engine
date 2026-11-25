"""Observed radiosonde diagnostics; bounded one-time NOAA IGRA YTD research pilot."""
import io,json,hashlib,urllib.request,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap
import metpy.calc as mp
from metpy.units import units
from metpy.plots import SkewT

ROOT="https://www.ncei.noaa.gov/pub/data/igra/"
OUT=Path("rason-pilot")
TARGET=datetime(2026,11,25,0,tzinfo=timezone.utc)
STATIONS=("IDM00096935","IDM00097180","IDM00097230","IDM00097372","IDM00097502")
# Kupang lies just beyond original southern limit; expand observed network map only.
MAX_ZIP=5*1024**2
MAX_RAW=40*1024**2
def get(path,cap):
    req=urllib.request.Request(ROOT+path,headers={"User-Agent":"TITAN-WAWP-rason-research/1.0"})
    with urllib.request.urlopen(req,timeout=60) as f:
        if not f.geturl().startswith(ROOT):raise ValueError("Unexpected redirect")
        data=f.read(cap+1)
    if len(data)>cap:raise ValueError("Payload over cap")
    return data
def station_list():
    result={}
    for line in get("igra2-station-list.txt",1024**2).decode("ascii").splitlines():
        sid=line[:11]
        if sid in STATIONS:
            result[sid]={"station":sid,"name":line[41:71].strip(),"lat":float(line[12:20]),"lon":float(line[21:30]),"last_year":int(line[77:81])}
    if set(result)!=set(STATIONS):raise ValueError("Station metadata incomplete")
    return result
def records(text,sid):
    lines=iter(text.splitlines())
    for line in lines:
        if not line.startswith("#"):raise ValueError("Unexpected header")
        if line[1:12]!=sid:raise ValueError("Wrong station")
        n=int(line[32:36])
        if not 0<n<=10000:raise ValueError("Invalid level count")
        rows=[next(lines) for _ in range(n)]
        hour=int(line[24:26])
        if hour==99:continue
        ts=datetime(int(line[13:17]),int(line[18:20]),int(line[21:23]),hour,tzinfo=timezone.utc)
        if TARGET-timedelta(days=5)<=ts<=TARGET:
            yield ts,rows,line
def profile(rows):
    values={}
    surface=None
    for line in rows:
        if len(line)<51:raise ValueError("Truncated data record")
        typ1,typ2=line[0],line[1]
        p=int(line[9:15]);t=int(line[22:27]);dpd=int(line[34:39]);z=int(line[16:21])
        # Pressure, T and dewpoint depression required. Respect negative sentinels.
        if p<=0 or t in (-9999,-8888) or dpd in (-9999,-8888) or dpd<0:continue
        ph=p/100;tc=t/10;td=tc-dpd/10
        if not 5<=ph<=1100 or not -100<=tc<=60 or not -140<=td<=tc:continue
        if typ2=="1":
            if surface is not None and surface!=ph:raise ValueError("Conflicting surface levels")
            surface=ph
        if ph in values and not np.allclose(values[ph][:2],[tc,td],atol=0,rtol=0):raise ValueError("Conflicting duplicate pressure")
        values[ph]=(tc,td,z if z not in (-9999,-8888) else np.nan)
    if surface is None:raise ValueError("No complete explicitly reported surface P/T/Td")
    keys=sorted((p for p in values if p<=surface),reverse=True)
    p=np.asarray(keys);t,td,z=np.asarray([values[k] for k in keys]).T
    if len(p)<20 or p[0]!=surface or p[-1]>150 or surface-p[0]>1e-6:raise ValueError("Insufficient profile extent")
    # Large gaps in lower troposphere can dominate parcel estimates.
    if np.sum(p>=surface-100)<3 or np.max(-np.diff(p[p>=500]),initial=0)>100 or np.max(-np.diff(p),initial=0)>175:
        raise ValueError("Sparse vertical thermodynamic sampling")
    return p*units.hPa,t*units.degC,td*units.degC,z
def calc(rows):
    p,t,td,z=profile(rows)
    # Distinguish parcel calculations; require EL for positive CAPE to avoid truncated integration.
    results={}
    for name in ("sb","ml","mu"):
        try:
            if name=="sb":
                pp,tt,dd=p,t,td;parcel=mp.parcel_profile(pp,tt[0],dd[0])
                cape,cin=mp.surface_based_cape_cin(p,t,td)
            elif name=="mu":
                _,_,_,i=mp.most_unstable_parcel(p,t,td,depth=300*units.hPa)
                pp,tt,dd=p[i:],t[i:],td[i:];parcel=mp.parcel_profile(pp,tt[0],dd[0])
                cape,cin=mp.most_unstable_cape_cin(p,t,td,depth=300*units.hPa)
            else:
                # Match MetPy mixed-layer implementation: 100 hPa layer.
                cape,cin=mp.mixed_layer_cape_cin(p,t,td,depth=100*units.hPa)
                # Explicit EL check for mixed parcel via equivalent starting profile.
                sp,st,sd=mp.mixed_parcel(p,t,td,depth=100*units.hPa)
                mask=p<sp-100*units.hPa
                pp=np.concatenate(([sp.m],p[mask].m))*units.hPa
                tt=np.concatenate(([st.to("degC").m],t[mask].m))*units.degC
                dd=np.concatenate(([sd.to("degC").m],td[mask].m))*units.degC
                parcel=mp.parcel_profile(pp,tt[0],dd[0])
            c=float(cape.to("J/kg").m);n=float(cin.to("J/kg").m)
            if not np.isfinite(c) or not np.isfinite(n) or c<0 or n>1e-8:raise ValueError("CAPE/CIN QC")
            if c>0:
                el,_=mp.el(pp,tt,dd,parcel_temperature_profile=parcel)
                if not np.isfinite(el.m):raise ValueError("Positive CAPE but no resolved equilibrium level")
            results[name]={"cape":c,"cin":n}
        except Exception as e:results[name]={"error":str(e)}
    # Actual profile-derived Showalter, K-index and first 0 C crossing.
    def interp(a,h):
        if h>p[0].m or h<p[-1].m:raise ValueError("Level outside profile")
        return np.interp(np.log(h),np.log(p.m[::-1]),a.m[::-1])
    try:
        t850,t700,t500=[interp(t,h) for h in (850,700,500)]
        d850,d700=[interp(td,h) for h in (850,700)]
        parcel500=mp.parcel_profile(np.array([850,700,500])*units.hPa,t850*units.degC,d850*units.degC)[-1].to("degC").m
        results["si"]=float(t500-parcel500)
        results["ki"]=float(t850-t500+d850-(t700-d700))
    except Exception as e:results["si_ki_error"]=str(e)
    for i in range(len(t)-1):
        if t.m[i]>=0 and t.m[i+1]<0:
            f=t.m[i]/(t.m[i]-t.m[i+1])
            results["freezing_pressure_hpa"]=float(np.exp(np.log(p.m[i])+f*(np.log(p.m[i+1])-np.log(p.m[i]))))
            if np.isfinite(z[i:i+2]).all() and z[i+1]>=z[i]:
                results["freezing_height_m_msl"]=float(z[i]+f*(z[i+1]-z[i]))
            break
    return results,(p,t,td)
def sounding_plot(profile,record):
    p,t,td=profile
    fig=plt.figure(figsize=(8,8));sk=SkewT(fig,rotation=45)
    sk.plot(p,t,"r",linewidth=1.6,label="Observed T")
    sk.plot(p,td,"g",linewidth=1.6,label="Observed Td")
    parcel=mp.parcel_profile(p,t[0],td[0]);sk.plot(p,parcel,"k",linewidth=1,label="Surface parcel")
    sk.ax.set_ylim(1050,100);sk.ax.set_xlim(-45,45);sk.plot_dry_adiabats();sk.plot_moist_adiabats();sk.plot_mixing_lines()
    sk.ax.legend(loc="upper right")
    fig.suptitle(f"{record['name']} | {record['time_utc']}\nIGRA observations · research only")
    fig.savefig(OUT/f"skewt_{record['station']}.png",dpi=130);plt.close(fig)
def map_points(records,target,kind):
    fig,ax=plt.subplots(figsize=(12,8),layout="constrained")
    m=Basemap(projection="cyl",llcrnrlon=110,urcrnrlon=133,llcrnrlat=-12,urcrnrlat=5,resolution="i",ax=ax)
    m.drawcoastlines(linewidth=.6);m.drawcountries(linewidth=.4);m.fillcontinents(color="#eeeae3",lake_color="#e3eff4");m.drawmapboundary(fill_color="#e3eff4")
    m.drawparallels(np.arange(-12,6,3),labels=[1,0,0,0],fontsize=9);m.drawmeridians(np.arange(110,134,3),labels=[0,0,0,1],fontsize=9)
    from matplotlib.colors import Normalize
    norm=Normalize(0,4000);cmap=plt.get_cmap("YlOrRd")
    n=0
    for r in records:
        current=r.get("selected",{})
        value=current.get("diagnostics",{}).get(kind,{}).get("cape") if current.get("time_utc")==target else None
        x,y=r["lon"],r["lat"]
        if value is None:
            ax.scatter(x,y,s=85,facecolor="white",edgecolor="#777777",marker="x")
            label=r["station"][-5:]+" · unavailable"
        else:
            n+=1;ax.scatter(x,y,s=110,c=[value],cmap=cmap,norm=norm,edgecolor="black");label=f"{r['station'][-5:]} · {value:.0f}"
        ax.annotate(label,(x,y),xytext=(5,7),textcoords="offset points",fontsize=9)
    ax.plot(121.525,-4.338,"k*",ms=10);ax.annotate("WAWP (no sounding)",(121.525,-4.338),xytext=(5,-13),textcoords="offset points",fontsize=9)
    fig.colorbar(plt.cm.ScalarMappable(norm=norm,cmap=cmap),ax=ax,orientation="horizontal",shrink=.7,label="CAPE (J/kg)")
    fig.suptitle(f"Observed {kind.upper()}CAPE | {target} | {n}/{len(records)} valid stations\nIGRA v2.2 · point observations, no interpolation · historical research")
    fig.savefig(OUT/f"observed_{kind}cape.png",dpi=140);plt.close(fig)
def main():
    OUT.mkdir(exist_ok=False)
    stations=station_list();inventory=[]
    for sid,r in stations.items():
        try:
            path=f"data/data-y2d/{sid}-data-beg2026.txt.zip"
            data=get(path,MAX_ZIP)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                members=[m for m in z.infolist() if m.filename.endswith(".txt") and not m.is_dir()]
                if len(members)!=1 or members[0].file_size>MAX_RAW:raise ValueError("Unexpected archive contents")
                text=z.read(members[0]).decode("ascii")
            found=list(records(text,sid));r.update({"url":ROOT+path,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"cycles":[ts.isoformat() for ts,_,_ in found]})
            r["available"]={}
            for ts,rows,h in found:
                try:
                    diag,p=calc(rows)
                    key=ts.isoformat()
                    if key in r["available"]:raise ValueError("Duplicate cycle")
                    r["available"][key]={"time_utc":key,"release_hhmm":h[27:31],"levels":len(p[0]),"diagnostics":diag,"profile":p}
                except Exception as e:
                    print("PROFILE_REJECT",sid,ts.isoformat(),str(e),flush=True)
        except Exception as e:r["error"]=str(e);r["available"]={}
        inventory.append(r)
    # Latest shared nominal 00/12Z with >=2 SBCAPE values. Do not mix times.
    cycles=sorted({k for r in inventory for k in r["available"]},reverse=True)
    eligible=[k for k in cycles if datetime.fromisoformat(k).hour in (0,12) and sum("cape" in r["available"].get(k,{}).get("diagnostics",{}).get("sb",{}) for r in inventory)>=2]
    target=eligible[0] if eligible else TARGET.isoformat()
    for r in inventory:
        a=r.pop("available");selected=a.get(target)
        if selected:
            p=selected.pop("profile");r["selected"]=selected;sounding_plot(p,{**r,**selected})
        r["latest_valid_cycle"]=max(a,default=None)
        print("STATION_RESULT",json.dumps(r),flush=True)
    for kind in ("sb","ml","mu"):map_points(inventory,target,kind)
    manifest={"requested_cycle":TARGET.isoformat(),"selected_common_cycle":target,"retrieved_utc":datetime.now(timezone.utc).isoformat(),"stations":inventory,"notes":"Archive latency; not current live data. No spatial interpolation; missing not zero. MetPy parcel methods: surface, 100 hPa mixed layer, 300 hPa most unstable. Positive CAPE requires resolved EL. Pressure/height freezing crossing based on actual profile."}
    (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2))
    n=sum("cape" in r.get("selected",{}).get("diagnostics",{}).get("sb",{}) for r in inventory)
    print("COMMON_CYCLE",target,"valid_SBCAPE_stations",n,flush=True)
    if n<2:raise SystemExit("Insufficient simultaneous observed CAPE coverage")
if __name__=="__main__":main()
