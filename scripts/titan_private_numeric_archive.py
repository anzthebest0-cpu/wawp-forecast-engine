"""Private permanent model-cycle numerical archive. No image or database writes.
Content-addressed ZIPs and manifest-last generation records; exact-ID readback.
Retry safely from immutable objects; no delete or overwrite operations."""
import hashlib,json,os,re,subprocess,sys,tempfile,time,zipfile,shutil
from pathlib import Path
import requests
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"titan-engine"))
from pilot_multicycle_storage import read_model,need,sha,dump,load
from pilot_cycle_discovery import MODELS
PREFIX="wawp:spatial-numerical-v1"
SEEDS={45902498571:"f0acc90bf3b1df25f7ccb6c18c2a8148e11e5fb3",45822462754:"63802dd909993944be4a29304bb56f24d3c99e26"}
def gh(*args):return subprocess.check_output(["gh",*map(str,args)],text=True)
def api(path):return json.loads(gh("api","repos/"+os.environ["GITHUB_REPOSITORY"]+"/"+path))
from titan_numeric_drive import DriveArchive,NAME
def put(path,a,work,drive):
    name=path.name;need(NAME.fullmatch(name),"Unsafe archive object")
    existing=name in a
    if not existing:a[name]=drive.create(name,path)
    need(a[name]["size"]==path.stat().st_size,"Private archive object size differs")
    back=work/("readback-"+name);drive.read(name,a[name],back,verified_metadata=not existing)
    need(sha(back)==sha(path),"Private archive hash mismatch")
    print("TITAN_PRIVATE_OBJECT_VERIFIED",json.dumps({"name":name,"bytes":path.stat().st_size,"reused":existing}),flush=True)
    return {"name":name,"id":a[name]["id"],"bytes":path.stat().st_size,"sha256":sha(path)}
def valid_source(r,run):
    return r["id"]==run and r["status"]=="completed" and r["conclusion"]=="success" and r["name"]=="TITAN rolling spatial research refresh" and ((run in SEEDS and r["head_sha"]==SEEDS[run] and r["head_branch"]=="codex/titan-rolling-refresh") or (r["head_branch"]=="main" and r.get("path","").split("@")[0]==".github/workflows/titan_rolling_refresh.yml"))
def source_artifacts(run):
    listing=api(f"actions/runs/{run}/artifacts?per_page=100")
    need(listing["total_count"]==len(listing["artifacts"]),"Artifact list truncated")
    arts={x["name"]:x for x in listing["artifacts"]}
    need(len(arts)==len(listing["artifacts"]),"Duplicate source artifacts")
    return arts
def archive(run,drive,a):
    r=api(f"actions/runs/{run}");need(valid_source(r,run),"Unapproved source run")
    arts=source_artifacts(run)
    if "titan-rolling-baseline" not in arts:
        print("TITAN_NUMERIC_NO_ADVANCE",run,flush=True);return False
    with tempfile.TemporaryDirectory() as td:
        w=Path(td)
        def download(n,d):
            need(n in arts and not arts[n]["expired"] and arts[n]["size_in_bytes"]>0,"Missing source artifact")
            gh("run","download",run,"--name",n,"--dir",d)
        download("titan-rolling-baseline",w/"receipt");download("titan-auto-publication-report",w/"proof")
        receipt=load(w/"receipt/rolling-baseline.json");proof=load(w/"proof/automated-publication-report.json")
        need(receipt["run"]==run and receipt["revision"]==r["head_sha"] and receipt["advanced"] is True and set(receipt["sources"])==set(MODELS) and proof["passed"] and proof["advanced"] and not proof["archived_replay"] and proof["candidate_catalog_sha256"]==receipt["catalog_sha256"],"Source receipt differs")
        marker=f"generation-{run}.json"
        if marker in a:
            # Marker-last prior commit must still reference the exact live
            # payload IDs and size. Never recreate over a prior marker.
            q=w/marker;drive.read(marker,a[marker],q);g=load(q)
            need(g["schema"]==1 and g["source_run"]==run and g["source_revision"]==r["head_sha"] and g["catalog_sha256"]==receipt["catalog_sha256"] and g["native_leads"]==81 and set(g["models"])==set(MODELS),"Existing archive marker differs")
            for model,ref in g["models"].items():
                s=receipt["sources"][model]
                need(all(ref[k]==v for k,v in s.items()) and ref["name"]=="model-"+model+"-"+s["manifest_sha256"]+".zip" and a.get(ref["name"])=={"id":ref["id"],"size":ref["bytes"]},"Committed archive reference differs")
            print("TITAN_PRIVATE_ALREADY_COMMITTED",run,flush=True);return True
        refs={}
        expected={m:"model-"+m+"-"+receipt["sources"][m]["manifest_sha256"]+".zip" for m in MODELS}
        missing=sum(n not in a for n in expected.values())+1
        drive.reserve(missing)
        print("TITAN_PRIVATE_RECOVERY_PLAN",json.dumps({"run":run,"reuse":sum(n in a for n in expected.values()),"missing_models":sum(n not in a for n in expected.values())}),flush=True)
        # Prioritize actual missing payloads: a provider rate limit late in
        # the run should not repeatedly starve the last missing model.
        # Existing packs still require independent full byte hash verification
        # before the generation marker is written.
        order=sorted(MODELS,key=lambda model:expected[model] in a)
        print("TITAN_PRIVATE_RECOVERY_ORDER",json.dumps(order),flush=True)
        for model in order:
            f=w/model;download("titan-full-cache-"+model,f);s=receipt["sources"][model]
            m,rows=read_model(f,model,allowed={s["acquisition_sha"]})
            need(m["cycle"]==s["cycle"] and len(rows)==s["entries"] and sha(f/"manifest.json")==s["manifest_sha256"],"Numerical source identity differs")
            p=w/expected[model]
            with zipfile.ZipFile(p,"x",compression=zipfile.ZIP_STORED,allowZip64=True) as z:
                for src in [f/"manifest.json",*[f/e["file"] for e in rows]]:
                    info=zipfile.ZipInfo(src.name,(1980,1,1,0,0,0))
                    with src.open("rb") as x,z.open(info,"w",force_zip64=True) as y:shutil.copyfileobj(x,y,1024*1024)
            ref=put(p,a,w,drive);refs[model]={**s,**ref};print("TITAN_PRIVATE_MODEL_PACK_PASS",json.dumps({"model":model,"cycle":s["cycle"],"bytes":ref["bytes"]}),flush=True)
        generation={"schema":1,"source_run":run,"source_revision":r["head_sha"],"catalog_sha256":receipt["catalog_sha256"],"models":refs,"native_leads":sum(s["entries"] for s in refs.values())}
        need(generation["native_leads"]==81,"Incomplete model archive")
        p=w/marker;p.write_bytes(dump(generation));put(p,a,w,drive)
        Path("titan-numeric-archive-report.json").write_bytes(dump({"passed":True,"source_run":run,"private_prefix":PREFIX,"models":5,"native_leads":81,"archive_bytes":sum(x["bytes"] for x in refs.values()),"deletions":0,"folder_inventory_calls":drive.inventory_calls,"drive_api_calls":drive.calls}))
        print("TITAN_PRIVATE_NUMERIC_ARCHIVE_PASS",run,flush=True)
        return True
def queue(a):
    # Auto recover any uncommitted advancing run still backed by Actions
    # artifacts, even after a no-change cycle. Prioritize newest so ongoing
    # operations stay protected; process one generation per invocation.
    runs=api("actions/runs?branch=main&status=success&per_page=50")["workflow_runs"]
    candidates=[r["id"] for r in runs if valid_source(r,r["id"])]
    candidates.extend(SEEDS)
    for run in sorted(set(candidates),reverse=True):
        if f"generation-{run}.json" in a:continue
        arts=source_artifacts(run)
        if "titan-rolling-baseline" not in arts:continue
        if arts["titan-rolling-baseline"]["expired"]:
            raise ValueError("Uncommitted archive source expired: "+str(run))
        return run
    return None
def main(arg):
    drive=DriveArchive();a=drive.inventory()
    if arg=="auto":
        run=queue(a)
        if run is None:
            print("TITAN_PRIVATE_QUEUE_CLEAR",flush=True);return
    else:run=int(arg)
    archive(run,drive,a)
if __name__=="__main__":main(sys.argv[1])
