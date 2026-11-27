"""Verify recovery of exact immutable model packs, without production writes.
Rehearsal uses an approved source run; remote mode reads a committed Drive
generation by exact IDs and never uploads or deletes data."""
import argparse,json,re,shutil,sys,tempfile,zipfile,os
from pathlib import Path
from titan_private_numeric_archive import api,gh,valid_source,source_artifacts,MODELS,read_model,need,sha,dump,load
from titan_numeric_drive import DriveArchive

HEX=re.compile(r"[0-9a-f]{64}")
FILE=re.compile(r"manifest\.json|f[0-9]{3}\.npz")
MAX=2*1024**3
def checked_pack(path,ref,model,dest):
    need(not dest.exists() and HEX.fullmatch(ref["sha256"]) and HEX.fullmatch(ref["manifest_sha256"]) and re.fullmatch(r"[0-9a-f]{40}",ref["acquisition_sha"]) is not None,"Invalid pack identity")
    need(path.name=="model-"+model+"-"+ref["manifest_sha256"]+".zip" and path.stat().st_size==ref["bytes"] and sha(path)==ref["sha256"],"ZIP identity differs")
    with tempfile.TemporaryDirectory(dir=dest.parent) as td:
        stage=Path(td)/"cache";stage.mkdir()
        with zipfile.ZipFile(path) as z:
            infos=z.infolist();names=[x.filename for x in infos]
            need(len(names)==len(set(names))==ref["entries"]+1 and "manifest.json" in names and all(FILE.fullmatch(n) for n in names),"Unexpected ZIP member")
            need(sum(x.file_size for x in infos)<=MAX and all(x.compress_type==zipfile.ZIP_STORED and x.file_size>0 and not x.is_dir() for x in infos),"Unexpected ZIP encoding or size")
            for x in infos:
                with z.open(x) as src,(stage/x.filename).open("xb") as target:shutil.copyfileobj(src,target,1024*1024)
        need(sha(stage/"manifest.json")==ref["manifest_sha256"],"Manifest digest differs")
        m,rows=read_model(stage,model,allowed={ref["acquisition_sha"]})
        need(m["cycle"]==ref["cycle"] and len(rows)==ref["entries"],"Restored cycle or lead count differs")
        stage.rename(dest)
    return len(rows)
def make_pack(f,model,s,w):
    m,rows=read_model(f,model,allowed={s["acquisition_sha"]})
    need(m["cycle"]==s["cycle"] and len(rows)==s["entries"] and sha(f/"manifest.json")==s["manifest_sha256"],"Source cache differs")
    p=w/("model-"+model+"-"+s["manifest_sha256"]+".zip")
    with zipfile.ZipFile(p,"x",compression=zipfile.ZIP_STORED,allowZip64=True) as z:
        for src in [f/"manifest.json",*[f/e["file"] for e in rows]]:
            info=zipfile.ZipInfo(src.name,(1980,1,1,0,0,0))
            with src.open("rb") as x,z.open(info,"w",force_zip64=True) as y:shutil.copyfileobj(x,y,1024*1024)
    return p,{**s,"name":p.name,"bytes":p.stat().st_size,"sha256":sha(p)}
def rehearse(run,w,cache):
    r=api(f"actions/runs/{run}");need(valid_source(r,run),"Unapproved source")
    a=source_artifacts(run)
    def download(n,d):
        need(n in a and not a[n]["expired"] and a[n]["size_in_bytes"]>0,"Source artifact unavailable")
        gh("run","download",run,"--name",n,"--dir",d)
    download("titan-rolling-baseline",w/"receipt");download("titan-auto-publication-report",w/"proof")
    receipt=load(w/"receipt/rolling-baseline.json");proof=load(w/"proof/automated-publication-report.json")
    need(receipt["run"]==run and receipt["revision"]==r["head_sha"] and receipt["advanced"] is True and set(receipt["sources"])==set(MODELS) and proof["passed"] is True and proof["advanced"] is True and not proof["archived_replay"] and proof["candidate_catalog_sha256"]==receipt["catalog_sha256"],"Unverified receipt")
    total=0;size=0;negative=False
    for model in MODELS:
        f=w/model;download("titan-full-cache-"+model,f)
        p,ref=make_pack(f,model,receipt["sources"][model],w)
        dest=cache/("titan-full-cache-"+model)
        total+=checked_pack(p,ref,model,dest);size+=ref["bytes"]
        need({x.name:sha(x) for x in f.glob("*.npz")}=={x.name:sha(x) for x in dest.glob("*.npz")},"Restored field bytes differ")
        if not negative:
            try:checked_pack(p,{**ref,"sha256":"0"*64},model,cache/"rejected")
            except ValueError:negative=True
            need(negative and not (cache/"rejected").exists(),"Invalid archive was accepted")
        print("TITAN_RESTORE_MODEL_PASS",json.dumps({"model":model,"leads":ref["entries"],"bytes":ref["bytes"]}),flush=True)
    need(total==81 and negative,"Incomplete restoration")
    return {"passed":True,"mode":"local_real_source_rehearsal","source_run":run,"catalog_sha256":receipt["catalog_sha256"],"models":5,"native_leads":total,"bytes":size,"tamper_rejected":negative,"remote_readback":False}
def remote(run,expected,w,cache):
    need(HEX.fullmatch(expected),"Expected catalog digest required")
    drive=DriveArchive(create_folder=False);a=drive.inventory();marker=f"generation-{run}.json"
    need(marker in a,"No committed generation")
    q=w/marker;drive.read(marker,a[marker],q);g=load(q)
    need(g["schema"]==1 and g["source_run"]==run and g["catalog_sha256"]==expected and g["native_leads"]==81 and set(g["models"])==set(MODELS),"Generation marker differs")
    total=0;size=0
    for model in MODELS:
        ref=g["models"][model];name=ref["name"]
        need(name=="model-"+model+"-"+ref["manifest_sha256"]+".zip" and a.get(name)=={"id":ref["id"],"size":ref["bytes"]},"Exact committed object missing")
        path=w/name;drive.read(name,a[name],path)
        total+=checked_pack(path,ref,model,cache/("titan-full-cache-"+model));size+=ref["bytes"]
        print("TITAN_RESTORE_MODEL_PASS",json.dumps({"model":model,"leads":ref["entries"],"bytes":ref["bytes"]}),flush=True)
    need(total==81,"Incomplete restoration")
    return {"passed":True,"mode":"private_drive_exact_id_restore","source_run":run,"catalog_sha256":expected,"models":5,"native_leads":total,"bytes":size,"remote_readback":True}
def main():
    p=argparse.ArgumentParser();p.add_argument("mode",choices=["rehearse","remote"]);p.add_argument("run",type=int);p.add_argument("--catalog-sha",default="");p.add_argument("--report",type=Path,default=Path("titan-restore-report.json"));a=p.parse_args()
    with tempfile.TemporaryDirectory() as td:
        w=Path(td);cache=w/"restored";cache.mkdir()
        report=rehearse(a.run,w,cache) if a.mode=="rehearse" else remote(a.run,a.catalog_sha,w,cache)
        a.report.write_bytes(dump(report));print("TITAN_NUMERIC_RESTORE_PASS",json.dumps(report),flush=True)
if __name__=="__main__":main()
