"""Read-only verified rolling image publication preparation.
No production writes. Rehearsal accepts historical snapshots explicitly;
real publication must pass current freshness, not fixture time."""
import hashlib,json,os,re,shutil,subprocess,tempfile,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
MODELS={"gfs","ifs-025","ifs-010","aifs-025","icon"}
BRANCH="codex/titan-rolling-refresh"
HEX=re.compile(r"[0-9a-f]{64}")
def need(ok,msg):
    if not ok:raise ValueError(msg)
def load(p):return json.loads(p.read_bytes())
def digest(p):
    with p.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def utc(s):
    d=datetime.fromisoformat(s.replace("Z","+00:00"));need(d.utcoffset()==timedelta(0),"Non UTC");return d
def api(path):
    return json.loads(subprocess.check_output(["gh","api","repos/"+os.environ["GITHUB_REPOSITORY"]+"/"+path],text=True))
def freshness(sources,now):
    ages={m:(now-utc(s["cycle"])).total_seconds()/3600 for m,s in sources.items()}
    return {"pass":set(ages)==MODELS and all(0<=a<=24 for a in ages.values()),"age_hours":{m:round(a,3) for m,a in ages.items()},"as_of_utc":now.isoformat()}
def obtain(run,work):
    r=api(f"actions/runs/{run}")
    need(r["id"]==run and r["head_branch"]==BRANCH and r["name"]=="TITAN rolling spatial research refresh" and r["status"]=="completed" and r["conclusion"]=="success","Unapproved or incomplete run")
    a=api(f"actions/runs/{run}/artifacts?per_page=100")
    need(a["total_count"]==len(a["artifacts"]),"Artifact list truncated")
    names={x["name"]:x for x in a["artifacts"]};need(len(names)==len(a["artifacts"]),"Duplicate artifact names")
    for name,dest in (("titan-rolling-baseline","receipt"),("titan-auto-publication-report","proof"),("titan-auto-validated-site","site")):
        need(name in names and not names[name]["expired"] and names[name]["size_in_bytes"]>0,"Missing successful-run evidence")
        subprocess.run(["gh","run","download",str(run),"--name",name,"--dir",str(work/dest)],check=True)
    receipt=load(work/"receipt/rolling-baseline.json");proof=load(work/"proof/automated-publication-report.json")
    need(receipt["run"]==run and receipt["revision"]==r["head_sha"] and receipt["advanced"] is True,"Baseline receipt mismatch")
    need(proof["passed"] and proof["archived_replay"] is False and proof["advanced"] and proof["previous_preserved"] and proof["invalid_candidate_rejected"] and proof["retry_idempotent"] and proof["candidate_catalog_sha256"]==receipt["catalog_sha256"],"Publication proof mismatch")
    return r,receipt,proof
def verify(source,receipt):
    p=source/"catalog.json";need(HEX.fullmatch(receipt["catalog_sha256"]) and digest(p)==receipt["catalog_sha256"],"Catalogue hash differs")
    c=load(p);r=c["report"];n=receipt["panels"]
    need(r["passed"] and r["native_leads"]==81 and r["panels"]==n and 0<n<=5000 and len(c["products"])==25 and set(r["domains"])=={"regional","sulawesi","kolaka"} and set(r["sources"])==MODELS and r["sources"]==receipt["sources"],"Catalogue inventory or source differs")
    need(sum(x["entries"] for x in r["sources"].values())==81,"Wrong native inventory")
    files={"catalog.json":p};seen=set()
    for g in c["comparisons"]:
        need(g["product"] in c["products"] and g["domain"] in r["domains"],"Unexpected comparison")
        for e in g["panels"]:
            i=e["id"];name=e["path"];m=e["model"]
            need(HEX.fullmatch(i) and HEX.fullmatch(e["sha256"]) and name=="panels/"+i+".webp" and i not in seen and m in MODELS,"Bad panel identity")
            need((e["product"],e["domain"],e["valid_utc"],e["window_utc"])==(g["product"],g["domain"],g["valid_utc"],g["window_utc"]),"Wrong comparison window")
            need(e["init_utc"]==r["sources"][m]["cycle"] and utc(e["init_utc"])+timedelta(hours=e["lead"])==utc(e["valid_utc"]),"Panel time identity differs")
            ident={k:e[k] for k in ("renderer","product","domain","valid_utc","window_utc","model","init_utc","lead","numeric_sha256","qc")}
            need(hashlib.sha256(json.dumps(ident,sort_keys=True,separators=(",",":")).encode()).hexdigest()==i,"Panel signature differs")
            f=source/name;need(f.is_file() and not f.is_symlink() and f.stat().st_size==e["bytes"] and digest(f)==e["sha256"],"Panel digest differs")
            with f.open("rb") as stream:head=stream.read(12)
            need(head[:4]==b"RIFF" and head[8:]==b"WEBP","Not WebP")
            seen.add(i);files[name]=f
    need(len(seen)==n and {f.name for f in (source/"panels").iterdir()}=={i+".webp" for i in seen},"Unexpected image inventory")
    return c,files
def stage(source,receipt,dest):
    c,files=verify(source,receipt)
    need(not dest.exists(),"Refuse to overwrite existing preview")
    with tempfile.TemporaryDirectory(dir=dest.parent) as td:
        root=Path(td)/"titan";root.mkdir()
        for n,p in files.items():
            out=root/n;out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,out)
        (root/"index.html").write_bytes(Path(__file__).with_name("titan_public_preview.html").read_bytes())
        (root/"catalog.js").write_text("window.TITAN_CATALOG="+json.dumps(c,ensure_ascii=True).replace("</","<\\/")+";",encoding="utf-8")
        verify(root,receipt);shutil.move(str(root),dest)
def main():
    run=int(sys.argv[1]);mode=sys.argv[2];need(mode in ("rehearse","fresh-only"),"Unknown mode")
    out=Path("publication-rehearsal");out.mkdir()
    with tempfile.TemporaryDirectory() as td:
        w=Path(td);r,receipt,proof=obtain(run,w)
        now=datetime.now(timezone.utc);f=freshness(receipt["sources"],now)
        historic=freshness(receipt["sources"],utc(r["updated_at"]))
        need(historic["pass"],"Candidate already stale at original completed run")
        if mode=="fresh-only":need(f["pass"],"Candidate stale now; retain current preview")
        stage(w/"site",receipt,out/"titan")
        # Test the gate deterministically using this same genuine source inventory.
        first=min(utc(x["cycle"]) for x in receipt["sources"].values())
        last=max(utc(x["cycle"]) for x in receipt["sources"].values())
        need(not freshness(receipt["sources"],first+timedelta(hours=24,seconds=1))["pass"] and not freshness(receipt["sources"],last-timedelta(seconds=1))["pass"],"Freshness negative tests failed")
        report={"passed":True,"mode":mode,"source_run":run,"catalog_sha256":receipt["catalog_sha256"],"panels":receipt["panels"],"current_freshness":f,"original_completion_freshness":historic,"production_deployed":False,"remote_writes":False}
        Path("rolling-publication-rehearsal.json").write_text(json.dumps(report,indent=2));print("TITAN_ROLLING_PUBLICATION_STAGE_PASS",json.dumps(report),flush=True)
if __name__=="__main__":main()
