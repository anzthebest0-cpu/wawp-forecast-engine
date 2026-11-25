"""Read-only real checkpoint redundancy rehearsal; no uploads or deletes."""
import json,tempfile,subprocess
from pathlib import Path
from src import checkpoint_retention as retention
from src.storage_snapshot import restore,select_asset,file_digest
REPO="anzthebest0-cpu/wawp-forecast-engine"
RELEASE=351035767
def main():
    report={"mode":"read_only","candidates":[]}
    try:
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            pages=retention.release_assets(REPO,RELEASE)
            assets=retention.indexed(pages)
            latest=select_asset(pages)
            report["latest"]=latest
            files=retention.run("rclone","lsf",retention.PREFIX,"--files-only").splitlines()
            if len(files)!=len(set(files)):raise ValueError("Duplicate names")
            records={}
            for f in files:
                if not f.endswith(".registered.json"):continue
                n=f.removesuffix(".registered.json")
                if not retention.name_allowed(n) or n+".retired.json" in files:continue
                # Independent read-only retrieval through cat and copyto; byte equality.
                raw=subprocess.check_output(["rclone","cat",retention.PREFIX+"/"+f,"--retries","3","--low-level-retries","5"],timeout=120)
                if len(raw)>65536:raise ValueError("Registration too large")
                rec=retention.get_json(work,f)
                if json.loads(raw)!=rec:raise ValueError("Registration readback disagreement")
                if rec.get("version")!=1 or rec["data"]["name"]!=n or rec["manifest"]["name"]!=n+".json":raise ValueError("Invalid registration")
                for key in ("data","manifest"):
                    a=rec[key]
                    if a["name"] not in assets or retention.identity(assets[a["name"]])!=a:raise ValueError("Release identity mismatch")
                records[n]=rec
            candidates=retention.eligible(records,latest)
            report["registered"]=len(records)
            report["retained"]=[n for n in records if n not in candidates]
            with tempfile.TemporaryDirectory(dir=work) as ld:
                ld=Path(ld);a=retention.verify_pair(ld,latest,records[latest])
                latestdb=ld/"latest.db";restore(a,latestdb)
                for n in candidates:
                    try:
                        with tempfile.TemporaryDirectory(dir=work) as od:
                            od=Path(od);old=retention.verify_pair(od,n,records[n]);old_db=od/"old.db";restore(old,old_db)
                            proof=retention.append_superset(old_db,latestdb)
                            report["candidates"].append({"name":n,"status":"verified_redundant","proof":proof})
                    except Exception as e:
                        report["candidates"].append({"name":n,"status":"blocked","reason":type(e).__name__})
            current=retention.release_assets(REPO,RELEASE)
            if select_asset(current)!=latest or retention.indexed(current)!=assets:raise ValueError("Release changed during rehearsal")
            report["passed"]=all(x["status"]=="verified_redundant" for x in report["candidates"])
    except Exception as e:
        report["passed"]=False;report["error"]=type(e).__name__
    finally:
        Path("retention-rehearsal.json").write_text(json.dumps(report,indent=2))
        print("RETENTION_REHEARSAL",json.dumps({**report,"candidates":[{k:v for k,v in x.items() if k!="proof"} for x in report["candidates"]]}),flush=True)
    if not report["passed"]:raise SystemExit("Rehearsal did not establish safe redundancy")
if __name__=="__main__":main()
