"""Assemble the six immutable full-pilot render artifacts into the existing-site preview."""
import hashlib,json,shutil,os
from datetime import datetime,timedelta
from pathlib import Path
from collections import Counter
from PIL import Image

RUN="45721468170"
SHA="5ba074efa1010f1dbf50006da97b313b6ed8f6e2"
CYCLES={"gfs":"2026-11-25T18:00:00+00:00","ifs-025":"2026-11-25T12:00:00+00:00","ifs-010":"2026-11-25T12:00:00+00:00","aifs-025":"2026-11-25T18:00:00+00:00","icon":"2026-11-25T18:00:00+00:00"}
GROUPS={
 "dynamics":(498,{"cape":"Surface-based CAPE","cin":"Convective inhibition","flux925":"925 hPa moisture flux","ki":"K index","mfc850":"850 hPa moisture flux convergence","mfc925":"925 hPa moisture flux convergence","mucape":"Most-unstable CAPE","shear":"850–200 hPa vector wind shear"}),
 "circulation":(345,{"mslp":"Mean sea-level pressure","rh700":"700 hPa relative humidity","rh850":"850 hPa relative humidity","wind850":"850 hPa circulation","wind925":"925 hPa circulation"}),
 "vertical":(276,{"freeze":"Resolved freezing height (MSL)","li":"Approximate surface-based lifted index","si":"Showalter index","z500":"500 hPa geopotential height"}),
 "rain":(171,{"gust_1h":"Previous-hour maximum gust","gust_instant":"Instantaneous gust","rain":"Accumulated rainfall"}),
 "icon":(153,{"CAPE_ML":"ICON mixed-layer CAPE","PMSL":"ICON mean sea-level pressure","TQV":"ICON precipitable water"}),
 "pwv":(120,{"pwv":"Precipitable water","refc":"GFS simulated composite reflectivity (not observed radar)"})
}
DOMAINS={"regional","sulawesi","kolaka"}
LEADS={0,3,6,9,12,15,18,21,24,30,36,42,48,60,72,84,96}
def require(ok,msg):
    if not ok: raise ValueError(msg)
def dt(s):
    t=datetime.fromisoformat(s)
    require(t.utcoffset()==timedelta(0),"Timestamp is not UTC")
    return t
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    src=Path("full-render-inputs");out=Path("docs/spatial-preview")
    require(not out.exists(),"Preview destination already exists")
    out.mkdir()
    entries=[];keys=set();products=[]
    category={"mslp":"1. Atmospheric circulation","PMSL":"1. Atmospheric circulation","wind925":"1. Atmospheric circulation","wind850":"1. Atmospheric circulation","z500":"1. Atmospheric circulation","rh850":"2. Moisture availability","rh700":"2. Moisture availability","pwv":"2. Moisture availability","TQV":"2. Moisture availability","mfc925":"2. Moisture availability","mfc850":"2. Moisture availability","flux925":"2. Moisture availability","cape":"3. Convective potential","cin":"3. Convective potential","mucape":"3. Convective potential","CAPE_ML":"3. Convective potential","ki":"3. Convective potential","li":"3. Convective potential","si":"3. Convective potential","shear":"3. Convective potential","rain":"4. Forecast weather","gust_1h":"4. Forecast weather","gust_instant":"4. Forecast weather","refc":"4. Forecast weather","freeze":"5. Aviation diagnostics"}
    for group,(count,labels) in GROUPS.items():
        folder=src/("titan-full-render-"+group)
        manifest=folder/"catalog.json"
        m=json.loads(manifest.read_text())
        require(m["group"]==group and m["code_sha"]==SHA and m["source_cycles"]==CYCLES and m["mode"]=="research_preview","Unexpected source manifest")
        require(m["render_sha"]==os.environ["GITHUB_SHA"] and m["style_revision"]=="refined-v1","Wrong rendering revision")
        original=json.loads((Path("original-render-inputs")/("titan-full-render-"+group)/"catalog.json").read_text())
        def identity(e):return json.dumps({k:e[k] for k in ("product","domain","valid_utc","window_utc","models")},sort_keys=True)
        require(sorted(map(identity,m["entries"]))==sorted(map(identity,original["entries"])),"Changed meteorological identity")
        require(len(m["entries"])==count and {e["product"] for e in m["entries"]}==set(labels),"Unexpected group coverage")
        dest=out/"sources"/group;dest.mkdir(parents=True)
        shutil.copy2(manifest,dest/"catalog.json")
        names=set()
        for e in m["entries"]:
            p=e["product"];v=dt(e["valid_utc"]);w=e["window_utc"];d=e["domain"];name=e["file"]
            require(d in DOMAINS and p in labels,"Unexpected product/domain")
            require(isinstance(w,list) and len(w)==(2 if p in {"rain","gust_1h","gust_instant"} else 0),"Unexpected window")
            if w:
                start,end=map(dt,w)
                require(end==v and start<=end and (p!="rain" or start<end),"Invalid window")
            key=(p,d,v,tuple(w));require(key not in keys,"Duplicate chart key");keys.add(key)
            require(Path(name).name==name and name.endswith(".webp") and name not in names,"Unsafe/duplicate filename");names.add(name)
            f=folder/name
            require(f.is_file() and f.stat().st_size==e["bytes"] and digest(f)==e["sha256"],"Image integrity error")
            with Image.open(f) as im:
                require(im.format=="WEBP" and im.width>0 and im.height>0,"Invalid chart")
                im.verify()
            seen=set()
            for panel in e["models"]:
                model=panel["model"];lead=panel["lead"]
                require(model in CYCLES and model not in seen and panel["init_utc"]==CYCLES[model],"Model provenance error")
                require(lead in LEADS and (model!="aifs-025" or lead not in {3,9,15,21}) and dt(panel["init_utc"])+timedelta(hours=lead)==v,"Valid time mismatch")
                require(len(panel["numeric_sha256"])==64,"Missing numerical hash")
                seen.add(model)
            require(bool(seen),"No model panels")
            shutil.copy2(f,dest/name)
            entries.append(dict(e,group=group,image=f"sources/{group}/{name}"))
        require(names=={f.name for f in folder.glob("*.webp")},"Uncatalogued charts")
        products.extend({"product":p,"label":label,"category":category[p]} for p,label in labels.items())
    require(len(entries)==1563 and len(products)==25,"Incomplete collection")
    coverage=Counter((e["product"],e["domain"]) for e in entries)
    require(all(coverage[p["product"],d]>0 for p in products for d in DOMAINS),"Missing domain coverage")
    products.sort(key=lambda p:(p["category"],p["label"]))
    catalog={"render_sha":os.environ["GITHUB_SHA"],"style_revision":"refined-v1","schema":2,"mode":"research_preview","run":RUN,"code_sha":SHA,"source_cycles":CYCLES,"discovery_utc":"2026-11-25T23:27:26+00:00","products":products,"entries":entries}
    (out/"catalog.json").write_text(json.dumps(catalog,separators=(",",":")))
    print("CATALOG_PASS",len(entries),len(products),sum(e["bytes"] for e in entries),"bytes")
if __name__=="__main__":main()
