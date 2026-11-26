"""Targeted remote chart retirement recovery, no local artifact regeneration.
Only the original obsolete chart pack in this exact frozen fixture may be trashed.
All publication and numeric objects are preserved. Never permanently delete."""
import hashlib,json,re,time,tempfile
from pathlib import Path
from unittest.mock import patch
import requests
from src import titan_fixture_drive_transport as remote
from rehearse_titan_remote_spatial_storage import Store,check_ref,verify_pack,require,sha,dump,load,COMMIT
from rehearse_titan_real_storage import CACHE_SHA,REFINED_SHA,MODELS

PREFIX="wawp:validation/titan-spatial-storage/run-45743437475-1"
INTENT="retirement-intent.json"
RECEIPT="retirement-receipt.json"
RETRYABLE={"rateLimitExceeded","userRateLimitExceeded","backendError"}
def reason(resp):
    try:
        values=resp.json().get("error",{}).get("errors",[])
        return [v.get("reason") for v in values if isinstance(v.get("reason"),str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}",v["reason"])]
    except (ValueError,AttributeError,TypeError):return []
def metadata(s,info,name,parent=None):
    data=s.t._get(info,{"supportsAllDrives":"true","fields":"id,name,size,trashed,parents,capabilities(canDownload,canTrash)"})
    require(data.get("id")==info["id"] and data.get("name")==name and int(data.get("size",-1))==info["size"],"Exact object identity differs")
    if parent is not None:require(data.get("parents")==[parent],"Object is outside fixture parent")
    require(isinstance(data.get("trashed"),bool),"Missing trash status")
    return data
def find_trashed(s,name,parent):
    # Parent is derived from an active, verified commit marker, not user input.
    require(re.fullmatch(r"[A-Za-z0-9_-]+",parent) and re.fullmatch(r"charts-[0-9a-f]{64}\.zip",name),"Unsafe lookup")
    params={"q":f"'{parent}' in parents and name = '{name}' and trashed = true","fields":"nextPageToken,files(id,name,size,trashed,parents)","supportsAllDrives":"true","includeItemsFromAllDrives":"true","pageSize":100}
    for delay in (0,20,60,120):
        if delay:time.sleep(delay)
        remote.pace()
        with requests.get("https://www.googleapis.com/drive/v3/files",params=params,headers=s.t._headers(),timeout=(20,60),allow_redirects=False) as resp:
            if resp.status_code==200:
                data=resp.json();rows=data["files"]
                require(not data.get("nextPageToken") and len(rows)==1,"No unique trashed chart within fixture parent")
                x=rows[0];require(x["name"]==name and x["parents"]==[parent] and x["trashed"] is True,"Trash lookup scope mismatch")
                return {"id":x["id"],"size":int(x["size"])}
            if resp.status_code==401:s.t.inventory()
            elif resp.status_code not in (403,429,500,502,503,504):break
    raise ValueError("Unable to confirm unique trashed chart")
def record(s,name,value):
    p=s.root/("record-"+name);p.write_bytes(dump(value))
    if name not in s.inv:
        s.refresh();remote.rclone_copy(str(p),s.prefix+"/"+name);s.refresh(added=name)
    back=s.root/("record-read-"+name)
    if back.exists():back.unlink()
    s.t.read(name,s.inv[name],back)
    require(back.read_bytes()==p.read_bytes(),"Immutable retirement record mismatch")
def read_record(s,name):
    p=s.root/("prior-"+name)
    if p.exists():p.unlink()
    s.t.read(name,s.inv[name],p);return load(p)
def main():
    report={"mode":"isolated_real_drive_frozen_weather_targeted_retirement","prefix":PREFIX,"passed":False,"stage":"inspect","checks":{},"patch_attempts":[],"numeric_reupload_bytes":0,"chart_reupload_bytes":0}
    try:
        with tempfile.TemporaryDirectory(prefix="titan-retirement-") as td,patch.object(remote,"PREFIX",PREFIX):
            s=Store(PREFIX,Path(td));chain=s.markers()
            require(len(chain)==3,"Exactly three committed generations required")
            refs=[m["generation"] for m,_ in chain]
            generations=[load(s.read(r)) for r in refs]
            old,previous,current=generations
            require([g["label"] for g in generations]==["original","refined","replay-same-frozen-fields"],"Unexpected generation labels")
            require(all(g["source_run"]=="45721468170" and g["cache_code_sha"]==CACHE_SHA and g["mode"]=="frozen_research_preview" and g["schema"]==1 for g in generations),"Unexpected generation provenance")
            require(previous["render_sha"]==REFINED_SHA and {k:v for k,v in previous.items() if k!="label"}=={k:v for k,v in current.items() if k!="label"},"Replay differs from refined generation")
            require(old["numeric"]==current["numeric"] and old["numeric_entries"]==current["numeric_entries"] and set(current["numeric"])==set(MODELS) and len(current["numeric_entries"])==81,"Numerical identity changed")
            r=old["chart_object"];check_ref(r);n=r["name"]
            require(n.startswith("charts-") and n!=current["chart_object"]["name"] and len(old["chart_members"])==1563,"Unsafe retirement candidate")
            require(set(k for k in s.inv if k.startswith("charts-")) <= {n,current["chart_object"]["name"]},"Unregistered chart package")
            marker_info=s.inv["commit-000001.json"]
            marker=metadata(s,marker_info,"commit-000001.json")
            require(marker["trashed"] is False and len(marker["parents"])==1,"Commit parent not established")
            parent=marker["parents"][0]
            initial=dict(s.inv)
            report["stage"]="verify_retained"
            s.verify_gen(refs[1],deep=True);s.verify_gen(refs[2],deep=True)
            oldmeta=old["metadata_object"];verify_pack(s.read(oldmeta),old["metadata_members"])
            report["checks"]["current_previous_remote_integrity"]=True
            report["checks"]["historical_metadata_verified"]=True
            # Identify the deliberately invalid, uncommitted generation and prove it cannot be selected.
            bad_refs=[]
            for name,info in s.inv.items():
                if not name.startswith("generation-") or name in {x["name"] for x in refs}:continue
                q={"name":name,"sha256":name[11:-5],"bytes":info["size"]};g=load(s.read(q))
                require(g["label"]=="deliberate-invalid" and g["chart_object"]["sha256"]=="0"*64,"Unexpected uncommitted generation")
                try:s.verify_gen(q,deep=True)
                except ValueError as e:require("Incomplete generation object inventory" in str(e),"Unexpected invalid generation failure")
                else:raise ValueError("Invalid generation became verifiable")
                bad_refs.append(q)
            require(len(bad_refs)==1,"Expected one rejected generation fixture")
            report["checks"]["invalid_generation_still_uncommitted"]=True
            report["stage"]="resolve_obsolete_identity"
            prior=read_record(s,INTENT) if INTENT in s.inv else None
            if n in s.inv:info=dict(s.inv[n])
            elif prior:info=prior["drive_object"]
            else:info=find_trashed(s,n,parent)
            require(info["size"]==r["bytes"],"Retirement candidate byte size differs")
            state=metadata(s,info,n,parent)
            require(state["trashed"]==(n not in s.inv),"Trash and active listing disagree")
            report["initial_candidate_trashed"]=state["trashed"]
            report["candidate_can_trash"]=state.get("capabilities",{}).get("canTrash")
            # If already trashed without a journal, exact-ID lookup plus content verification
            # establishes what happened; no mutation is repeated.
            if state["trashed"]:
                if not prior:
                    p=s.root/"already-trashed.zip";s.t._get(info,{"supportsAllDrives":"true","alt":"media"},p)
                    require(sha(p)==r["sha256"],"Trashed chart hash differs");verify_pack(p,old["chart_members"]);p.unlink()
            else:verify_pack(s.read(r),old["chart_members"])
            report["checks"]["obsolete_exact_identity_verified"]=True
            protected={k:v for k,v in s.inv.items() if k not in (n,INTENT,RECEIPT)}
            intent={"schema":1,"mode":"frozen_fixture_retirement","source_run":"45743437475","failed_retirement_run":"45745176812","commit_chain_sha256":[h for _,h in chain],"candidate":r,"drive_object":info,"parent":parent,"protected_inventory_sha256":hashlib.sha256(dump(protected)).hexdigest(),"retained_generation_refs":refs[1:],"proof":"both retained generations and historical metadata verified; obsolete chart absent from retained references"}
            if prior:require(prior==intent,"Existing intent differs from current verified state")
            s.refresh();record(s,INTENT,intent)
            report["checks"]["durable_retirement_intent_verified"]=True
            report["stage"]="journaled_trash"
            if not state["trashed"]:
                require(state.get("capabilities",{}).get("canTrash") is True,"Drive denies Trash capability for obsolete fixture")
                require(RECEIPT not in s.inv,"Receipt exists while chart remains active")
                # Each mutation is preceded by a fresh exact-state check. Only explicit
                # throttle rejection + independently confirmed active status can retry.
                for delay in (0,45,120):
                    if delay:time.sleep(delay)
                    s.refresh();st=metadata(s,info,n,parent)
                    require(st["trashed"] is False,"Unexpected pre-mutation Trash state")
                    remote.pace()
                    try:
                        with requests.patch(s.t._url(info),params={"supportsAllDrives":"true","fields":"id,name,size,trashed"},headers=s.t._headers(),json={"trashed":True},timeout=(20,60),allow_redirects=False) as resp:
                            status=resp.status_code;why=reason(resp) if status!=200 else []
                            report["patch_attempts"].append({"status":status,"reasons":why})
                            if status==200:
                                x=resp.json();require(x.get("id")==info["id"] and x.get("name")==n and int(x.get("size",-1))==info["size"] and x.get("trashed") is True,"Trash response identity mismatch")
                    except requests.RequestException as e:
                        report["patch_attempts"].append({"transport_error":type(e).__name__})
                        status=0;why=[]
                    st=metadata(s,info,n,parent)
                    if st["trashed"]:
                        s.refresh(removed=n);break
                    require(n in s.refresh(),"Candidate missing without confirmed Trash")
                    if not (status==429 or (status==403 and bool(set(why)&RETRYABLE))):raise ValueError("Trash not confirmed; inspect recorded status and reason")
                else:raise ValueError("Explicit rate limit persisted; journal retained")
            else:
                s.refresh();require(n not in s.inv,"Already trashed chart returned to active inventory")
            require(metadata(s,info,n,parent)["trashed"] is True,"Trash final state not confirmed")
            report["checks"]["obsolete_chart_trashed_exact_id"]=True
            report["stage"]="verify_after_retirement"
            require({k:v for k,v in s.inv.items() if k not in (INTENT,RECEIPT)}==protected,"Protected inventory changed")
            require(s.markers()==chain,"Publication chain changed")
            # Independent post-mutation media readback of the retained chart pack,
            # numeric packs and all historical metadata, not just inventory equality.
            all_objects={}
            for g in generations:
                all_objects[g["metadata_object"]["name"]]=(g["metadata_object"],g["metadata_members"])
                for x in g["numeric"].values():all_objects[x["object"]["name"]]=(x["object"],x["members"])
            all_objects[current["chart_object"]["name"]]=(current["chart_object"],current["chart_members"])
            for obj,expected in all_objects.values():verify_pack(s.read(obj,fresh=True),expected)
            s.refresh();require(s.markers()==chain,"Publication chain changed during readback")
            receipt={"schema":1,"intent_sha256":hashlib.sha256(dump(intent)).hexdigest(),"candidate":r,"status":"trashed_verified","protected_inventory_sha256":intent["protected_inventory_sha256"],"current_previous_verified":True}
            record(s,RECEIPT,receipt)
            require({k:v for k,v in s.refresh().items() if k not in (INTENT,RECEIPT)}==protected,"Final protected inventory changed")
            report["checks"]["post_retirement_remote_integrity"]=True
            report["checks"]["permanent_numeric_metadata_preserved"]=True
            report["checks"]["receipt_verified"]=True
            report.update({"passed":True,"stage":"complete","numeric_entries":81,"numeric_pack_bytes":sum(x["object"]["bytes"] for x in current["numeric"].values()),"chart_entries":1563,"commit_count":3,"active_chart_packs":sum(k.startswith("charts-") for k in s.inv),"retired_fixture_chart_pack_bytes":r["bytes"],"active_remote_bytes":sum(v["size"] for v in s.inv.values()),"inventory_calls":s.inventory_calls,"direct_http_requests":remote.HTTP_REQUESTS,"readback_bytes":s.read_bytes})
    except Exception as e:
        report["error"]=str(e)[:180] if type(e) is ValueError else type(e).__name__
    finally:
        Path("remote-spatial-retirement-report.json").write_bytes(dump(report));print("REMOTE_SPATIAL_RETIREMENT",json.dumps(report),flush=True)
    if not report["passed"]:raise SystemExit("Retirement not verified; preserve remaining objects")
if __name__=="__main__":main()
