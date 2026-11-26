"""Actual historical ICON 18Z -> 00Z transition, not live acquisition.
Create the prior mixed-cycle generation by replacing only the ICON model in
the validated full site; use the same integrated render/assemble/publish code."""
import json,shutil,sys,os
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import titan_automated_update as auto
import titan_full_independent_panels as full
from pilot_multicycle_storage import read_model,folder,need,sha,dump,load,BASE_SHA,EXPERIMENT_SHA
from pilot_full_spatial_render import render,MODELS,DOMAINS,SPECS,GROUPS
from PIL import Image
MODEL="icon";OLDER="2026-11-25T18:00:00+00:00";NEWER=full.CYCLES[MODEL]
def configure():
    full.CYCLES[MODEL]=OLDER
    full.REVISIONS[MODEL]=BASE_SHA
    auto.REPLAY_REVISIONS[MODEL]=EXPERIMENT_SHA
def prepare():
    # Before mutating the pinned baseline config, fully validate the exact
    # original full site and every matching numeric manifest.
    latest=auto.previous_entries();auto.verify_site(auto.OLD_SITE,latest)
    current=load(auto.OLD_SITE/"catalog.json");sources=current["report"]["sources"]
    for m in MODELS:
        f=folder(auto.BASE,m);manifest,rows=read_model(f,m,allowed={full.REVISIONS[m]})
        need(manifest["cycle"]==full.CYCLES[m] and sha(f/"manifest.json")==sources[m]["manifest_sha256"],"Original source differs")
    shutil.copytree(auto.BASE,auto.CAND)
    old_icon=Path("archived-icon")
    manifest,rows=read_model(old_icon,MODEL,allowed={BASE_SHA});need(manifest["cycle"]==OLDER,"Unexpected replay origin")
    # Replace only ICON's numerical inventory and its 153 image objects.
    shutil.rmtree(folder(auto.BASE,MODEL));shutil.copytree(old_icon,folder(auto.BASE,MODEL))
    kept={i:e for i,e in latest.items() if e["model"]!=MODEL};need(len(latest)-len(kept)==153,"Unexpected ICON panel count")
    for i,e in latest.items():
        if e["model"]==MODEL:(auto.OLD_SITE/e["path"]).unlink()
    revision=full.renderer_identity();temp=Path("replay-render");temp.mkdir();added=0
    for e in rows:
        for p in e["products"]:
            if p not in SPECS:continue
            w=tuple(e["windows"].get(p,[]))
            for d in DOMAINS:
                panel=(MODEL,OLDER,e,old_icon/e["file"],[])
                i=full.identity(p,e["valid_utc"],w,d,panel,revision);ident=full.key(i)
                r=render(temp,p,e["valid_utc"],w,[panel[:4]],d)
                dest=auto.OLD_SITE/"panels"/(ident+".webp");need(not dest.exists(),"Duplicate replay panel")
                (temp/r["file"]).rename(dest)
                with Image.open(dest) as im:dims=list(im.size)
                rec={**i,"id":ident,"path":"panels/"+dest.name,"sha256":r["sha256"],"bytes":r["bytes"],"dimensions":dims}
                auto.verify_image(dest,rec);kept[ident]=rec;added+=1
    need(added==153 and len(kept)==3567,"Prior mixed inventory differs")
    temp.rmdir()
    sources[MODEL]={"cycle":OLDER,"acquisition_sha":BASE_SHA,"entries":17,"manifest_sha256":sha(old_icon/"manifest.json")}
    shutil.copyfile(old_icon/"manifest.json",auto.OLD_SITE/"sources"/(MODEL+".json"))
    groups=defaultdict(list)
    for e in kept.values():groups[e["product"],e["domain"],e["valid_utc"],tuple(e["window_utc"])].append(e)
    current["comparisons"]=[{"product":p,"domain":d,"valid_utc":t,"window_utc":list(w),"panels":sorted(es,key=lambda e:MODELS.index(e["model"]))} for (p,d,t,w),es in sorted(groups.items())]
    current["report"].update({"mode":"archived_real_cycle_replay_baseline","comparisons":len(groups),"image_bytes":sum(e["bytes"] for e in kept.values())})
    (auto.OLD_SITE/"catalog.json").write_bytes(dump(current));(auto.OLD_SITE/"report.json").write_bytes(dump(current["report"]))
    (auto.OLD_SITE/"catalog.js").write_text("window.TITAN_CATALOG="+json.dumps(current,ensure_ascii=True).replace("</","<\\/")+";")
    configure();auto.verify_site(auto.OLD_SITE,auto.previous_entries())
    asof=datetime.fromisoformat("2026-11-26T05:28:00+00:00")
    for m in MODELS:
        f=folder(auto.CAND,m);revision=auto.fresh_revision(m) if m==MODEL else full.REVISIONS[m]
        b,rows=read_model(f,m,allowed={revision})
        age=(asof-datetime.fromisoformat(b["cycle"])).total_seconds()/3600
        receipt={"model":m,"previous_cycle":full.CYCLES[m],"discovered_cycle":b["cycle"],"selected_cycle":b["cycle"],"status":"advanced" if m==MODEL else "unchanged","acquisition_attempted":False,"archived_replay":True,"successful_new_payload_bytes":0,"original_acquisition_revision":revision,"manifest_sha256":sha(f/"manifest.json"),"freshness_pass":0<=age<=24,"age_hours_at_discovery":age,"native_entries":len(rows)}
        dest=Path("selection-inputs")/("titan-selection-"+m);dest.mkdir(parents=True);(dest/"selection-receipt.json").write_bytes(dump(receipt))
    for group in GROUPS:
        auto.group_render(group)
        dst=Path("render-inputs")/("titan-auto-panels-"+group);dst.parent.mkdir(exist_ok=True);Path("panel-output").rename(dst)
    auto.assemble()
    report=load(Path("titan-independent-review/report.json"))
    need(report["reused_panels"]==3414 and report["new_panels"]==153 and report["selection"][MODEL]["status"]=="advanced","Replay did not exercise changed cycle")
    print("ARCHIVED_REAL_ICON_ADVANCE_PASS",json.dumps({"previous":OLDER,"candidate":NEWER,"new_panels":153,"reused_panels":3414,"actual_live_acquisition":False,"historical_as_of":asof.isoformat()}),flush=True)
def publish():
    configure();auto.publish_local()
    proof=load(Path("automated-publication-report.json"))
    need(proof["published_new_local_generation"] and proof["new_numeric_models"]==[MODEL] and proof["new_panels"]==153 and proof["reused_panels"]==3414,"Replay publication mismatch")
    print("ARCHIVED_REAL_ICON_PUBLICATION_PASS",json.dumps(proof),flush=True)
if __name__=="__main__":
    if sys.argv[1]=="prepare":prepare()
    else:publish()
