"""Build a static offline review from existing CI artifacts; no new model downloads."""
import json,re,shutil
from pathlib import Path
from PIL import Image
SOURCES=[["pwv","45622818688","titan-v3-pwv","15026671324"],["circulation","45622818688","titan-v3-circulation","15027641087"],["dynamics","45622818688","titan-v3-dynamics","15027361323"],["rain","45622818688","titan-v3-rain","15026306885"],["vertical","45622818688","titan-v3-vertical","15027975999"],["icon","45622818688","titan-v3-icon","15026861200"]]
ROOT=Path("review-inputs")
OUT=Path("titan-spatial-review")
PRODUCTS={
"pwv":("Moisture & convection","Precipitable water","GFS and IFS native PWV, common scale; ICON PWV is a separate selection."),
"refc":("Moisture & convection","GFS simulated composite reflectivity","Simulated echo, not observed radar; native model grid."),
"wind925":("Circulation","925 hPa streamlines","Wind speed and earth-relative streamlines."),
"wind850":("Circulation","850 hPa streamlines","Wind speed and earth-relative streamlines."),
"rh850":("Moisture","850 hPa relative humidity","AIFS derived liquid-water RH; subfreezing cells masked."),
"rh700":("Moisture","700 hPa relative humidity","AIFS derived liquid-water RH; subfreezing cells masked."),
"mslp":("Circulation","Mean sea-level pressure","1 hPa isobars."),
"mfc925":("Moisture dynamics","925 hPa moisture convergence","Horizontal pressure-level qV convergence, not column-integrated."),
"mfc850":("Moisture dynamics","850 hPa moisture convergence","Horizontal pressure-level qV convergence, not column-integrated."),
"flux925":("Moisture dynamics","925 hPa moisture flux magnitude","Specific humidity times wind speed."),
"shear":("Instability","850–200 hPa vector shear","Not 0–6 km bulk shear."),
"ki":("Instability","K-index","Temperature and dewpoint-derived index."),
"cape":("Instability","GFS surface-based CAPE","Not equivalent to IFS MUCAPE."),
"cin":("Instability","GFS surface-based CIN","Native surface parcel diagnostic."),
"mucape":("Instability","IFS most-unstable CAPE","Native IFS MUCAPE; not equivalent to GFS SBCAPE or ICON mixed-layer CAPE."),
"rain":("Surface forecasts","Six-hour rainfall","H+0–6 or H+6–12, matched accumulation intervals."),
"gust_1h":("Surface forecasts","IFS previous-hour maximum gust","One-hour maximum, not instantaneous or six-hour maximum."),
"gust_instant":("Surface forecasts","GFS instantaneous gust","Instantaneous guidance; not IFS maximum gust."),
"li":("Vertical profile","Approximate surface-based Lifted Index","2 m parcel at model surface pressure; not mixed-layer LI."),
"si":("Vertical profile","Showalter Index","850 hPa parcel."),
"freeze":("Vertical profile","Resolved freezing height","First resolved warm-to-cold pressure-level crossing; not an icing forecast."),
"TQV":("ICON native grid","ICON precipitable water","Nearest native ICON cell on a 0.05° display mesh; native resolution unchanged."),
"PMSL":("ICON native grid","ICON mean sea-level pressure","Nearest native ICON cell on a 0.05° display mesh; no inferred isobars."),
"CAPE_ML":("ICON native grid","ICON mixed-layer CAPE","Native DWD mixed-layer parcel on a nearest-cell display mesh, not GFS SBCAPE or IFS MUCAPE."),
"z500":("Vertical profile","500 hPa geopotential height","Native geopotential height or converted geopotential.")}
LEADS=(0,6,12)
DOMAINS=("regional","sulawesi","kolaka")
def expected(group):
    if group=="pwv":products=("pwv","refc")
    elif group=="circulation":products=("wind925","wind850","rh850","rh700","mslp")
    elif group=="dynamics":products=("mfc925","mfc850","flux925","shear","ki","cape","cin","mucape")
    elif group=="rain":return [(p,d,h) for p in ("rain","gust_1h","gust_instant") for d in DOMAINS for h in (6,12)]
    elif group=="icon":products=("TQV","PMSL","CAPE_ML")
    else:products=("li","si","freeze","z500")
    return [(p,d,h) for p in products for d in DOMAINS for h in LEADS]
def main():
    OUT.mkdir(exist_ok=False)
    entries=[]
    for group,run,artifact,aid in SOURCES:
        folder=ROOT/group
        manifests=list(folder.rglob("manifest.json"))
        if len(manifests)!=1:raise ValueError(f"{group}: expected one source manifest")
        m=json.loads(manifests[0].read_text())
        if not str(m.get("cycle","")).startswith("2026-11-25T00:00"):raise ValueError(f"{group}: mismatched model cycle")
        dest=OUT/"sources"/group;dest.mkdir(parents=True)
        shutil.copyfile(manifests[0],dest/"manifest.json")
        for p,d,h in expected(group):
            stem=f"{p}_{d}_f{h:03d}"
            files=list(folder.rglob(stem+".png"))
            if len(files)!=1:raise ValueError(f"{group}: expected one {stem}.png")
            with Image.open(files[0]) as im:
                if im.format!="PNG" or im.width<500 or im.height<400:raise ValueError("Unexpected image "+stem)
                im.verify()
            category,label,note=PRODUCTS[p]
            target=dest/(stem+".png")
            shutil.copyfile(files[0],target)
            entries.append({"product":p,"category":category,"label":label,"note":note,"domain":d,"lead":h,"image":target.relative_to(OUT).as_posix(),"run":run,"artifact":aid,"source":group})
    if len({(e["product"],e["domain"],e["lead"]) for e in entries})!=len(entries):raise ValueError("Duplicate chart key")
    if len(entries)!=216 or len({e["product"] for e in entries})!=25:raise ValueError("Unexpected V3 coverage")
    data={"cycle":"2026-11-25 00 UTC","entries":entries}
    (OUT/"catalog.json").write_text(json.dumps(data,indent=2))
    html=Path("scripts/titan_spatial_review.html").read_text()
    (OUT/"index.html").write_text(html.replace("__CATALOG__",json.dumps(data,ensure_ascii=False).replace("<","\\u003c")))
    (OUT/"README.txt").write_text("Extract the whole ZIP, then open index.html in a browser. Offline charts are from 25 November 2026 00 UTC, not current guidance. Source manifests are included. No live data retrieval. External reference links require internet. Observed RASON integration deferred by user; ICON PWV/MSLP/mixed-layer CAPE included; synthetic IR unresolved. No production deployment. All 25 products have three geographic domains. Rainfall and gust are unavailable at H+0 by design. Missing combinations remain unavailable, not silently substituted.")
    print("REVIEW_CHARTS",len(entries),"PRODUCTS",len({e["product"] for e in entries}),flush=True)
if __name__=="__main__":main()
