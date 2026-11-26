"""Third-generation retention test on the exact isolated remote archive.
Third marker is a same-weather storage fixture, not an operational update.
Move only the unreferenced older AIFS chart ZIP to Drive Trash."""
import json,hashlib,os,re,time,tempfile,shutil
from pathlib import Path
import requests
import titan_independent_remote_publication as base
from src import titan_fixture_drive_transport as remote
from pilot_multicycle_storage import need,sha,dump,load,read_model,folder
from pilot_full_spatial_render import MODELS
from titan_panel_retention_policy import assert_inventory
PREFIX="wawp:validation/titan-spatial-storage/run-45767345982-1"
INTENT="retirement-intent.json";RECEIPT="retirement-receipt.json"
base.prefix=lambda:PREFIX
base.NAME=re.compile(r"(numeric|charts|metadata|evidence)-[0-9a-f]{64}\.zip|generation-[0-9a-f]{64}\.json|commit-00000[123]\.json|retirement-(intent|receipt)\.json")
def record(s,name,value):
    p=s.root/("write-"+name);p.write_bytes(dump(value))
    s.refresh()
    if name not in s.inv:
        remote.rclone_copy(str(p),s.prefix+"/"+name);s.refresh([name])
    got=read_record(s,name)
    need(got==value,"Immutable journal differs")
def read_record(s,name):
    p=s.root/("read-"+name)
    s.t._get(s.inv[name],{"supportsAllDrives":"true","alt":"media"},p)
    need(p.stat().st_size==s.inv[name]["size"],"Journal size mismatch")
    return load(p)
def meta(s,name,info,parent=None):
    x=s.t._get(info,{"supportsAllDrives":"true","fields":"id,name,size,trashed,parents,capabilities(canTrash,canDownload)"})
    need(x["id"]==info["id"] and x["name"]==name and int(x["size"])==info["size"] and isinstance(x["trashed"],bool),"Exact Drive identity mismatch")
    if parent is not None:need(x["parents"]==[parent],"Unexpected parent")
    return x
def charts(g):return {x["object"]["name"] for x in g["charts"].values()}
def permanent(g):
    return [(x["object"],x["members"]) for x in g["numeric"].values()]+[(g[k]["object"],g[k]["members"]) for k in ("metadata","evidence")]
def generations(s):
    chain=s.markers();need(len(chain) in (2,3),"Unexpected fixture commit count")
    refs=[m["generation"] for m,_ in chain];gs=[load(s.read(r)) for r in refs]
    a,b=gs[:2]
    need(a["stage"]=="old" and b["stage"]=="new" and a["source_run"]==base.OLD_RUN and b["source_run"]==base.NEW_RUN,"Original generation source differs")
    need(all((a[k][m]==b[k][m])==(m!="aifs-025") for k in ("numeric","charts") for m in MODELS) and a["evidence"]==b["evidence"],"Unexpected changed model")
    if len(gs)==3:need(gs[2]=={**b,"retention_fixture":"third_marker_same_weather_no_new_forecast"},"Third marker is not expected fixture")
    return chain,refs,gs
def verify_permanent(s,gs,fresh=False):
    seen=set()
    for g in gs:
        for r,m in permanent(g):
            if r["name"] not in seen:
                base.verify_pack(s.read(r,fresh=fresh),m);seen.add(r["name"])
    return seen
def protect(s,gs,refs):
    registered=set().union(*(charts(g) for g in gs))
    registered|={r["name"] for g in gs for r,_ in permanent(g)}
    registered|={r["name"] for r in refs}
    registered|={f"commit-{i:06}.json" for i in range(1,len(gs)+1)}
    need(set(s.inv)-{INTENT,RECEIPT}<=registered,"Unexpected unregistered object")
    return registered
def reconcile_removed(s,n):
    actual=s.t.inventory();s.inventory_calls+=1;s.check_inventory(actual)
    expected={k:v for k,v in s.inv.items() if k!=n}
    need(actual==expected,"Inventory changed beyond exact obsolete pack")
    s.inv=actual
def trash(s,n,info,parent,attempts):
    for delay in (0,45,120):
        if delay:time.sleep(delay)
        s.refresh();state=meta(s,n,info,parent)
        if state["trashed"]:
            reconcile_removed(s,n);return
        need(state.get("capabilities",{}).get("canTrash") is True,"Trash capability absent")
        remote.pace()
        status=0;reasons=[]
        try:
            with requests.patch(s.t._url(info),params={"supportsAllDrives":"true","fields":"id,name,size,trashed"},headers=s.t._headers(),json={"trashed":True},timeout=(20,60),allow_redirects=False) as resp:
                status=resp.status_code
                if status==200:
                    x=resp.json();need(x["id"]==info["id"] and x["name"]==n and int(x["size"])==info["size"] and x["trashed"] is True,"Trash response differs")
                else:
                    try:reasons=[v.get("reason") for v in resp.json().get("error",{}).get("errors",[]) if isinstance(v.get("reason"),str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}",v["reason"])]
                    except (ValueError,TypeError,AttributeError):pass
        except requests.RequestException:pass
        attempts.append({"status":status,"reasons":reasons})
        if meta(s,n,info,parent)["trashed"]:
            reconcile_removed(s,n);return
        need(status==429 or status==403 and bool(set(reasons)&{"rateLimitExceeded","userRateLimitExceeded","backendError"}),"Trash not confirmed; preserve journal")
    raise ValueError("Trash retry budget exhausted")
def main():
    report={"passed":False,"mode":"isolated_independent_third_generation_retirement","prefix":PREFIX,"checks":{},"patch_attempts":[],"stage":"inspect","production_changed":False}
    try:
        with tempfile.TemporaryDirectory() as td:
            s=base.Store(Path(td));need(s.prefix==PREFIX,"Wrong fixture prefix")
            chain,refs,gs=generations(s);old,new=gs[:2]
            n=old["charts"]["aifs-025"]["object"]["name"];r=old["charts"]["aifs-025"]["object"]
            need(n not in charts(new),"Old AIFS pack still referenced by new")
            report["stage"]="verify_retained"
            s.verify_generation(refs[1]);verify_permanent(s,gs)
            if len(gs)==2:
                need(INTENT not in s.inv and RECEIPT not in s.inv,"Unexpected prior retirement journal")
                s.verify_generation(refs[0])
                third={**new,"retention_fixture":"third_marker_same_weather_no_new_forecast"}
                p=s.root/"third.json";p.write_bytes(dump(third));r3=base.ref("generation",p)
                before=s.markers();s.batch([(r3,p)]);need(s.markers()==before,"Staging altered chain")
                s.commit(r3,3)
                chain,refs,gs=generations(s)
            need(len(gs)==3,"Third fixture marker missing")
            s.verify_generation(refs[1]);s.verify_generation(refs[2])
            report["checks"]["current_previous_verified"]=True
            registered=protect(s,gs,refs)
            marker=meta(s,"commit-000001.json",s.inv["commit-000001.json"]);need(marker["trashed"] is False and len(marker["parents"])==1,"Fixture parent unknown");parent=marker["parents"][0]
            prior=read_record(s,INTENT) if INTENT in s.inv else None
            active={k for k in s.inv if k.startswith("charts-")}
            already=[] if n in active else [n]
            # No significant-case pins exist in this dedicated fixture. Production
            # must supply a verified pin registry before enabling this policy.
            due=assert_inventory([charts(g) for g in gs],active,pinned=(),already_retired=already)
            need(due==({n} if n in active else set()),"Unexpected retirement set")
            need(n in registered and n.startswith("charts-") and not charts(gs[1])&{n} and not charts(gs[2])&{n},"Unsafe chart candidate")
            if n in active:info=dict(s.inv[n])
            else:
                need(prior is not None,"Missing chart without durable intent")
                info=prior["drive_object"]
            need(info["size"]==r["bytes"],"Candidate size differs")
            state=meta(s,n,info,parent);need(state["trashed"]==(n not in active),"Active and Trash disagree")
            if not state["trashed"]:base.verify_pack(s.read(r),old["charts"]["aifs-025"]["members"])
            report["checks"]["obsolete_exact_identity_verified"]=True
            protected={k:v for k,v in s.inv.items() if k not in (n,INTENT,RECEIPT)}
            need(set(protected)==registered-{n},"Protected inventory incomplete")
            intent={"schema":1,"mode":"third_generation_storage_fixture","source_run":"45767345982","candidate":r,"drive_object":info,"parent":parent,"commit_chain":[h for _,h in chain],"protected_inventory_sha256":hashlib.sha256(dump(protected)).hexdigest(),"retained_refs":refs[1:],"pinned_fixture_packs":[]}
            if prior:need(prior==intent,"Existing retirement intent differs")
            record(s,INTENT,intent);report["checks"]["durable_intent"]=True
            report["stage"]="trash_exact_pack"
            if not state["trashed"]:
                need(RECEIPT not in s.inv,"Receipt exists while active")
                trash(s,n,info,parent,report["patch_attempts"])
            need(meta(s,n,info,parent)["trashed"] is True and n not in s.inv,"Trash not confirmed")
            report["checks"]["exact_trash_verified"]=True
            report["stage"]="post_retirement_verify"
            need(s.markers()==chain and {k:v for k,v in s.inv.items() if k not in (INTENT,RECEIPT)}==protected,"Protected inventory changed")
            # Independent media fetch after mutation for all numerical and metadata
            # objects, as well as every still-referenced chart pack.
            verify_permanent(s,gs,fresh=True)
            for x in gs[2]["charts"].values():base.verify_pack(s.read(x["object"],fresh=True),x["members"])
            s.verify_generation(refs[1]);s.verify_generation(refs[2])
            receipt={"schema":1,"intent_sha256":hashlib.sha256(dump(intent)).hexdigest(),"candidate":r,"status":"trashed_verified","current_previous_verified":True,"protected_inventory_sha256":intent["protected_inventory_sha256"]}
            record(s,RECEIPT,receipt);s.refresh()
            need({k:v for k,v in s.inv.items() if k not in (INTENT,RECEIPT)}==protected and s.markers()==chain,"Post-retirement state changed")
            report.update({"passed":True,"stage":"complete","commit_count":3,"third_marker_replay":True,"retired_pack":n,"retired_bytes":r["bytes"],"active_chart_packs":sum(k.startswith("charts-") for k in s.inv),"permanent_numeric_packs":sum(k.startswith("numeric-") for k in s.inv),"active_remote_bytes":sum(v["size"] for v in s.inv.values()),"numeric_reupload_bytes":0,"chart_reupload_bytes":0,"readback_bytes":s.read_bytes,"inventory_calls":s.inventory_calls,"direct_http_requests":remote.HTTP_REQUESTS,"permanent_delete":False})
            report["checks"]["post_retirement_readback"]=True;report["checks"]["permanent_archive_preserved"]=True;report["checks"]["durable_receipt"]=True
    except Exception as e:
        report["error"]=str(e)[:250] if isinstance(e,ValueError) else type(e).__name__
    finally:
        Path("independent-retirement-report.json").write_bytes(dump(report));print("INDEPENDENT_RETIREMENT",json.dumps(report),flush=True)
    if not report["passed"]:raise SystemExit("Retirement not verified; preserve remaining objects")
if __name__=="__main__":main()
