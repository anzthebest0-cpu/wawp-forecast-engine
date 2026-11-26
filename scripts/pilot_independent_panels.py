"""Matched chart benchmark on the already validated mixed-cycle snapshots.
Same rendering function and palette; independent single-panel canvas is not pixel-identical
to the multi-panel canvas. Sample timings are not extrapolated as measured full workload."""
import json,time,hashlib,os,html
from pathlib import Path
from collections import defaultdict
from pilot_full_spatial_render import render,MODELS,SPECS,DOMAINS,LABELS
from pilot_multicycle_storage import read_model,BASE_SHA,EXPERIMENT_SHA,folder,need
from PIL import Image
import numpy as np
PRODUCTS=("rh700","rain","wind850","mslp","pwv","mucape","ki","TQV")
OUT=Path("panel-benchmark")
def catalog(root):
    groups=defaultdict(list)
    for model in MODELS:
        f=folder(root,model);m,rows=read_model(f,model,allowed={BASE_SHA,EXPERIMENT_SHA})
        for e in rows:
            for p in e["products"]:
                if p in SPECS:
                    w=tuple(e["windows"].get(p,[]))
                    if p in ("rain","gust_1h","gust_instant"):need(len(w)==2 and w[1]==e["valid_utc"],"Window mismatch")
                    groups[p,e["valid_utc"],w].append((model,m["cycle"],e,f/e["file"]))
    return groups
def signature(p,t,w,domain,panels):
    return (p,t,w,domain,tuple((m,c,e["lead"],e["sha256"]) for m,c,e,_ in panels))
def key(p,t,w,d,panel):
    return hashlib.sha256(json.dumps(signature(p,t,w,d,[panel]),sort_keys=True).encode()).hexdigest()
def allkeys(g):
    return {key(p,t,w,d,a) for (p,t,w),panels in g.items() for d in DOMAINS for a in panels}
def combinedkeys(g):
    return {signature(p,t,w,d,panels) for (p,t,w),panels in g.items() for d in DOMAINS}
def main():
    OUT.mkdir();old=catalog("baseline");new=catalog("candidate")
    selected=[]
    for p in PRODUCTS:
        common=sorted(k for k in set(old)&set(new) if k[0]==p)
        need(bool(common),"No comparable sample for "+p)
        for k in sorted({common[len(common)//3],common[2*len(common)//3]}):
            for d in DOMAINS:selected.append((k,d))
    report={"mode":"measured_matched_sample_and_full_signature_inventory","selected_groups":len(selected),"products":list(PRODUCTS),"snapshots":{}}
    cached={};old_combined={};cards=[]
    for label,g in (("baseline",old),("candidate",new)):
        stats={"combined":{"seconds":0.,"bytes":0,"rendered":0,"reused":0,"new_bytes":0},"independent":{"seconds":0.,"bytes":0,"rendered":0,"reused":0,"new_bytes":0},"sample":[]}
        for (p,t,w),d in selected:
            panels=g[p,t,w];sig=signature(p,t,w,d,panels)
            cid=hashlib.sha256(json.dumps(sig).encode()).hexdigest()
            if cid in old_combined:
                rec=old_combined[cid];stats["combined"]["reused"]+=1
            else:
                f=OUT/"combined"/cid;f.mkdir(parents=True)
                start=time.monotonic();rec=render(f,p,t,w,panels,d);stats["combined"]["seconds"]+=time.monotonic()-start
                rec["path"]=(f/rec["file"]).relative_to(OUT).as_posix()
                old_combined[cid]=rec;stats["combined"]["rendered"]+=1;stats["combined"]["new_bytes"]+=rec["bytes"]
            stats["combined"]["bytes"]+=rec["bytes"];panelrecs=[]
            for a in panels:
                ident=key(p,t,w,d,a)
                if ident in cached:
                    r=cached[ident];stats["independent"]["reused"]+=1
                else:
                    f=OUT/"panels"/ident;f.mkdir(parents=True)
                    start=time.monotonic();r=render(f,p,t,w,[a],d);stats["independent"]["seconds"]+=time.monotonic()-start
                    r["path"]=(f/r["file"]).relative_to(OUT).as_posix()
                    cached[ident]=r;stats["independent"]["rendered"]+=1;stats["independent"]["new_bytes"]+=r["bytes"]
                need(r["models"]==[m for m in rec["models"] if m["model"]==a[0]],"Panel provenance mismatch")
                stats["independent"]["bytes"]+=r["bytes"];panelrecs.append(r)
            need([r["models"][0] for r in panelrecs]==rec["models"],"Panel composition differs")
            for r in [rec]+panelrecs:
                path=OUT/r["path"]
                need(hashlib.sha256(path.read_bytes()).hexdigest()==r["sha256"],"Rendered hash mismatch")
                with Image.open(path) as im:need(im.format=="WEBP" and im.width>300 and im.height>300,"Bad image");r["dimensions"]=list(im.size)
            stats["sample"].append({"product":p,"valid_utc":t,"window_utc":w,"domain":d,"combined":rec,"panels":panelrecs})
        report["snapshots"][label]=stats
    a,b=allkeys(old),allkeys(new);x,y=combinedkeys(old),combinedkeys(new)
    report["full_inventory"]={"baseline_panels":len(a),"candidate_panels":len(b),"reusable_panels":len(a&b),"baseline_combined":len(x),"candidate_combined":len(y),"reusable_combined":len(x&y)}
    report["limitations"]=["Sample uses the previously validated mixed-cycle candidate (older IFS 0.1°), separate from experimental RH recovery.","Same DPI, rendering routine, product scales and cartography; single-panel canvas dimensions differ and storage/render costs may be higher.","Full inventory counts are signature estimates, not full measured rendering costs.","No production deployment or Drive writes."]
    report["passed"]=True
    (OUT/"report.json").write_text(json.dumps(report,indent=2))
    items=report["snapshots"]["candidate"]["sample"]
    data=json.dumps(items).replace("</","<\\/")
    page="""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>TITAN panel trial</title><style>body{font:16px system-ui;margin:20px;background:#f4f6f8;color:#203040}select{max-width:100%;padding:10px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,350px),1fr));gap:12px}img{width:100%;height:auto;background:white}article{background:white;padding:8px;border:1px solid #d6dde2}label{margin-right:12px}a{color:#245b8f}</style></head><body><h1>TITAN independent model panels</h1><p>Frozen research preview; not an official forecast. Click a panel to open its full-resolution image.</p><select id="choose"></select><p id="models"></p><p><a id="combined" target="_blank" rel="noopener">Original combined chart</a></p><main id="panels"></main><script>const data=DATA;const s=document.getElementById('choose'),m=document.getElementById('models'),p=document.getElementById('panels');function show(){const g=data[+s.value];m.replaceChildren();p.replaceChildren();document.getElementById('combined').href=g.combined.path;for(const r of g.panels){const name=r.models[0].model;const a=document.createElement('article'),l=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.checked=true;c.onchange=()=>a.hidden=!c.checked;l.append(c,document.createTextNode(name));m.append(l);const link=document.createElement('a');link.href=r.path;link.target='_blank';link.rel='noopener';const img=document.createElement('img');img.src=r.path;img.alt=name+' '+g.product+' '+g.valid_utc;img.loading='lazy';link.append(img);a.append(link);p.append(a)}}data.forEach((g,i)=>{const o=document.createElement('option');o.value=i;o.textContent=g.product+' | '+g.domain+' | '+g.valid_utc+(g.window_utc.length?' | from '+g.window_utc[0]:'');s.append(o)});s.onchange=show;show();</script></body></html>""".replace("DATA",data)
    (OUT/"index.html").write_text(page)
    print("PANEL_BENCHMARK",json.dumps({**report,"snapshots":{k:{x:y for x,y in v.items() if x!="sample"} for k,v in report["snapshots"].items()}}),flush=True)
if __name__=="__main__":main()
