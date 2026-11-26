"""Immutable public image-only generations. Commit receipt last.
All mutations limited to the dedicated TITAN rolling image release."""
import hashlib,json,os,re,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path
from datetime import datetime,timezone
from titan_rolling_publication import obtain,verify,stage,freshness,need,load,digest,utc,api
TAG="titan-rolling-images-v1"
PAT=re.compile(r"titan-panels-([0-9]+)\.(zip|json)")
def gh(*a):return subprocess.check_output(["gh",*map(str,a)],text=True)
def release(required=False):
    r=subprocess.run(["gh","api","repos/"+os.environ["GITHUB_REPOSITORY"]+"/releases/tags/"+TAG],capture_output=True,text=True)
    if r.returncode:
        if not required and "HTTP 404" in r.stderr:return None
        raise ValueError("Cannot resolve rolling image release: "+r.stderr[:250])
    x=json.loads(r.stdout);need(x["tag_name"]==TAG and not x["draft"],"Unexpected image release")
    return x
def assets(rel):
    if rel is None:return {}
    rows=json.loads(gh("api","--paginate","--slurp","repos/"+os.environ["GITHUB_REPOSITORY"]+"/releases/"+str(rel["id"])+"/assets?per_page=100"))
    items=[x for page in rows for x in page];a={x["name"]:x for x in items}
    need(len(a)==len(items),"Duplicate release names")
    return a
def fetch(name,root):
    root.mkdir(parents=True,exist_ok=True)
    p=root/name
    if not p.exists():gh("release","download",TAG,"--pattern",name,"--dir",root)
    need(p.is_file() and not p.is_symlink(),"Missing release file")
    return p
def markers(a):
    return sorted((int(m.group(1)) for n in a if (m:=PAT.fullmatch(n)) and m.group(2)=="json"),reverse=True)
def names(run):return f"titan-panels-{run}.zip",f"titan-panels-{run}.json"
def marker(run,a,root,require_payload=True):
    z,n=names(run);p=fetch(n,root);m=load(p);r=m["receipt"]
    need(m["schema"]==1 and m["source_run"]==run==r["run"] and r["advanced"] is True and m["zip_name"]==z and m["zip_bytes"]>0 and re.fullmatch(r"[0-9a-f]{64}",m["zip_sha256"]),"Invalid image receipt")
    need(p.stat().st_size==a[n]["size"],"Marker size differs")
    if require_payload:
        need(z in a and a[z]["id"]==m["asset_id"] and a[z]["size"]==m["zip_bytes"] and a[z]["state"]=="uploaded","Missing or changed image payload")
    return m
def extract(z,root):
    root.mkdir()
    with zipfile.ZipFile(z) as f:
        info=f.infolist();need(0<len(info)<=5001 and len({x.filename for x in info})==len(info) and sum(x.file_size for x in info)<350_000_000,"Unsafe image ZIP inventory")
        for x in info:
            n=x.filename;need(n=="catalog.json" or re.fullmatch(r"panels/[0-9a-f]{64}\.webp",n),"Unsafe image ZIP member")
            p=root/n;p.parent.mkdir(parents=True,exist_ok=True)
            with f.open(x) as src,p.open("xb") as dst:shutil.copyfileobj(src,dst,1024*1024)
def restore(run,a,work,dest=None):
    m=marker(run,a,work);z=fetch(m["zip_name"],work)
    need(z.stat().st_size==m["zip_bytes"] and digest(z)==m["zip_sha256"],"Image archive digest mismatch")
    root=work/("unpacked-"+str(run));extract(z,root);verify(root,m["receipt"])
    if dest is not None:stage(root,m["receipt"],dest)
    return m
def prepare(run,site,work):
    rel=release();a=assets(rel);old=markers(a)
    if old and run<=old[0]:
        print("TITAN_PUBLIC_NO_ADVANCE",run,flush=True);return False
    rows=api(f"actions/runs/{run}/artifacts?per_page=100")
    if not any(x["name"]=="titan-rolling-baseline" for x in rows["artifacts"]):
        print("TITAN_PUBLIC_NO_GENERATION",run,flush=True);return False
    w=work/"candidate";w.mkdir();r,receipt,proof=obtain(run,w)
    f=freshness(receipt["sources"],datetime.now(timezone.utc))
    if not f["pass"]:
        print("TITAN_PUBLIC_STALE_CANDIDATE",json.dumps(f),flush=True);return False
    if old:
        previous=marker(old[0],a,work/"previous")
        need(all(utc(receipt["sources"][m]["cycle"])>=utc(s["cycle"]) for m,s in previous["receipt"]["sources"].items()),"Public model cycle regression")
        if not any(utc(receipt["sources"][m]["cycle"])>utc(s["cycle"]) for m,s in previous["receipt"]["sources"].items()):
            print("TITAN_PUBLIC_IDENTICAL_CYCLES",flush=True);return False
    stage(w/"site",receipt,site/"titan")
    (work/"pending.json").write_text(json.dumps(receipt,sort_keys=True))
    print("TITAN_PUBLIC_CANDIDATE_PREPARED",json.dumps({"run":run,"panels":receipt["panels"],"freshness":f}),flush=True);return True
def commit(site,work):
    pending=work/"pending.json"
    if not pending.is_file():return
    r=load(pending);run=r["run"];z,n=names(run)
    need(freshness(r["sources"],datetime.now(timezone.utc))["pass"],"Candidate aged out before commit")
    source=site/"titan";_,files=verify(source,r)
    p=work/z
    with zipfile.ZipFile(p,"x",compression=zipfile.ZIP_STORED,allowZip64=True) as f:
        for name,v in sorted(files.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0))
            with v.open("rb") as src,f.open(info,"w",force_zip64=True) as dst:shutil.copyfileobj(src,dst,1024*1024)
    rel=release()
    if rel is None:
        gh("release","create",TAG,"--target",os.environ["GITHUB_SHA"],"--title","TITAN rolling research images","--notes","Immutable validated image generations; current and previous retained. No numerical archives.","--latest=false")
        rel=release(True)
    a=assets(rel);old=markers(a)
    need(not old or run>old[0],"Public generation advanced concurrently")
    if z not in a:
        gh("release","upload",TAG,p);a=assets(rel)
    need(z in a and a[z]["size"]==p.stat().st_size,"Uploaded size differs")
    rb=fetch(z,work/"readback");need(digest(rb)==digest(p),"Remote archive readback differs")
    root=work/"readback/extracted";extract(rb,root);verify(root,r)
    m={"schema":1,"source_run":run,"receipt":r,"zip_name":z,"zip_bytes":p.stat().st_size,"zip_sha256":digest(p),"asset_id":a[z]["id"]}
    q=work/n;q.write_text(json.dumps(m,sort_keys=True,separators=(",",":")))
    need(n not in a,"Marker already present")
    gh("release","upload",TAG,q);a=assets(rel)
    need(load(fetch(n,work/"readback"))==m and marker(run,a,work/"readback")==m,"Committed marker readback differs")
    print("TITAN_ROLLING_IMAGE_COMMIT_PASS",json.dumps({"run":run,"bytes":m["zip_bytes"],"sha256":m["zip_sha256"],"asset_id":m["asset_id"]}),flush=True)
def attach(site):
    a=assets(release());old=markers(a)
    if not old:return False
    with tempfile.TemporaryDirectory() as td:restore(old[0],a,Path(td),site/"titan")
    print("TITAN_ROLLING_IMAGE_ATTACH_PASS",old[0],flush=True);return True
def retire():
    rel=release();a=assets(rel);runs=markers(a)
    if len(runs)<=2:return
    with tempfile.TemporaryDirectory() as td:
        w=Path(td)
        # Verify both retained archives completely before deleting any old image.
        for run in runs[:2]:restore(run,a,w/str(run))
        keep={n:a[n]["id"] for run in runs[:2] for n in names(run)}
        for run in runs[2:]:
            z,n=names(run)
            if z not in a:continue
            m=marker(run,a,w/str(run));now=assets(rel)
            need(all(k in now and now[k]["id"]==v for k,v in keep.items()) and now[z]["id"]==m["asset_id"],"Asset identity changed")
            gh("api","--method","DELETE","repos/"+os.environ["GITHUB_REPOSITORY"]+"/releases/assets/"+str(m["asset_id"]))
            after=assets(rel);need(z not in after and all(k in after and after[k]["id"]==v for k,v in keep.items()) and n in after,"Retirement readback failed")
            print("TITAN_ROLLING_IMAGE_RETIRED",json.dumps({"run":run,"bytes":m["zip_bytes"]}),flush=True)
def main():
    cmd=sys.argv[1]
    if cmd=="attach":raise SystemExit(0 if attach(Path(sys.argv[2])) else 3)
    if cmd=="prepare":
        site=Path(sys.argv[2]);work=Path(sys.argv[3]);work.mkdir(parents=True,exist_ok=True)
        raise SystemExit(0 if prepare(int(sys.argv[4]),site,work) else 3)
    if cmd=="commit":commit(Path(sys.argv[2]),Path(sys.argv[3]))
    elif cmd=="retire":retire()
    else:raise ValueError("Unknown command")
if __name__=="__main__":main()
