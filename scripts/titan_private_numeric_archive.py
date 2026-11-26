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
def cli(*args):
    for delay in (0,30,90):
        if delay:time.sleep(delay)
        p=subprocess.run(["rclone",*map(str,args),"--tpslimit","0.25","--tpslimit-burst","1","--retries","1","--low-level-retries","2"],capture_output=True,text=True,timeout=600)
        if p.returncode==0:return p.stdout
        s=p.stderr.lower()
        if not any(x in s for x in ("ratelimitexceeded","rate limit","requests per minute","429","quota metric")):raise ValueError("Private archive CLI failed; no mutation retry without throttle evidence")
    raise ValueError("Private archive rate limit exhausted")
def inventory():
    rows=json.loads(cli("lsjson",PREFIX,"--files-only","--no-modtime","--no-mimetype"))
    a={}
    for x in rows:
        n=x["Name"];need(n not in a and re.fullmatch(r"[A-Za-z0-9_-]+",x.get("ID","")),"Ambiguous private archive identity")
        a[n]={"id":x["ID"],"size":x["Size"]}
    return a
def headers():
    # Never print OAuth configuration or credentials.
    c=json.loads(subprocess.check_output(["rclone","config","dump"],stderr=subprocess.DEVNULL,text=True))["wawp"];need(c["type"]=="drive","Private Drive backend required")
    t=c["token"];t=json.loads(t) if isinstance(t,str) else t
    return {"Authorization":"Bearer "+t["access_token"],"Accept-Encoding":"identity"}
def get(info,params,dest=None):
    for d in (0,20,60,120):
        if d:time.sleep(d)
        time.sleep(3)
        try:
            if dest and dest.exists():dest.unlink()
            with requests.get("https://www.googleapis.com/drive/v3/files/"+info["id"],headers=headers(),params={"supportsAllDrives":"true",**params},stream=dest is not None,timeout=(20,90),allow_redirects=False) as r:
                if r.status_code==200:
                    if dest is None:return r.json()
                    count=0
                    with dest.open("xb") as f:
                        for chunk in r.iter_content(1024*1024):
                            count+=len(chunk);need(count<=info["size"],"Remote size overflow");f.write(chunk)
                    need(count==info["size"],"Remote size differs");return
                if r.status_code==401:inventory()
                elif r.status_code not in (403,404,408,429,500,502,503,504):raise ValueError("Unexpected Drive read status "+str(r.status_code))
        except requests.RequestException:pass
    raise ValueError("Exact Drive readback exhausted")
def read(name,info,dest):
    m=get(info,{"fields":"id,name,size,trashed,capabilities(canDownload)"})
    need(m["id"]==info["id"] and m["name"]==name and int(m["size"])==info["size"] and m["trashed"] is False and m.get("capabilities",{}).get("canDownload") is True,"Drive object identity differs")
    get(info,{"alt":"media"},dest)
def put(path,a,work):
    name=path.name
    if name not in a:
        cli("copyto","--immutable","--checksum",path,PREFIX+"/"+name)
        b=inventory();need(all(b.get(k)==v for k,v in a.items()) and name in b,"Private archive inventory changed");a.clear();a.update(b)
    need(a[name]["size"]==path.stat().st_size,"Private archive object size differs")
    back=work/("readback-"+name);read(name,a[name],back)
    need(sha(back)==sha(path),"Private archive hash mismatch")
    return {"name":name,"id":a[name]["id"],"bytes":path.stat().st_size,"sha256":sha(path)}
def archive(run):
    r=api(f"actions/runs/{run}")
    need(r["id"]==run and r["status"]=="completed" and r["conclusion"]=="success" and r["name"]=="TITAN rolling spatial research refresh" and ((run in SEEDS and r["head_sha"]==SEEDS[run] and r["head_branch"]=="codex/titan-rolling-refresh") or (r["head_branch"]=="main" and r.get("path","").split("@")[0]==".github/workflows/titan_rolling_refresh.yml")),"Unapproved source run")
    listing=api(f"actions/runs/{run}/artifacts?per_page=100")
    need(listing["total_count"]==len(listing["artifacts"]),"Artifact list truncated")
    arts={x["name"]:x for x in listing["artifacts"]}
    if "titan-rolling-baseline" not in arts:
        print("TITAN_NUMERIC_NO_ADVANCE",run,flush=True);return
    with tempfile.TemporaryDirectory() as td:
        w=Path(td)
        def download(n,d):
            need(n in arts and not arts[n]["expired"] and arts[n]["size_in_bytes"]>0,"Missing source artifact")
            gh("run","download",run,"--name",n,"--dir",d)
        download("titan-rolling-baseline",w/"receipt");download("titan-auto-publication-report",w/"proof")
        receipt=load(w/"receipt/rolling-baseline.json");proof=load(w/"proof/automated-publication-report.json")
        need(receipt["run"]==run and receipt["revision"]==r["head_sha"] and receipt["advanced"] is True and set(receipt["sources"])==set(MODELS) and proof["passed"] and proof["advanced"] and not proof["archived_replay"] and proof["candidate_catalog_sha256"]==receipt["catalog_sha256"],"Source receipt differs")
        cli("mkdir",PREFIX);a=inventory();refs={}
        for model in MODELS:
            f=w/model;download("titan-full-cache-"+model,f);s=receipt["sources"][model]
            m,rows=read_model(f,model,allowed={s["acquisition_sha"]})
            need(m["cycle"]==s["cycle"] and len(rows)==s["entries"] and sha(f/"manifest.json")==s["manifest_sha256"],"Numerical source identity differs")
            p=w/("model-"+model+"-"+s["manifest_sha256"]+".zip")
            with zipfile.ZipFile(p,"x",compression=zipfile.ZIP_STORED,allowZip64=True) as z:
                for src in [f/"manifest.json",*[f/e["file"] for e in rows]]:
                    info=zipfile.ZipInfo(src.name,(1980,1,1,0,0,0))
                    with src.open("rb") as x,z.open(info,"w",force_zip64=True) as y:shutil.copyfileobj(x,y,1024*1024)
            ref=put(p,a,w);refs[model]={**s,**ref};print("TITAN_PRIVATE_MODEL_PACK_PASS",json.dumps({"model":model,"cycle":s["cycle"],"bytes":ref["bytes"]}),flush=True)
        generation={"schema":1,"source_run":run,"source_revision":r["head_sha"],"catalog_sha256":receipt["catalog_sha256"],"models":refs,"native_leads":sum(s["entries"] for s in refs.values())}
        need(generation["native_leads"]==81,"Incomplete model archive")
        p=w/f"generation-{run}.json";p.write_bytes(dump(generation));put(p,a,w)
        Path("titan-numeric-archive-report.json").write_bytes(dump({"passed":True,"source_run":run,"private_prefix":PREFIX,"models":5,"native_leads":81,"archive_bytes":sum(x["bytes"] for x in refs.values()),"deletions":0}))
        print("TITAN_PRIVATE_NUMERIC_ARCHIVE_PASS",run,flush=True)
if __name__=="__main__":archive(int(sys.argv[1]))
