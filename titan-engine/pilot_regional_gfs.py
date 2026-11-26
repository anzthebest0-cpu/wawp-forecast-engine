"""One-cycle experimental regional GFS plots, not operational guidance."""
import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes as ec
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap

BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
LON = np.arange(110, 132.01, .25)
LAT = np.arange(-10, 5.01, .25)
LEADS = (0, 6, 12)
LEVELS = (925, 850, 700)
LIMIT = 8 * 1024 * 1024
REQUIRED = {(v, p) for v in ("u", "v") for p in (925, 850)} | {("rh", p) for p in (850, 700)} | {("sp", 0), ("mslp", 0)}
UTC = timezone.utc

def url_for(init, lead):
    q = {"dir": init.strftime("/gfs.%Y%m%d/%H/atmos"), "file": f"gfs.t{init:%H}z.pgrb2.0p25.f{lead:03d}", "subregion": "", "leftlon": 110, "rightlon": 132, "toplat": 5, "bottomlat": -10}
    q.update({f"lev_{p}_mb": "on" for p in LEVELS})
    q.update({"lev_surface": "on", "lev_mean_sea_level": "on"})
    q.update({f"var_{v}": "on" for v in ("UGRD", "VGRD", "RH", "PRES", "PRMSL")})
    return BASE + "?" + urllib.parse.urlencode(q)

def download(url, path):
    request = urllib.request.Request(url, headers={"User-Agent": "TITAN-WAWP-research-pilot/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if urllib.parse.urlsplit(response.geturl()).hostname != "nomads.ncep.noaa.gov":
            raise ValueError("Unexpected redirect")
        data = response.read(LIMIT + 1)
    if len(data) > LIMIT or not data.startswith(b"GRIB") or not data.endswith(b"7777"):
        raise ValueError("Unexpected or oversized GRIB payload")
    path.write_bytes(data)
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "url": url}

def read_fields(path, init, lead):
    out, provenance = {}, []
    valid = init + timedelta(hours=lead)
    params = {(0, 2, 2): "u", (0, 2, 3): "v", (0, 1, 1): "rh", (0, 3, 0): "sp", (0, 3, 1): "mslp"}
    with path.open("rb") as f:
        while (g := ec.codes_grib_new_from_file(f)) is not None:
            try:
                get = lambda name: ec.codes_get(g, name)
                var = params.get(tuple(get(k) for k in ("discipline", "parameterCategory", "parameterNumber")))
                typ, level = get("typeOfLevel"), get("level")
                key = (var, int(level)) if typ == "isobaricInhPa" else (var, 0)
                if key not in REQUIRED:
                    continue
                if var == "sp" and typ != "surface" or var == "mslp" and typ != "meanSea":
                    continue
                if var in ("u", "v", "rh") and typ != "isobaricInhPa":
                    continue
                if key in out:
                    raise ValueError(f"Duplicate field {key}")
                for stamp, daykey, timekey in ((init, "dataDate", "dataTime"), (valid, "validityDate", "validityTime")):
                    if (get(daykey), get(timekey)) != (int(stamp.strftime("%Y%m%d")), int(stamp.strftime("%H%M"))):
                        raise ValueError(f"Time mismatch {key}")
                if get("gridType") != "regular_ll" or abs(get("iDirectionIncrementInDegrees")-.25)>1e-6 or abs(get("jDirectionIncrementInDegrees")-.25)>1e-6:
                    raise ValueError("Unexpected grid")
                if var in ("u", "v") and get("uvRelativeToGrid") != 0:
                    raise ValueError("Wind not earth relative")
                units = get("units")
                if units not in {"u": ("m s**-1", "m/s"), "v": ("m s**-1", "m/s"), "rh": ("%",), "sp": ("Pa",), "mslp": ("Pa",)}[var]:
                    raise ValueError(f"Unexpected units {key}: {units}")
                x, y, values = (np.asarray(ec.codes_get_array(g, k), dtype=float) for k in ("longitudes", "latitudes", "values"))
                x = x % 360
                ix, iy = np.rint((x-LON[0])/.25).astype(int), np.rint((y-LAT[0])/.25).astype(int)
                if len(values) != len(LON)*len(LAT) or np.any(ix<0) or np.any(ix>=len(LON)) or np.any(iy<0) or np.any(iy>=len(LAT)):
                    raise ValueError("Wrong extent or grid dimensions")
                if not np.allclose(x,LON[ix],atol=1e-6,rtol=0) or not np.allclose(y,LAT[iy],atol=1e-6,rtol=0) or len(np.unique(iy*len(LON)+ix))!=len(values):
                    raise ValueError("Grid mismatch or duplicated points")
                if get("bitmapPresent"):
                    values[np.asarray(ec.codes_get_array(g,"bitmap"))==0]=np.nan
                arr=np.full((len(LAT),len(LON)),np.nan);arr[iy,ix]=values
                if var in ("sp","mslp"):
                    arr /= 100
                    if not np.isfinite(arr).all() or np.any((arr<300)|(arr>1100)):
                        raise ValueError("Invalid pressure field")
                elif var=="rh":
                    # Reject substantial out-of-range values, allow small supersaturation
                    if np.any(np.isfinite(arr)&((arr < -1)|(arr > 110))):
                        raise ValueError("RH range failure")
                elif np.any(np.isfinite(arr)&(np.abs(arr)>150)):
                    raise ValueError("Wind range failure")
                out[key]=arr
                provenance.append({"field":var,"level":level,"level_type":typ,"units":units,"finite_cells":int(np.isfinite(arr).sum())})
            finally:
                ec.codes_release(g)
    if set(out)!=REQUIRED:
        raise ValueError(f"Missing fields: {REQUIRED-set(out)}")
    masked={}
    for (var,p),arr in out.items():
        if p:
            below = out["sp",0] < p
            arr[below] = np.nan
            masked[f"{var}_{p}"] = int(below.sum())
            if not np.isfinite(arr).any(): raise ValueError("No usable values")
    return out, {"fields":provenance,"terrain_masked_cells":masked}

def plot(fields, init, lead, dest):
    fig, axs = plt.subplots(2,3,figsize=(18,10),layout="constrained")
    for ax in axs.flat[:5]:
        m=Basemap(projection="cyl",llcrnrlon=110,urcrnrlon=132,llcrnrlat=-10,urcrnrlat=5,resolution="l",ax=ax)
        m.drawcoastlines(color="#293342",linewidth=.65)
        m.drawcountries(color="#5f6874",linewidth=.4)
        m.drawparallels(np.arange(-10,6,5),labels=[1,0,0,0],fontsize=8,linewidth=.3)
        m.drawmeridians(np.arange(110,133,5),labels=[0,0,0,1],fontsize=8,linewidth=.3)
        ax.plot(121.525,-4.338,"k*",ms=9,zorder=10)
        ax.annotate("WAWP",(121.525,-4.338),xytext=(5,5),textcoords="offset points",fontsize=8,zorder=11)
        ax.set_facecolor("#dddddd")
    for ax,p in zip(axs.flat[:2],(925,850)):
        u,v=fields["u",p],fields["v",p]
        speed=np.hypot(u,v)
        fill=ax.contourf(LON,LAT,speed,levels=[0,2,4,6,8,10,12,16,20,25,35],cmap="YlGnBu",extend="max")
        ax.streamplot(LON,LAT,np.ma.masked_invalid(u),np.ma.masked_invalid(v),density=1.1,color="#242c36",linewidth=.7,arrowsize=.75)
        fig.colorbar(fill,ax=ax,orientation="horizontal",shrink=.85,label="Wind speed (m/s)")
        ax.set_title(f"{p} hPa wind streamlines")
    for ax,p in zip(axs.flat[2:4],(850,700)):
        fill=ax.contourf(LON,LAT,fields["rh",p],levels=np.arange(0,111,10),cmap="BrBG",extend="both")
        fig.colorbar(fill,ax=ax,orientation="horizontal",shrink=.85,label="Relative humidity (%)")
        ax.set_title(f"{p} hPa relative humidity")
    ax=axs.flat[4];p=fields["mslp",0]
    fill=ax.contourf(LON,LAT,p,levels=np.arange(980,1033,2),cmap="RdBu_r",extend="both")
    levels=np.arange(np.ceil(p.min()),np.floor(p.max())+1,1)
    if len(levels):
        cs=ax.contour(LON,LAT,p,levels=levels,colors="#252525",linewidths=.65)
        ax.clabel(cs,fmt="%.0f",fontsize=8)
    fig.colorbar(fill,ax=ax,orientation="horizontal",shrink=.85,label="MSLP (hPa)")
    ax.set_title("Mean sea-level pressure · 1 hPa isobars")
    axs.flat[5].axis("off")
    valid=init+timedelta(hours=lead)
    axs.flat[5].text(.04,.95,f"GFS 0.25°\nInitialization: {init:%d %b %Y %H:%M} UTC\nLead: H+{lead}\nValid: {valid:%d %b %Y %H:%M} UTC\n\nGray: missing or below model terrain\nStar: WAWP (approximate)\n\nSource: NOAA/NCEP NOMADS\nResearch pilot — not official BMKG guidance",va="top",fontsize=13,linespacing=1.6)
    fig.suptitle(f"TITAN regional GFS {'analysis' if lead==0 else 'forecast'} · {valid:%d %b %Y %H:%M} UTC",fontsize=19,fontweight="bold")
    fig.savefig(dest,dpi=140,bbox_inches="tight")
    plt.close(fig)

def main():
    p=argparse.ArgumentParser();p.add_argument("--cycle",required=True);p.add_argument("--output",required=True,type=Path);args=p.parse_args()
    init=datetime.strptime(args.cycle,"%Y%m%d%H").replace(tzinfo=UTC)
    if init.hour not in (0,6,12,18) or init>datetime.now(UTC): raise ValueError("Invalid cycle")
    args.output.mkdir(parents=True,exist_ok=False)
    evidence={"cycle":init.isoformat(),"domain":[110,132,-10,5],"grid":[len(LAT),len(LON)],"leads":{}}
    for lead in LEADS:
        if lead:time.sleep(10)
        path=args.output/f"gfs_f{lead:03d}.grib2"
        try:
            meta=download(url_for(init,lead),path)
            fields,qc=read_fields(path,init,lead)
            plot(fields,init,lead,args.output/f"regional_f{lead:03d}.png")
            evidence["leads"][str(lead)]={**meta,**qc,"valid_utc":(init+timedelta(hours=lead)).isoformat()}
            print("PASS",lead,meta["bytes"],flush=True)
        finally:
            path.unlink(missing_ok=True)
    (args.output/"manifest.json").write_text(json.dumps(evidence,indent=2))
    print("PASS: one cycle, three leads, eight required fields per lead; no raw GRIB retained")

if __name__=="__main__":
    main()
