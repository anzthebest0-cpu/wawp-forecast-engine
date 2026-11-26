"""Full native-model independent panels, pinned real cycles, no live deployment."""
import hashlib,json,sys,os,shutil,time,importlib.metadata as md
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timedelta
import numpy as np
from PIL import Image
from pilot_full_spatial_render import render,MODELS,LABELS,GROUPS,SPECS,DOMAINS
from pilot_multicycle_storage import read_model,BASE_SHA,EXPERIMENT_SHA,folder,need,sha,dump,load
QC_SHA="dac98fdf26f37aeaf811ec462f18f5134a806d36"
CYCLES={"gfs":"2026-11-26T00:00:00+00:00","ifs-025":"2026-11-25T18:00:00+00:00","ifs-010":"2026-11-25T18:00:00+00:00","aifs-025":"2026-11-25T18:00:00+00:00","icon":"2026-11-26T00:00:00+00:00"}
REVISIONS={m:BASE_SHA if m=="aifs-025" else QC_SHA if m=="ifs-010" else EXPERIMENT_SHA for m in MODELS}
ROOT=Path("cache-inputs")
def renderer_identity():
    # Content key must change when style, renderer, product scales or runtime
    # version changes. Dependencies also affect output pixels.
    files=("pilot_full_spatial_render.py","spatial_v3_style.py","pilot_instability_moisture.py","pilot_vertical_profiles.py","titan_full_independent_panels.py")
    return hashlib.sha256(dump({"source":{f:sha(Path(__file__).parent/f) for f in files},"packages":{p:md.version(p) for p in ("numpy","matplotlib","basemap","basemap_data","pillow")},"policy":"independent-v1"})).hexdigest()
def read_sources():
    groups=defaultdict(list);sources={};n=0
    for model in MODELS:
        f=folder(ROOT,model);m,rows=read_model(f,model,allowed={REVISIONS[model]})
        need(m["cycle"]==CYCLES[model],"Unpinned model cycle")
        if model=="ifs-010":
            need(m.get("qc_policy")=="experimental_localized_rh_quarantine_v1" and m.get("original_prefix_code_sha")==EXPERIMENT_SHA,"Missing recovered QC lineage")
        sources[model]={"cycle":m["cycle"],"acquisition_sha":m["code_sha"],"entries":len(rows),"manifest_sha256":sha(f/"manifest.json")}
        n+=len(rows)
        for e in rows:
            for p in e["products"]:
                if p not in SPECS:continue
                w=tuple(e["windows"].get(p,[]))
                if p in ("rain","gust_1h","gust_instant"):
                    need(len(w)==2 and w[1]==e["valid_utc"],"Native window invalid")
                    duration=(datetime.fromisoformat(w[1])-datetime.fromisoformat(w[0])).total_seconds()
                    need(duration>0 if p=="rain" else duration==3600 if p=="gust_1h" else duration==0,"Wrong window duration")
                else:need(not w,"Unexpected window")
                q=[]
                if p.startswith("rh"):
                    q=[{"level_hpa":d["level_hpa"],"lead":d["lead"],"above_ground_masked":d["quarantine"]["above_ground_masked"],"raw_grib_sha256":d["rh_grib_sha256"]} for d in e["provenance"].get("rh_range_diagnostics",[]) if d["level_hpa"]==int(p[2:]) and d.get("quarantine",{}).get("above_ground_masked",0)>0]
                groups[(p,e["valid_utc"],w)].append((model,m["cycle"],e,f/e["file"],q))
    need(n==81 and {k[0] for k in groups}==set(SPECS) and len(SPECS)==25,"Incomplete product inventory")
    return groups,sources
def identity(p,t,w,d,panel,revision):
    model,c,e,path,q=panel
    return {"renderer":revision,"product":p,"domain":d,"valid_utc":t,"window_utc":list(w),"model":model,"init_utc":c,"lead":e["lead"],"numeric_sha256":e["sha256"],"qc":q}
def key(i):return hashlib.sha256(dump(i)).hexdigest()
def prepare():
    groups,sources=read_sources()
    # Verify the preserved 13-entry original prefix against its exact partial manifest.
    partial=load(Path("partial-ifs/manifest.json"))
    recovered=load(folder(ROOT,"ifs-010")/"manifest.json")
    need(partial["code_sha"]==EXPERIMENT_SHA and partial["passed"] is False and len(partial["entries"])==13 and recovered["entries"][:13]==partial["entries"],"Recovered prefix provenance changed")
    for e in partial["entries"]:
        need(sha(Path("partial-ifs")/e["file"])==e["sha256"]==sha(folder(ROOT,"ifs-010")/e["file"]),"Recovered prefix bytes changed")
    q=[d for e in recovered["entries"] for d in e["provenance"].get("rh_range_diagnostics",[]) if d.get("quarantine",{}).get("above_ground_masked",0)>0]
    need(len(q)==1 and q[0]["lead"]==60 and q[0]["level_hpa"]==700 and q[0]["quarantine"]["above_ground_masked"]==1,"Unexpected RH quarantine lineage")
    with np.load(folder(ROOT,"ifs-010")/"f060.npz",allow_pickle=False) as f:
        need(np.isnan(f["rh700"][66,100]),"Expected quarantined native cell missing")
    print("FULL_PANEL_INPUTS_PASS",json.dumps({"entries":81,"products":25,"sources":sources,"quarantined_cells":1}),flush=True)
def run_group(group):
    groups,sources=read_sources();revision=renderer_identity();out=Path("panel-output");out.mkdir();(out/"panels").mkdir()
    entries=[];start=time.monotonic()
    for (p,t,w),panels in sorted(groups.items()):
        if p not in GROUPS[group]:continue
        for d in DOMAINS:
            for panel in panels:
                i=identity(p,t,w,d,panel,revision);ident=key(i)
                temp=out/"work";temp.mkdir(exist_ok=True)
                r=render(temp,p,t,w,[panel[:4]],d)
                path=temp/r["file"];target=out/"panels"/(ident+".webp")
                need(not target.exists(),"Duplicate panel identity")
                path.rename(target)
                need(r["sha256"]==sha(target) and r["models"]==[{"model":i["model"],"init_utc":i["init_utc"],"lead":i["lead"],"numeric_sha256":i["numeric_sha256"]}],"Rendered provenance differs")
                with Image.open(target) as im:
                    need(im.format=="WEBP" and im.width>=1000 and im.height>=700,"Invalid native panel dimensions")
                    dims=list(im.size)
                entries.append({**i,"id":ident,"path":"panels/"+target.name,"sha256":r["sha256"],"bytes":target.stat().st_size,"dimensions":dims})
    (out/"work").rmdir()
    need(bool(entries),"Empty group")
    (out/"catalog.json").write_bytes(dump({"group":group,"renderer":revision,"sources":sources,"entries":entries}))
    print("FULL_INDEPENDENT_GROUP",json.dumps({"group":group,"panels":len(entries),"bytes":sum(e["bytes"] for e in entries),"seconds":round(time.monotonic()-start,2),"renderer":revision}),flush=True)
def assemble():
    groups,sources=read_sources();revision=renderer_identity()
    expected={key(i):i for (p,t,w),ps in groups.items() for d in DOMAINS for panel in ps for i in [identity(p,t,w,d,panel,revision)]}
    need(len(expected)==sum(len(ps)*len(DOMAINS) for ps in groups.values()),"Duplicate expected signatures")
    out=Path("titan-independent-review");out.mkdir();(out/"panels").mkdir();(out/"sources").mkdir()
    entries={};groups_seen=set()
    for group in GROUPS:
        f=Path("render-inputs")/("titan-full-panels-"+group);c=load(f/"catalog.json")
        need(c["group"]==group and c["renderer"]==revision and c["sources"]==sources,"Render catalogue revision differs")
        need(set(p.name for p in (f/"panels").glob("*.webp"))=={e["id"]+".webp" for e in c["entries"]},"Orphan or missing images")
        for e in c["entries"]:
            ident=e["id"];need(ident in expected and ident not in entries and e["product"] in GROUPS[group],"Unexpected panel identity")
            need({k:e[k] for k in expected[ident]}==expected[ident] and e["path"]=="panels/"+ident+".webp","Panel identity mismatch")
            p=f/e["path"];need(p.stat().st_size==e["bytes"] and sha(p)==e["sha256"],"Panel bytes mismatch")
            with Image.open(p) as im:
                need(im.format=="WEBP" and list(im.size)==e["dimensions"],"Image metadata differs");im.load()
            shutil.copyfile(p,out/e["path"]);entries[ident]=e
        groups_seen.add(group)
    need(set(entries)==set(expected) and groups_seen==set(GROUPS),"Full panel coverage incomplete")
    for model in MODELS:shutil.copyfile(folder(ROOT,model)/"manifest.json",out/"sources"/(model+".json"))
    comparison=defaultdict(list)
    for e in entries.values():comparison[(e["product"],e["domain"],e["valid_utc"],tuple(e["window_utc"]))].append(e)
    comparisons=[{"product":p,"domain":d,"valid_utc":t,"window_utc":list(w),"panels":sorted(es,key=lambda e:MODELS.index(e["model"]))} for (p,d,t,w),es in sorted(comparison.items())]
    need(all(len({e["model"] for e in c["panels"]})==len(c["panels"]) for c in comparisons),"Duplicate model in comparison")
    report={"passed":True,"mode":"frozen_full_independent_research_preview","renderer":revision,"sources":sources,"products":25,"domains":list(DOMAINS),"native_leads":81,"panels":len(entries),"comparisons":len(comparisons),"image_bytes":sum(e["bytes"] for e in entries.values()),"quarantine_panel_count":sum(bool(e["qc"]) for e in entries.values()),"production_deployed":False}
    need(report["quarantine_panel_count"]==3,"QC annotation coverage mismatch")
    data={"report":report,"labels":LABELS,"products":{k:{"title":v[0],"unit":v[1]} for k,v in SPECS.items()},"comparisons":comparisons}
    (out/"catalog.json").write_bytes(dump(data))
    # External JS makes the preview usable both via local files and HTTP.
    (out/"catalog.js").write_text("window.TITAN_CATALOG="+json.dumps(data,ensure_ascii=True).replace("</","<\\/")+";")
    shutil.copyfile(Path(__file__).parent/"titan_full_panels.html",out/"index.html")
    (out/"report.json").write_bytes(dump(report))
    print("FULL_INDEPENDENT_PASS",json.dumps(report),flush=True)
if __name__=="__main__":
    command=sys.argv[1]
    if command=="prepare":prepare()
    elif command=="assemble":assemble()
    else:run_group(command)
