"""Read-only proof for managed checkpoint retention; report stage and failure without exposing credentials."""
import json,subprocess,tempfile
from pathlib import Path
from src import checkpoint_retention as r
from src.storage_snapshot import restore,select_asset,file_digest

REPO="anzthebest0-cpu/wawp-forecast-engine"
RELEASE=351035767
def need(ok,msg):
    if not ok:raise ValueError(msg)
def read_file(remote,dest):
    # Stream bytes through rclone cat to a newly created local file; no remote writes.
    with dest.open("xb") as f:
        p=subprocess.run(["rclone","cat",remote,"--retries","2","--low-level-retries","3","--timeout","2m"],stdout=f,stderr=subprocess.PIPE,timeout=240)
    if p.returncode:
        err=p.stderr.decode(errors="replace").lower()
        reason="not_found" if "not found" in err else "quota" if "quota" in err else "permission" if "403" in err else "transport_error"
        raise ValueError("Drive readback "+reason)
def pair(folder,name,rec,inventory):
    for field in ("data","manifest"):
        meta=rec[field];n=meta["name"];p=folder/n
        need(n in inventory and inventory[n]==meta["size"],"Drive inventory size mismatch")
        read_file(r.PREFIX+"/"+n,p)
        need(p.stat().st_size==meta["size"] and "sha256:"+file_digest(p)==meta["digest"],"Drive/GitHub registered digest mismatch")
    return folder/name
def main():
    report={"mode":"read_only","passed":False,"stage":"inventory","candidates":[]}
    try:
        quota=json.loads(r.run("rclone","about","wawp:","--json"))
        report["capacity"]={k:quota[k] for k in ("total","used","free","trashed","other") if k in quota}
        rows=json.loads(r.run("rclone","lsjson",r.PREFIX,"--files-only","--no-modtime","--no-mimetype"))
        names=[x["Name"] for x in rows]
        need(len(names)==len(set(names)),"Duplicate Drive names")
        inventory={x["Name"]:x["Size"] for x in rows}
        pages=r.release_assets(REPO,RELEASE);assets=r.indexed(pages);latest=select_asset(pages)
        report["latest"]=latest
        payloads=sorted(n for n in inventory if r.name_allowed(n))
        report["managed_payloads"]=len(payloads);records={}
        with tempfile.TemporaryDirectory(prefix="titan-checkpoint-readonly-") as td:
            root=Path(td)
            for n in payloads:
                report["stage"]="registration:"+n
                need(n+".registered.json" in inventory and n+".retired.json" not in inventory,"Unregistered or retired active payload")
                p=root/(n+".registered.json");read_file(r.PREFIX+"/"+p.name,p);rec=json.loads(p.read_bytes())
                need(rec["version"]==1 and rec["data"]["name"]==n and rec["manifest"]["name"]==n+".json","Invalid registration")
                for key in ("data","manifest"):
                    m=rec[key];need(m["name"] in assets and r.identity(assets[m["name"]])==m,"GitHub identity mismatch")
                records[n]=rec
            report["registered"]=len(records)
            candidates=r.eligible(records,latest);report["retain"]=[n for n in records if n not in candidates]
            report["proposed_retirements"]=candidates
            report["stage"]="latest_pair:"+latest
            latestdir=root/"latest";latestdir.mkdir()
            latestarchive=pair(latestdir,latest,records[latest],inventory)
            latestdb=latestdir/"restored.db";restore(latestarchive,latestdb)
            for n in candidates:
                report["stage"]="candidate:"+n
                result={"name":n,"status":"blocked"}
                try:
                    with tempfile.TemporaryDirectory(dir=root) as od:
                        od=Path(od);a=pair(od,n,records[n],inventory);db=od/"restored.db";restore(a,db)
                        proof=r.append_superset(db,latestdb)
                        result.update(status="verified_redundant",proof=proof)
                except Exception as e:
                    result["reason"]=str(e)[:180] if isinstance(e,ValueError) else type(e).__name__
                report["candidates"].append(result)
            report["stage"]="recheck_inventory"
            current=r.release_assets(REPO,RELEASE)
            need(select_asset(current)==latest and r.indexed(current)==assets,"GitHub release changed during proof")
            rows2=json.loads(r.run("rclone","lsjson",r.PREFIX,"--files-only","--no-modtime","--no-mimetype"))
            need(len(rows2)==len(inventory) and {x["Name"]:x["Size"] for x in rows2}==inventory,"Drive inventory changed during proof")
            report["passed"]=all(x["status"]=="verified_redundant" for x in report["candidates"])
            report["stage"]="complete"
    except Exception as e:
        report["error"]=str(e)[:180] if isinstance(e,ValueError) else type(e).__name__
    finally:
        Path("checkpoint-readonly-report.json").write_text(json.dumps(report,indent=2))
        print("CHECKPOINT_READONLY",json.dumps({**report,"candidates":[{k:v for k,v in x.items() if k!="proof"} for x in report["candidates"]]}),flush=True)
    if not report["passed"]:raise SystemExit("Read-only proof incomplete; keep every checkpoint")
if __name__=="__main__":main()
