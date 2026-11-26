"""Read-only two-backend recovery proof. No retirement, uploads, or live changes."""
import json,subprocess,tempfile
from pathlib import Path
from src import checkpoint_retention as r
from src.storage_snapshot import restore,select_asset,file_digest
REPO="anzthebest0-cpu/wawp-forecast-engine"
RELEASE=351035767
def need(ok,msg):
    if not ok:raise ValueError(msg)
def classification(p):
    s=p.stderr.decode(errors="replace").lower()
    if "downloadquotaexceeded" in s or "download quota" in s:return "download_quota"
    if "ratelimitexceeded" in s or "rate limit" in s or "429" in s:return "rate_limit"
    if "storagequotaexceeded" in s or "storage quota" in s:return "storage_quota"
    if "not found" in s or "404" in s:return "not_found"
    if "quota" in s:return "unspecified_quota"
    if "403" in s or "permission" in s:return "permission"
    return "transport_error"
def drive_read(name,p,methods=("cat","copyto")):
    results=[]
    for method in methods:
        if p.exists():p.unlink()
        common=["--retries","2","--low-level-retries","3","--timeout","2m"]
        try:
            if method=="cat":
                with p.open("xb") as f:
                    proc=subprocess.run(["rclone","cat",r.PREFIX+"/"+name,*common],stdout=f,stderr=subprocess.PIPE,timeout=240)
            else:
                proc=subprocess.run(["rclone","copyto","--immutable","--checksum",*common,r.PREFIX+"/"+name,str(p)],capture_output=True,timeout=240)
            results.append({"method":method,"result":"ok" if proc.returncode==0 else classification(proc)})
            if proc.returncode==0:return results
        except subprocess.TimeoutExpired:results.append({"method":method,"result":"timeout"})
    return results
def matches(p,meta):
    return p.is_file() and p.stat().st_size==meta["size"] and "sha256:"+file_digest(p)==meta["digest"]
def github_pair(folder,rec):
    folder.mkdir()
    for key in ("data","manifest"):
        m=rec[key]
        subprocess.run(["gh","release","download","latest-db","--repo",REPO,"--pattern",m["name"],"--dir",str(folder)],check=True,timeout=240)
        need(matches(folder/m["name"],m),"GitHub registered digest mismatch")
    archive=folder/rec["data"]["name"];db=folder/"restored.db";restore(archive,db);return db
def drive_pair(folder,rec,inventory):
    folder.mkdir();report={"files":{},"verified":False}
    for key in ("data","manifest"):
        m=rec[key];n=m["name"];p=folder/n
        item={"inventory_matches":inventory.get(n)==m["size"]}
        report["files"][key]=item
        if not item["inventory_matches"]:continue
        item["attempts"]=drive_read(n,p)
        item["verified"]=item["attempts"][-1]["result"]=="ok" and matches(p,m)
    if all(report["files"][k].get("verified") for k in ("data","manifest")):
        try:
            restore(folder/rec["data"]["name"],folder/"restored.db")
            report["verified"]=True
        except Exception as e:report["restore_error"]=type(e).__name__
    return report
def main():
    report={"mode":"read_only","github_redundancy_proven":False,"drive_retirement_preconditions_met":False,"stage":"inventory","candidates":[],"retained":[]}
    try:
        rows=json.loads(r.run("rclone","lsjson",r.PREFIX,"--files-only","--no-modtime","--no-mimetype"))
        names=[x["Name"] for x in rows];need(len(names)==len(set(names)),"Duplicate Drive names")
        inv={x["Name"]:x["Size"] for x in rows}
        pages=r.release_assets(REPO,RELEASE);assets=r.indexed(pages);latest=select_asset(pages);report["latest"]=latest
        with tempfile.TemporaryDirectory(prefix="checkpoint-proof-") as td:
            root=Path(td);records={}
            for n in sorted(x for x in inv if r.name_allowed(x)):
                report["stage"]="registration:"+n
                reg=n+".registered.json"
                need(reg in inv and n+".retired.json" not in inv,"Unregistered or retired active payload")
                p=root/reg;attempts=drive_read(reg,p)
                need(attempts[-1]["result"]=="ok","Registration unavailable")
                need(p.stat().st_size==inv[reg],"Registration size mismatch")
                rec=json.loads(p.read_bytes())
                need(rec["version"]==1 and rec["data"]["name"]==n and rec["manifest"]["name"]==n+".json","Invalid registration")
                for k in ("data","manifest"):
                    m=rec[k];need(m["name"] in assets and r.identity(assets[m["name"]])==m,"GitHub identity mismatch")
                records[n]=rec
            candidates=r.eligible(records,latest);retained=[n for n in records if n not in candidates]
            report["managed"]=len(records)
            report["stage"]="latest:"+latest
            latestdb=github_pair(root/"latest-github",records[latest])
            latestdrive=drive_pair(root/"latest-drive",records[latest],inv)
            report["retained"].append({"name":latest,"github_restored":True,"drive":latestdrive})
            if latestdrive["verified"]:
                need(file_digest(latestdb)==file_digest(root/"latest-drive"/"restored.db"),"Latest backend restoration differs")
            for n in retained:
                if n==latest:continue
                report["stage"]="retained:"+n
                with tempfile.TemporaryDirectory(dir=root) as sub:
                    p=Path(sub)
                    db=github_pair(p/"github",records[n])
                    drive=drive_pair(p/"drive",records[n],inv)
                    if drive["verified"]:need(file_digest(db)==file_digest(p/"drive"/"restored.db"),"Retained backend restoration differs")
                    report["retained"].append({"name":n,"github_restored":True,"drive":drive})
            for n in candidates:
                report["stage"]="candidate:"+n
                result={"name":n,"github_restored":False,"redundancy_proven":False}
                with tempfile.TemporaryDirectory(dir=root) as sub:
                    p=Path(sub)
                    try:
                        db=github_pair(p/"github",records[n]);result["github_restored"]=True
                        result["proof"]=r.append_superset(db,latestdb);result["redundancy_proven"]=True
                    except Exception as e:result["github_proof_error"]=str(e)[:160] if isinstance(e,ValueError) else type(e).__name__
                    result["drive"]=drive_pair(p/"drive",records[n],inv)
                    if result["github_restored"] and result["drive"]["verified"]:
                        need(file_digest(db)==file_digest(p/"drive"/"restored.db"),"Candidate backend restoration differs")
                report["candidates"].append(result)
            report["stage"]="recheck_inventory"
            current=r.release_assets(REPO,RELEASE)
            need(select_asset(current)==latest and r.indexed(current)==assets,"GitHub release changed during proof")
            rows2=json.loads(r.run("rclone","lsjson",r.PREFIX,"--files-only","--no-modtime","--no-mimetype"))
            need(len(rows2)==len(inv) and {x["Name"]:x["Size"] for x in rows2}==inv,"Drive inventory changed during proof")
            report["github_redundancy_proven"]=bool(candidates) and all(x["redundancy_proven"] for x in report["candidates"])
            report["drive_retirement_preconditions_met"]=report["github_redundancy_proven"] and all(x["drive"]["verified"] for x in report["retained"]+report["candidates"])
            report["stage"]="complete"
    except Exception as e:report["error"]=str(e)[:160] if isinstance(e,ValueError) else type(e).__name__
    finally:
        Path("checkpoint-recovery-proof.json").write_text(json.dumps(report,indent=2))
        print("CHECKPOINT_RECOVERY_PROOF",json.dumps({**report,"candidates":[{k:v for k,v in x.items() if k!="proof"} for x in report["candidates"]]}),flush=True)
    if not report["github_redundancy_proven"]:raise SystemExit("Independent redundancy proof incomplete; preserve all copies")
    if not report["drive_retirement_preconditions_met"]:raise SystemExit("GitHub proof passed but Drive remains unverified; preserve all copies")
if __name__=="__main__":main()
