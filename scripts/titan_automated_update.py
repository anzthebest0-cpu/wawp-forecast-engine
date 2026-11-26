"""Automated research update rehearsal, isolated Actions artifacts and local publication.
Do not write Drive, change Pages, or activate an operational schedule."""
import hashlib,json,os,sys,shutil,time,tempfile
from pathlib import Path
from collections import defaultdict
from datetime import datetime,timedelta,timezone
import numpy as np
from PIL import Image
import pilot_full_spatial_acquire as acq
import titan_full_independent_panels as full
from pilot_multicycle_storage import read_model,folder,need,sha,dump,load,utc
from pilot_cycle_discovery import LEADS,MODELS
from pilot_full_spatial_render import GROUPS,SPECS,DOMAINS,render
BASE_RUN="45759575549"
BASELINE=None
def configure_rolling():
    global BASE_RUN,BASELINE
    BASELINE=load(Path("rolling-baseline.json"));BASE_RUN=str(BASELINE["run"])
    full.CYCLES.update({m:x["cycle"] for m,x in BASELINE["sources"].items()})
    full.REVISIONS.update({m:x["acquisition_sha"] for m,x in BASELINE["sources"].items()})
BASE=Path("previous-inputs")
CAND=Path("cache-inputs")
OLD_SITE=Path("previous-site")
MAX_AGE_H=24 # trial gate; not an operational SLA
# Only the isolated archive replay sets this mapping to a pinned, validated historical revision.
REPLAY_REVISIONS={}
def fresh_revision(model):return REPLAY_REVISIONS.get(model,os.environ["GITHUB_SHA"])
RECOVERED_REV="a41d84f17b4227462e3aa0c40001b11cece5dfd9"
RECOVERED={
    "ifs-010":("2026-11-26T00:00:00+00:00","5cb48c0d9dc278b765b2101561c9aba00e9bb17c0e2a091a864d2f86bab05ed3"),
    "aifs-025":("2026-11-26T06:00:00+00:00","79ad6a6a9ce26351cbe815ad917de2ce5c41c59aa23d68dd2fe9d9b8a37b5311")}
def allowed_new(model):
    return {fresh_revision(model)}|({RECOVERED_REV} if model in RECOVERED else set())
def check_new(model,m,f):
    if m["code_sha"]==RECOVERED_REV:
        need(model in RECOVERED and (m["cycle"],sha(f/"manifest.json"))==RECOVERED[model] and m.get("recovery_status")=="strict_targeted_recovery","Unverified recovered manifest")
    else:need(m["code_sha"]==fresh_revision(model),"Unexpected acquisition revision")
def use_recovered(model,target,old,chosen,asof,rows,receipt):
    if model not in RECOVERED:return False
    c,h=RECOVERED[model];f=Path("recovered-input")/model
    if not f.is_dir() or not utc(old["cycle"])<utc(c)<=asof or (asof-utc(c)).total_seconds()/3600>MAX_AGE_H:return False
    if chosen and utc(c)>utc(chosen):return False
    m,newrows=read_model(f,model,allowed={RECOVERED_REV});check_new(model,m,f)
    need(all(set(a["products"])-{"rain","gust_1h","gust_instant"}<=set(b["products"]) for a,b in zip(rows,newrows)),"Recovered core field missing")
    shutil.copytree(f,target)
    receipt.update({"status":"advanced","recovered_artifact_run":45828477995,"acquisition_sha":RECOVERED_REV,"successful_new_payload_bytes":0,"recovery_reused_verified_cache":True})
    return True
def check_model(root,m,allowed):
    return read_model(folder(root,m),m,allowed=allowed)
def candidate_select(model):
    old,rows=check_model(BASE,model,{full.REVISIONS[model]})
    need(old["cycle"]==full.CYCLES[model],"Baseline cycle changed")
    if BASELINE:need(sha(folder(BASE,model)/"manifest.json")==BASELINE["sources"][model]["manifest_sha256"],"Baseline manifest differs")
    info=next(x for x in load(Path("cycle-discovery.json"))["results"] if x["model"]==model)
    asof=utc(load(Path("cycle-discovery.json"))["as_of_utc"])
    target=Path("regional-cache")
    expected=[h for h in LEADS if model!="aifs-025" or h%6==0]
    chosen=info.get("selected"); reason=None
    receipt={"model":model,"previous_cycle":old["cycle"],"discovered_cycle":chosen,"acquisition_attempted":False,"successful_new_payload_bytes":0,"status":"unchanged"}
    if not chosen:reason="provider_unavailable"
    elif utc(chosen)<utc(old["cycle"]):reason="provider_regression"
    elif info.get("available_leads")!=expected:reason="incomplete_native_schedule"
    elif utc(chosen)>asof:reason="future_cycle"
    elif utc(chosen)>utc(old["cycle"]) and model in RECOVERED and chosen==RECOVERED[model][0] and use_recovered(model,target,old,chosen,asof,rows,receipt):
        pass
    elif utc(chosen)>utc(old["cycle"]):
        receipt["acquisition_attempted"]=True
        try:
            sys.argv=[sys.argv[0],model];acq.main()
            new,newrows=read_model(target,model,allowed={os.environ["GITHUB_SHA"]})
            need(new["cycle"]==chosen,"Unexpected cycle")
            # No silent loss of core fields. Rain/gust are explicitly optional
            # only when the native window cannot be constructed.
            optional={"rain","gust_1h","gust_instant"}
            for a,b in zip(rows,newrows):
                need(set(a["products"])-optional<=set(b["products"]),"Core product missing")
            receipt["status"]="advanced"
            receipt["successful_new_payload_bytes"]=sum(e["provenance"]["network_payload_bytes"] for e in newrows)
        except (Exception,SystemExit) as e:
            if target.exists():
                failed=target/"manifest.json"
                if failed.is_file():
                    fm=load(failed)
                    receipt["failed_completed_payload_bytes"]=sum(x["provenance"]["network_payload_bytes"] for x in fm.get("entries",[]))
                    receipt["failure_detail"]=str(fm.get("error",str(e)))[:300]
                shutil.move(str(target),"rejected-cache")
            reason="acquisition_or_qc_failed"
    if receipt["status"]!="advanced" and reason and not target.exists():
        use_recovered(model,target,old,chosen,asof,rows,receipt)
    if receipt["status"]!="advanced":
        shutil.copytree(folder(BASE,model),target)
        receipt["status"]="fallback" if reason else "unchanged"
        receipt["fallback_reason"]=reason
    m,r=read_model(target,model,allowed={full.REVISIONS[model]}|allowed_new(model))
    receipt["selected_cycle"]=m["cycle"];receipt["manifest_sha256"]=sha(target/"manifest.json")
    age=(asof-utc(m["cycle"])).total_seconds()/3600
    receipt["age_hours_at_discovery"]=round(age,3)
    receipt["freshness_pass"]=0<=age<=MAX_AGE_H
    receipt["native_entries"]=len(r)
    Path("selection-receipt.json").write_bytes(dump(receipt));print("AUTO_MODEL",json.dumps(receipt),flush=True)
def receipts():
    return {m:load(Path("selection-inputs")/("titan-selection-"+m)/"selection-receipt.json") for m in MODELS}
def read_dynamic():
    rr=receipts();g=defaultdict(list);sources={};n=0
    for model in MODELS:
        r=rr[model];need(r["model"]==model and r["freshness_pass"] and r["previous_cycle"]==full.CYCLES[model],"Selection/freshness gate failed")
        f=folder(CAND,model)
        m,rows=read_model(f,model,allowed={full.REVISIONS[model]}|allowed_new(model))
        need(m["cycle"]==r["selected_cycle"] and sha(f/"manifest.json")==r["manifest_sha256"],"Candidate receipt differs")
        need((r["status"]=="advanced" and m["code_sha"] in allowed_new(model) and utc(m["cycle"])>utc(r["previous_cycle"])) or (r["status"] in ("unchanged","fallback") and m["code_sha"]==full.REVISIONS[model] and m["cycle"]==r["previous_cycle"]),"Selection lineage differs")
        if r["status"]=="advanced":check_new(model,m,f)
        if model=="ifs-010" and m["code_sha"]==full.QC_SHA:need(m.get("qc_policy")=="experimental_localized_rh_quarantine_v1","Missing prior QC lineage")
        sources[model]={"cycle":m["cycle"],"acquisition_sha":m["code_sha"],"entries":len(rows),"manifest_sha256":sha(f/"manifest.json")}
        n+=len(rows)
        for e in rows:
            for p in e["products"]:
                if p not in SPECS:continue
                w=tuple(e["windows"].get(p,[]))
                if p in ("rain","gust_1h","gust_instant"):
                    need(len(w)==2 and w[1]==e["valid_utc"],"Native window invalid")
                    duration=(utc(w[1])-utc(w[0])).total_seconds()
                    need(duration>0 if p=="rain" else duration==3600 if p=="gust_1h" else duration==0,"Wrong window duration")
                else:need(not w,"Unexpected window")
                q=[]
                if p.startswith("rh"):
                    q=[{"level_hpa":d["level_hpa"],"lead":d["lead"],"above_ground_masked":d["quarantine"]["above_ground_masked"],"raw_grib_sha256":d["rh_grib_sha256"]} for d in e["provenance"].get("rh_range_diagnostics",[]) if d["level_hpa"]==int(p[2:]) and d.get("quarantine",{}).get("above_ground_masked",0)>0]
                g[p,e["valid_utc"],w].append((model,m["cycle"],e,f/e["file"],q))
    need(n==81 and set(k[0] for k in g)==set(SPECS) and len(SPECS)==25,"Candidate inventory incomplete")
    return g,sources
def previous_entries():
    c=load(OLD_SITE/"catalog.json");need(c["report"]["passed"] and c["report"]["native_leads"]==81 and c["report"]["panels"]==(BASELINE["panels"] if BASELINE else 3567),"Invalid previous catalogue")
    if BASELINE:need(sha(OLD_SITE/"catalog.json")==BASELINE["catalog_sha256"],"Baseline catalogue differs")
    need(c["report"]["renderer"]==full.renderer_identity(),"Renderer changed; cannot reuse prior panels")
    for m in MODELS:
        need(c["report"]["sources"][m]["cycle"]==full.CYCLES[m],"Previous source cycle mismatch")
    entries={}
    for group in c["comparisons"]:
        for e in group["panels"]:
            i={k:e[k] for k in ("renderer","product","domain","valid_utc","window_utc","model","init_utc","lead","numeric_sha256","qc")}
            need(e["id"]==full.key(i) and e["id"] not in entries and e["path"]=="panels/"+e["id"]+".webp","Previous panel identity mismatch")
            entries[e["id"]]=e
    need(len(entries)==(BASELINE["panels"] if BASELINE else 3567) and {x.name for x in (OLD_SITE/"panels").glob("*.webp")}=={i+".webp" for i in entries},"Previous image inventory differs")
    return entries
def verify_image(p,e):
    need(p.is_file() and p.stat().st_size==e["bytes"] and sha(p)==e["sha256"],"Panel hash mismatch")
    with Image.open(p) as im:
        need(im.format=="WEBP" and list(im.size)==e["dimensions"],"Image metadata differs")
        im.load()
def group_render(group):
    g,sources=read_dynamic();revision=full.renderer_identity();previous=previous_entries()
    out=Path("panel-output");out.mkdir();(out/"panels").mkdir();temp=out/"work";temp.mkdir()
    entries=[];reused=0;reuse_bytes=0;rendered=0;start=time.monotonic()
    for (p,t,w),panels in sorted(g.items()):
        if p not in GROUPS[group]:continue
        for d in DOMAINS:
            for panel in panels:
                i=full.identity(p,t,w,d,panel,revision);ident=full.key(i);target=out/"panels"/(ident+".webp")
                need(not target.exists(),"Duplicate panel")
                if ident in previous:
                    e=previous[ident];need({k:e[k] for k in i}==i,"Reuse identity mismatch")
                    src=OLD_SITE/e["path"];verify_image(src,e);shutil.copyfile(src,target)
                    reused+=1;reuse_bytes+=e["bytes"]
                else:
                    r=render(temp,p,t,w,[panel[:4]],d);(temp/r["file"]).rename(target)
                    need(r["models"]==[{"model":i["model"],"init_utc":i["init_utc"],"lead":i["lead"],"numeric_sha256":i["numeric_sha256"]}] and sha(target)==r["sha256"],"Render provenance mismatch")
                    with Image.open(target) as im:dims=list(im.size)
                    e={**i,"id":ident,"path":"panels/"+target.name,"sha256":r["sha256"],"bytes":target.stat().st_size,"dimensions":dims}
                    verify_image(target,e);rendered+=1
                entries.append(e)
    temp.rmdir();need(bool(entries),"Empty group")
    stats={"group":group,"panels":len(entries),"reused":reused,"rendered":rendered,"reused_bytes":reuse_bytes,"new_bytes":sum(e["bytes"] for e in entries)-reuse_bytes,"seconds":round(time.monotonic()-start,2)}
    (out/"catalog.json").write_bytes(dump({"group":group,"renderer":revision,"sources":sources,"entries":entries}))
    (out/"reuse.json").write_bytes(dump(stats));print("AUTO_PANEL_GROUP",json.dumps(stats),flush=True)
def verify_site(path,expected):
    data=load(path/"catalog.json")
    seen={}
    for c in data["comparisons"]:
        for e in c["panels"]:
            need(e["id"] not in seen and e["id"] in expected and e==expected[e["id"]],"Site catalogue differs")
            need((e["product"],e["domain"],e["valid_utc"],e["window_utc"])==(c["product"],c["domain"],c["valid_utc"],c["window_utc"]),"Comparison window differs")
            verify_image(path/e["path"],e);seen[e["id"]]=e
    need(set(seen)==set(expected),"Site panels incomplete")
    need({p.name for p in (path/"panels").glob("*.webp")}=={i+".webp" for i in expected},"Site image inventory differs")
def assemble():
    g,sources=read_dynamic();revision=full.renderer_identity()
    expected={full.key(i):i for (p,t,w),ps in g.items() for d in DOMAINS for a in ps for i in [full.identity(p,t,w,d,a,revision)]}
    old=previous_entries();verify_site(OLD_SITE,old)
    for model in MODELS:
        m,rows=check_model(BASE,model,{full.REVISIONS[model]})
        need(sha(folder(BASE,model)/"manifest.json")==load(OLD_SITE/"catalog.json")["report"]["sources"][model]["manifest_sha256"],"Previous numeric/site mismatch")
    out=Path("titan-independent-review");out.mkdir();(out/"panels").mkdir();(out/"sources").mkdir();entries={};stats=[]
    for group in GROUPS:
        f=Path("render-inputs")/("titan-auto-panels-"+group);c=load(f/"catalog.json");s=load(f/"reuse.json")
        need(c["group"]==group and c["renderer"]==revision and c["sources"]==sources,"Render source mismatch")
        need({p.name for p in (f/"panels").glob("*.webp")}=={e["id"]+".webp" for e in c["entries"]},"Render files differ")
        group_reuse=0;group_reuse_bytes=0
        for e in c["entries"]:
            ident=e["id"];need(ident in expected and ident not in entries and e["product"] in GROUPS[group],"Unexpected panel")
            need({k:e[k] for k in expected[ident]}==expected[ident] and e["path"]=="panels/"+ident+".webp","Candidate identity differs")
            p=f/e["path"];verify_image(p,e)
            if ident in old:
                need(e==old[ident],"Reused panel bytes/provenance changed");group_reuse+=1;group_reuse_bytes+=e["bytes"]
            shutil.copyfile(p,out/e["path"]);entries[ident]=e
        need(s["reused"]==group_reuse and s["reused_bytes"]==group_reuse_bytes and s["rendered"]+s["reused"]==len(c["entries"]),"Reuse receipt mismatch")
        stats.append(s)
    need(set(entries)==set(expected),"Missing candidate panels")
    rr=receipts()
    for m in MODELS:shutil.copyfile(folder(CAND,m)/"manifest.json",out/"sources"/(m+".json"))
    groups=defaultdict(list)
    for e in entries.values():groups[e["product"],e["domain"],e["valid_utc"],tuple(e["window_utc"])].append(e)
    comparisons=[{"product":p,"domain":d,"valid_utc":t,"window_utc":list(w),"panels":sorted(es,key=lambda e:MODELS.index(e["model"]))} for (p,d,t,w),es in sorted(groups.items())]
    need(all(len(set(e["model"] for e in c["panels"]))==len(c["panels"]) for c in comparisons),"Duplicate models")
    report={"passed":True,"mode":"archived_real_cycle_replay" if REPLAY_REVISIONS else "automated_research_rehearsal","renderer":revision,"sources":sources,"products":25,"domains":list(DOMAINS),"native_leads":81,"panels":len(entries),"comparisons":len(comparisons),"image_bytes":sum(e["bytes"] for e in entries.values()),"quarantine_panel_count":sum(bool(e["qc"]) for e in entries.values()),"production_deployed":False,"selection":rr,"groups":stats,"reused_panels":len(set(entries)&set(old)),"new_panels":len(set(entries)-set(old)),"new_image_bytes":sum(e["bytes"] for k,e in entries.items() if k not in old),"reused_image_bytes":sum(e["bytes"] for k,e in entries.items() if k in old)}
    data={"report":report,"labels":full.LABELS,"products":{k:{"title":v[0],"unit":v[1]} for k,v in SPECS.items()},"comparisons":comparisons}
    (out/"catalog.json").write_bytes(dump(data));(out/"catalog.js").write_text("window.TITAN_CATALOG="+json.dumps(data,ensure_ascii=True).replace("</","<\\/")+";")
    page=(Path(__file__).parent/"titan_full_panels.html").read_text().replace("Frozen model-cycle comparison | Research preview","Automated update rehearsal | Research preview").replace("IFS 0.1° includes an experimental RH cell quarantine; affected panels are flagged.","Previous-cycle fallback is possible; check each model initialization. Any experimental RH quarantine is flagged on the affected product.")
    (out/"index.html").write_text(page);(out/"report.json").write_bytes(dump(report))
    verify_site(out,entries)
    print("AUTO_ASSEMBLE_PASS",json.dumps(report),flush=True)
def publish_local():
    # This command runs only after browser tests. Durable operational transport is
    # deliberately not enabled. Demonstrate a local manifest-last transaction.
    old=previous_entries();verify_site(OLD_SITE,old)
    out=Path("titan-independent-review");data=load(out/"catalog.json");new={e["id"]:e for c in data["comparisons"] for e in c["panels"]}
    verify_site(out,new);report=data["report"];need(report["passed"],"Invalid candidate")
    numeric=Path("new-numerical-archive");numeric.mkdir();numeric_bytes=0
    for m in MODELS:
        a,x=check_model(BASE,m,{full.REVISIONS[m]});b,y=check_model(CAND,m,{full.REVISIONS[m]}|allowed_new(m))
        need(report["sources"][m]["manifest_sha256"]==sha(folder(CAND,m)/"manifest.json"),"Candidate numeric manifest mismatch")
        if a["cycle"]==b["cycle"]:
            need(a==b and [(e["lead"],e["sha256"]) for e in x]==[(e["lead"],e["sha256"]) for e in y],"Unchanged model differs")
        else:
            dest=folder(numeric,m);shutil.copytree(folder(CAND,m),dest);read_model(dest,m,allowed=allowed_new(m));check_new(m,b,dest)
            numeric_bytes+=sum(p.stat().st_size for p in dest.iterdir() if p.is_file())
    (numeric/"archive-index.json").write_bytes(dump({"source_run":os.environ["GITHUB_RUN_ID"],"previous_run":BASE_RUN,"archived_replay":bool(REPLAY_REVISIONS),"new_models":[m for m in MODELS if folder(numeric,m).exists()],"new_numeric_bytes":numeric_bytes}))
    with tempfile.TemporaryDirectory(prefix="titan-local-publication-") as td:
        root=Path(td);objects=root/"panels";objects.mkdir();generations=root/"generations";generations.mkdir()
        def stage(site,es):
            for ident,e in es.items():
                p=objects/(ident+".webp")
                if not p.exists():shutil.copyfile(site/e["path"],p)
                verify_image(p,e)
            manifest={"catalog_sha256":sha(site/"catalog.json"),"panel_ids":sorted(es),"numeric_sources":load(site/"catalog.json")["report"]["sources"]}
            h=hashlib.sha256(dump(manifest)).hexdigest();p=generations/(h+".json")
            if not p.exists():p.write_bytes(dump(manifest))
            need(sha(p)==h,"Immutable generation differs")
            return h
        def verify_generation(h,es):
            p=generations/(h+".json");need(sha(p)==h and load(p)["panel_ids"]==sorted(es),"Generation manifest mismatch")
            for i,e in es.items():verify_image(objects/(i+".webp"),e)
        def commit(h,previous,es):
            verify_generation(h,es)
            p=root/"pending.json";p.write_bytes(dump({"current":h,"previous":previous}));os.replace(p,root/"pointer.json")
        h1=stage(OLD_SITE,old);commit(h1,None,old);before=(root/"pointer.json").read_bytes()
        h2=stage(out,new);need((root/"pointer.json").read_bytes()==before,"Staging changed pointer")
        bad=dict(new);bad.pop(next(iter(bad)))
        try:commit(h2,h1,bad)
        except ValueError:pass
        else:raise ValueError("Incomplete candidate committed")
        need((root/"pointer.json").read_bytes()==before,"Rejected candidate changed pointer")
        advanced=any(x["status"]=="advanced" for x in report["selection"].values())
        if advanced:
            commit(h2,h1,new);saved=(root/"pointer.json").read_bytes()
            commit(h2,h1,new);need((root/"pointer.json").read_bytes()==saved,"Retry not idempotent")
            keep=set(old)|set(new)
        else:
            need(set(old)==set(new) and all(old[i]==new[i] for i in old),"No-advance candidate differs")
            need((root/"pointer.json").read_bytes()==before,"No-op changed pointer")
            keep=set(old)
        need({p.stem for p in objects.glob("*.webp")}==keep,"Rolling union differs")
        verify_generation(h1,old)
        if advanced:verify_generation(h2,new)
        proof={"passed":True,"mode":"local_disposable_publication_only","archived_replay":bool(REPLAY_REVISIONS),"advanced":advanced,"published_new_local_generation":advanced,"no_op_preserved":not advanced,"previous_preserved":True,"invalid_candidate_rejected":True,"staging_preserved_pointer":True,"retry_idempotent":True,"retained_panel_union":len(keep),"retained_panel_union_bytes":sum(p.stat().st_size for p in objects.glob("*.webp")),"new_numeric_bytes":numeric_bytes,"new_numeric_models":[m for m in MODELS if folder(numeric,m).exists()],"new_panels":report["new_panels"],"reused_panels":report["reused_panels"],"new_image_bytes":report["new_image_bytes"],"candidate_catalog_sha256":sha(out/"catalog.json"),"production_deployed":False,"remote_archive_written":False}
        Path("automated-publication-report.json").write_bytes(dump(proof));print("AUTO_PUBLICATION_PASS",json.dumps(proof),flush=True)
        if BASELINE and advanced:
            receipt={"run":int(os.environ["GITHUB_RUN_ID"]),"revision":os.environ["GITHUB_SHA"],"advanced":True,"catalog_sha256":sha(out/"catalog.json"),"panels":len(new),"sources":report["sources"],"previous_run":BASE_RUN}
            Path("next-rolling-baseline.json").write_bytes(dump(receipt));print("ROLLING_ADVANCE_PASS",json.dumps(receipt),flush=True)
if __name__=="__main__":
    if Path("rolling-baseline.json").exists():configure_rolling()
    cmd=sys.argv[1]
    if cmd=="select":candidate_select(sys.argv[2])
    elif cmd=="assemble":assemble()
    elif cmd=="publish":publish_local()
    else:group_render(cmd)
