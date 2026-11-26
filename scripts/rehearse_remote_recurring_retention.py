"""Real Drive fixture rehearsal; GitHub release operations are strictly simulated.
Only synthetic payloads in a fresh validation prefix may be uploaded or trashed."""
import json,os,re,sqlite3,subprocess,tempfile
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from src import checkpoint_retention as retention
from src import rolling_checkpoint_transport as transport
from src.storage_snapshot import file_digest,manifest_path,pack,restore

def need(ok,msg):
    if not ok:raise ValueError(msg)

def main():
    run=os.environ["GITHUB_RUN_ID"];attempt=os.environ["GITHUB_RUN_ATTEMPT"]
    need(re.fullmatch(r"\d+",run) and re.fullmatch(r"\d+",attempt),"Invalid run identity")
    prefix="wawp:validation/titan-recurring-retention/run-"+run+"-"+attempt
    need(prefix.startswith("wawp:validation/titan-recurring-retention/run-"),"Not a validation prefix")
    report={"mode":"real_drive_synthetic_fixtures_simulated_release","prefix":prefix,"passed":False,"stage":"setup","checks":{}}
    assets=[];deleted=[];allowed_ids=set();mode={"interrupt":None}
    def fake_release(*args):
        # Never forward any release operation to a shell or real GitHub.
        need(args[:2]==("gh","api"),"Unexpected command in release simulation")
        if "--method" in args:
            need(args[2:4]==("--method","DELETE") and args[-1].startswith("repos/fixture-only/releases/assets/"),"Unexpected simulated mutation")
            aid=int(args[-1].split("/")[-1]);need(aid in allowed_ids and aid not in deleted,"Unsafe simulated deletion")
            need(any(a["id"]==aid for a in assets),"Missing simulated asset")
            assets[:]=[a for a in assets if a["id"]!=aid];deleted.append(aid)
            if mode["interrupt"]=="after_manifest" and aid%2==0:
                mode["interrupt"]=None
                raise OSError("injected interruption after simulated manifest removal")
            return ""
        need(args[2:4]==("--paginate","repos/fixture-only/releases/1/assets?per_page=100"),"Unexpected simulated inventory")
        # Simulate counters changing between reads.
        return json.dumps([{**a,"download_count":len(deleted)+1} for a in assets])
    try:
        # Unique Actions run/attempt folder; reject an existing nonempty folder.
        subprocess.run(["rclone","mkdir",prefix],check=True,timeout=120)
        with patch.object(transport,"PREFIX",prefix),patch.object(retention,"run",fake_release):
            t=transport.RollingTransport();need(t.kind=="drive","Real Drive backend required")
            need(not t.inventory(),"Fixture prefix is not empty")
            with tempfile.TemporaryDirectory(prefix="titan-drive-fixtures-") as td:
                root=Path(td);db=root/"candidate.db";names=[]
                with closing(sqlite3.connect(db)) as conn:
                    conn.executescript("CREATE TABLE openmeteo_forecasts(id INTEGER PRIMARY KEY AUTOINCREMENT,value REAL); CREATE TABLE observations(id INTEGER PRIMARY KEY,data REAL); INSERT INTO observations VALUES(1,2);")
                    conn.commit()
                # Nonmanaged sentinel proves unrelated files survive.
                sentinel=root/"unrelated.db.zst";sentinel.write_bytes(b"synthetic retention sentinel")
                transport.rclone_copy(str(sentinel),prefix+"/"+sentinel.name)
                sentinel_identity=t.inventory()[sentinel.name]
                def add_generation(i):
                    with closing(sqlite3.connect(db)) as conn:
                        conn.execute("INSERT INTO openmeteo_forecasts(value) VALUES(?)",(float(i),));conn.commit()
                    name=f"wawp_forecasts-{run}{i}-1.db.zst";names.append(name)
                    p=root/name;pack(db,p)
                    w=root/("replicate-"+str(i));w.mkdir()
                    back=transport.replicate_rolling(p,w)
                    restore(back,w/"restored.db")
                    need(file_digest(w/"restored.db")==file_digest(db),"Real Drive restore differs")
                    pair=[]
                    for j,f in enumerate((p,manifest_path(p)),1):
                        aid=(i-1)*2+j;allowed_ids.add(aid)
                        a={"id":aid,"name":f.name,"size":f.stat().st_size,"digest":"sha256:"+file_digest(f),"state":"uploaded","created_at":f"2026-11-26T0{i}:00:00Z"}
                        assets.append(a);pair.append(a)
                    # Only the fixture release is represented in memory.
                    return p,{"version":1,"data":pair[0],"manifest":pair[1]}
                def retire(p,label):
                    path=root/(label+".json")
                    retention.retire("fixture-only",1,p,db,path)
                    return json.loads(path.read_text())
                def fail(p,label):
                    try:retire(p,label)
                    except ValueError:
                        return json.loads((root/(label+".json")).read_text())
                    raise AssertionError("Injected failure did not stop workflow")
                def verify_state(old,retained):
                    inv=t.inventory()
                    need(set(n for n in inv if retention.name_allowed(n))==set(retained),"Active fixture scope differs")
                    need(inv[sentinel.name]==sentinel_identity,"Unrelated fixture changed")
                    for n in old:
                        need(n+".retiring.json" in inv and n+".retired.json" in inv and n+".registered.json" in inv and n+".json" in inv,"Missing retirement evidence")
                        journal=t.read_json(n+".retiring.json",inv[n+".retiring.json"],root)
                        receipt=t.read_json(n+".retired.json",inv[n+".retired.json"],root)
                        need(receipt==retention.receipt(journal),"Receipt differs from committed journal")
                        t.metadata(n,journal["drive_data"],trashed=True)
                    for n in retained:
                        reg=t.read_json(n+".registered.json",inv[n+".registered.json"],root)
                        with tempfile.TemporaryDirectory(dir=root) as sub:
                            p=Path(sub);a=retention.verify_pair(p,n,reg,t,inv)
                            restore(a,p/"retained.db")
                    return inv
                report["stage"]="initial_three"
                for i in range(1,4):
                    p,rec=add_generation(i);t.put_json(p.name+".registered.json",rec,root)
                report["checks"]["initial_three_independent_drive_restores"]=True
                report["stage"]="interrupt_after_manifest"
                p,_=add_generation(4);mode["interrupt"]="after_manifest"
                failed=fail(p,"manifest-interruption")
                inv=t.inventory()
                need(deleted==[2] and names[0] in inv and names[0]+".retiring.json" in inv and names[0]+".retired.json" not in inv,"Unexpected state after manifest interruption")
                need(failed["skipped"] and failed["stage"]=="candidate:"+names[0],"Unexpected failure stage")
                recovered=retire(p,"manifest-recovery")
                need(recovered["retired"]==[names[0]] and deleted==[2,1],"Manifest interruption did not reconcile")
                verify_state(names[:1],names[1:4])
                report["checks"]["manifest_interruption_reconciled"]=True
                report["stage"]="interrupt_after_trash"
                p,_=add_generation(5)
                real=transport.RollingTransport.put_json
                def stop_receipt(self,name,value,work):
                    if name==names[1]+".retired.json":raise OSError("injected interruption before receipt")
                    return real(self,name,value,work)
                with patch.object(transport.RollingTransport,"put_json",stop_receipt):
                    failed=fail(p,"trash-interruption")
                inv=t.inventory()
                need(deleted==[2,1,4,3] and names[1] not in inv and names[1]+".retiring.json" in inv and names[1]+".retired.json" not in inv,"Unexpected state after trash interruption")
                journal=t.read_json(names[1]+".retiring.json",inv[names[1]+".retiring.json"],root)
                t.metadata(names[1],journal["drive_data"],trashed=True)
                recovered=retire(p,"trash-recovery")
                need(recovered["retired"]==[names[1]] and deleted==[2,1,4,3],"Trash interruption did not reconcile")
                verify_state(names[:2],names[2:5])
                report["checks"]["trash_interruption_reconciled"]=True
                report["stage"]="normal_retirement"
                p,_=add_generation(6)
                normal=retire(p,"normal-retirement")
                need(normal["retired"]==[names[2]] and deleted==[2,1,4,3,6,5],"Normal retirement differs")
                inv=verify_state(names[:3],names[3:6])
                report["checks"]["normal_retirement_passed"]=True
                report["stage"]="idempotence"
                again=retire(p,"repeat-retirement")
                need(again["retired"]==[] and t.inventory()==inv and deleted==[2,1,4,3,6,5],"Repeat altered completed retirement")
                report["checks"]["retry_idempotent"]=True
                report["checks"]["all_three_retained_restorable"]=True
                report["checks"]["unrelated_fixture_preserved"]=True
                report["real_drive_fixture_payloads_trashed"]=3
                report["simulated_release_asset_removals"]=len(deleted)
                report["active_managed"]=3
                report["active_fixture_bytes"]=sum(x["size"] for x in inv.values())
                report["stage"]="complete";report["passed"]=True
    except Exception as e:
        # Do not log raw provider exceptions, credentials, IDs or data.
        report["error"]=str(e)[:180] if type(e) is ValueError else type(e).__name__
        raise
    finally:
        Path("remote-retention-report.json").write_text(json.dumps(report,indent=2))
        print("REMOTE_RETENTION_REHEARSAL",json.dumps(report),flush=True)
if __name__=="__main__":main()
