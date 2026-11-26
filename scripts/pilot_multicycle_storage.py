"""Compare genuinely newer model cycles with the validated frozen baseline.
No production changes, Drive writes, rendering or fabricated forecast data."""
import hashlib,json,os,re,sys,shutil
from pathlib import Path
from datetime import datetime,timedelta,timezone
from collections import defaultdict
import numpy as np
from pilot_cycle_discovery import LEADS
from pilot_full_spatial_render import SPECS,DOMAINS,MODELS
from pilot_full_spatial_acquire import main as acquire_main
BASE_SHA="5ba074efa1010f1dbf50006da97b313b6ed8f6e2"
BASE_RUN="45721468170"
EXPERIMENT_SHA="ff68052a7b71016f29d18b85f9af98426d81fb1c"
def need(ok,msg):
    if not ok:raise ValueError(msg)
def sha(p):
    with p.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def load(p):return json.loads(p.read_bytes())
def dump(x):return json.dumps(x,sort_keys=True,separators=(",",":")).encode()
def utc(x):
    t=datetime.fromisoformat(x);need(t.utcoffset()==timedelta(0),"Non-UTC time");return t
def arrayhash(a):
    h=hashlib.sha256();h.update(dump([a.dtype.str,list(a.shape)]));h.update(np.ascontiguousarray(a).tobytes());return h.hexdigest()
def read_model(folder,model,*,allowed):
    m=load(folder/"manifest.json")
    need(m["passed"] is True and m["model"]==model and m["code_sha"] in allowed,"Invalid acquisition provenance")
    cycle=utc(m["cycle"]);leads=[h for h in LEADS if model!="aifs-025" or h%6==0]
    need(m["expected_leads"]==leads and [e["lead"] for e in m["entries"]]==leads,"Incomplete native lead schedule")
    need({p.name for p in folder.glob("*.npz")}=={e["file"] for e in m["entries"]},"Unexpected numerical files")
    rows=[]
    for e in m["entries"]:
        need(e["file"]==f"f{e['lead']:03d}.npz" and cycle+timedelta(hours=e["lead"])==utc(e["valid_utc"]),"Invalid lead identity")
        p=folder/e["file"];need(sha(p)==e["sha256"],"Numerical digest mismatch")
        shape=(round(15/e["dx"])+1,round(22/e["dx"])+1)
        with np.load(p,allow_pickle=False) as f:
            need(set(f.files)==set(e["products"]),"Field membership mismatch")
            fields={}
            for k in f.files:
                a=f[k];need(a.shape==shape and np.isfinite(a).any() and not np.isinf(a).any(),"Invalid array")
                fields[k]=arrayhash(a)
        rows.append({**e,"bytes":p.stat().st_size,"field_hashes":fields,"model":model,"cycle":m["cycle"]})
    return m,rows
def folder(root,m):return Path(root)/("titan-full-cache-"+m)
def select(model):
    baseline=folder("baseline",model);m,rows=read_model(baseline,model,allowed={BASE_SHA})
    d=load(Path("cycle-discovery.json"));info=next(x for x in d["results"] if x["model"]==model)
    need(bool(info["selected"]),"No complete candidate cycle")
    selected=utc(info["selected"]);old=utc(m["cycle"])
    need(selected>=old,"Provider discovery regressed relative to baseline")
    expected=[h for h in LEADS if model!="aifs-025" or h%6==0]
    need(info["available_leads"]==expected,"Discovery lacks full native schedule")
    if selected==old:
        # Preserve original manifest byte for byte; do not falsely label it a fresh download.
        shutil.copytree(baseline,"regional-cache")
        mode="same_cycle_reused";network=0
    else:
        sys.argv=[sys.argv[0],model];acquire_main()
        mode="new_cycle_acquired"
        nm,rr=read_model(Path("regional-cache"),model,allowed={os.environ["GITHUB_SHA"]})
        need(nm["cycle"]==info["selected"],"Unexpected acquired cycle")
        network=sum(e["provenance"]["network_payload_bytes"] for e in rr)
    result={"model":model,"mode":mode,"baseline_cycle":m["cycle"],"selected_cycle":info["selected"],"network_payload_bytes":network,"original_acquisition_revision":load(Path("regional-cache/manifest.json"))["code_sha"]}
    Path("reuse-receipt.json").write_bytes(dump(result));print("MULTICYCLE_MODEL",json.dumps(result),flush=True)
def rows_by_key(rows):
    result={(r["model"],r["cycle"],r["lead"]):r for r in rows};need(len(result)==len(rows),"Duplicate numerical identity");return result
def chart_signatures(rows):
    groups=defaultdict(list)
    for r in rows:
        for p in r["products"]:
            if p not in SPECS:continue
            window=tuple(r["windows"].get(p,[]))
            if p in ("rain","gust_1h","gust_instant"):need(len(window)==2 and window[1]==r["valid_utc"],"Window mismatch")
            # Use exact model-cycle and numeric digests. This conservatively requires
            # the same provenance as the original renderer, including panel annotations.
            groups[(p,r["valid_utc"],window)].append((r["model"],r["cycle"],r["lead"],r["sha256"]))
    return {(p,domain,valid,win):tuple(sorted(panels,key=lambda x:MODELS.index(x[0]))) for (p,valid,win),panels in groups.items() for domain in DOMAINS}
def compare_charts(old,new):
    a,b=chart_signatures(old),chart_signatures(new)
    overlap=set(a)&set(b)
    return {"baseline_charts":len(a),"candidate_charts":len(b),"same_valid_product_domain_window":len(overlap),"safe_reuse_candidates_same_renderer":sum(a[k]==b[k] for k in overlap),"changed_overlapping_charts":sum(a[k]!=b[k] for k in overlap),"new_chart_keys":len(set(b)-set(a)),"expired_chart_keys":len(set(a)-set(b))}
def compare():
    baseline=[];candidate=[];model_stats=[];base_models={};new_models={}
    for model in MODELS:
        a,x=read_model(folder("baseline",model),model,allowed={BASE_SHA})
        b,y=read_model(folder("candidate",model),model,allowed={BASE_SHA,EXPERIMENT_SHA,os.environ["GITHUB_SHA"]})
        receipt=load(folder("receipts",model)/"reuse-receipt.json")
        need(receipt["model"]==model and receipt["baseline_cycle"]==a["cycle"] and receipt["selected_cycle"]==b["cycle"],"Receipt identity mismatch")
        need(utc(b["cycle"])>=utc(a["cycle"]),"Cycle regression")
        same=a["cycle"]==b["cycle"]
        if same:
            need(receipt["mode"]=="same_cycle_reused" and receipt["network_payload_bytes"]==0 and a==b,"Same-cycle reuse evidence differs")
            need([(r["lead"],r["sha256"]) for r in x]==[(r["lead"],r["sha256"]) for r in y],"Same-cycle bytes differ")
        else:need(receipt["mode"]=="new_cycle_acquired" and b["code_sha"] in {EXPERIMENT_SHA,os.environ["GITHUB_SHA"]} and receipt["network_payload_bytes"]>0,"Fresh acquisition evidence missing")
        baseline.extend(x);candidate.extend(y);base_models[model]=x;new_models[model]=y
        ax={r["valid_utc"]:r for r in x};by={r["valid_utc"]:r for r in y};overlap=set(ax)&set(by)
        field_pairs=field_equal=0
        for t in overlap:
            u,v=ax[t],by[t]
            for f in set(u["field_hashes"])&set(v["field_hashes"]):
                if u["windows"].get(f,[])!=v["windows"].get(f,[]):continue
                field_pairs+=1;field_equal+=u["field_hashes"][f]==v["field_hashes"][f]
        model_stats.append({**receipt,"baseline_npz_bytes":sum(r["bytes"] for r in x),"candidate_npz_bytes":sum(r["bytes"] for r in y),"reusable_native_leads":len(y) if same else 0,"new_native_leads":0 if same else len(y),"overlapping_valid_times":len(overlap),"comparable_field_pairs":field_pairs,"identical_field_pairs":field_equal,"new_cycle_manifest_bytes":0 if same else (folder("candidate",model)/"manifest.json").stat().st_size})
    need(len(baseline)==len(candidate)==81,"Expected complete five-model inventories")
    need(any(r["mode"]=="new_cycle_acquired" for r in model_stats),"No genuinely new cycle; comparison inconclusive")
    old=rows_by_key(baseline);new=rows_by_key(candidate)
    overlap=set(old)&set(new);need(all(old[k]["sha256"]==new[k]["sha256"] for k in overlap),"Same native identity with different bytes")
    old_hash={r["sha256"]:r["bytes"] for r in baseline};new_hash={r["sha256"]:r["bytes"] for r in candidate}
    distinct_new=sum(v for k,v in new_hash.items() if k not in old_hash)
    identity_new=sum(new[k]["bytes"] for k in set(new)-set(old))
    manifest_growth=sum(m["new_cycle_manifest_bytes"] for m in model_stats)
    charts=compare_charts(baseline,candidate)
    # Counterfactual staggered refresh, using the measured fields, not a separate
    # observed provider cycle or a claimed render benchmark.
    stagger=[]
    for m in MODELS:
        if any(s["model"]==m and s["mode"]=="new_cycle_acquired" for s in model_stats):
            rr=[r for k in MODELS for r in (new_models[k] if k==m else base_models[k])]
            stagger.append({"only_advanced_model":m,**compare_charts(baseline,rr)})
    report={"passed":True,"mode":"actual_baseline_and_new_discovered_cycles","baseline_run":BASE_RUN,"candidate_run":os.environ["GITHUB_RUN_ID"],"baseline_native_entries":len(old),"candidate_native_entries":len(new),"same_native_identities":len(overlap),"new_native_identities":len(set(new)-set(old)),"baseline_npz_bytes":sum(r["bytes"] for r in baseline),"candidate_npz_bytes":sum(r["bytes"] for r in candidate),"incremental_native_identity_bytes":identity_new,"incremental_unique_npz_content_bytes":distinct_new,"new_manifest_bytes":manifest_growth,"network_payload_bytes":sum(m["network_payload_bytes"] for m in model_stats),"models":model_stats,"ifs_010_recovery_status":next(m.get("recovery_status","not_applicable") for m in model_stats if m["model"]=="ifs-010"),"chart_reuse":charts,"staggered_counterfactual":stagger,"notes":["No new rendering or Drive writes; chart signatures identify safe reuse candidates only under unchanged renderer/style/domain settings.","Cross-cycle array equality is diagnostic only; preserve model-cycle provenance even if bytes coincide.","Two snapshots do not establish annual storage growth or sustained update availability. Staggered refreshes are counterfactual combinations of the measured data, not additional actual acquisitions."]}
    Path("multicycle-storage-report.json").write_text(json.dumps(report,indent=2))
    print("MULTICYCLE_STORAGE",json.dumps(report),flush=True)
if __name__=="__main__":
    if sys.argv[1]=="compare":compare()
    else:select(sys.argv[1])
