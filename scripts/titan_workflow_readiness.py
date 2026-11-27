"""Publish allowlisted workflow metadata and counts-only shadow readiness.
Never include forecasts, observations, raw logs, credentials or private files."""
import json,os,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path

def api(path):
    return json.loads(subprocess.check_output(["gh","api","repos/"+os.environ["GITHUB_REPOSITORY"]+"/"+path],text=True,stderr=subprocess.DEVNULL,timeout=45))
def compact(r):
    return {k:r.get(k) for k in ("id","status","conclusion","event","updated_at")} if r else None
def main(out):
    d={"schema":1,"as_of_utc":datetime.now(timezone.utc).isoformat(),"runs":dict.fromkeys(("rolling","scheduled","archive","publication","shadow","point_refresh")),"shadow":None}
    try:
        rows=api("actions/runs?branch=main&per_page=100")["workflow_runs"]
        def select(name,event=None,success=False):
            return next((r for r in rows if r["name"]==name and r["head_branch"]=="main" and (event is None or r["event"]==event) and (not success or r["conclusion"]=="success")),None)
        for k,name in [("rolling","TITAN rolling spatial research refresh"),("archive","TITAN private numerical archive"),("publication","WAWP Publish Verified Model Preview"),("shadow","WAWP Prospective Shadow Research"),("point_refresh","WAWP Model-Only Refresh")]:
            row=select(name)
            if k=="publication" and row and row["id"]==int(os.environ["GITHUB_RUN_ID"]):row=next((r for r in rows if r["name"]==name and r["id"]!=int(os.environ["GITHUB_RUN_ID"])),None)
            d["runs"][k]=compact(row)
        d["runs"]["scheduled"]=compact(select("TITAN rolling spatial research refresh","schedule"))
        s=select("WAWP Prospective Shadow Research",success=True)
        if s:
            name=f"shadow-research-status-{s['id']}-{s['run_attempt']}"
            with tempfile.TemporaryDirectory() as td:
                subprocess.run(["gh","run","download",str(s["id"]),"--name",name,"--dir",td],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60)
                x=json.loads((Path(td)/"status.json").read_text())
            if x.get("status")!="research_only_no_promotion":raise ValueError("Unexpected research status")
            counts=("source_models","raw_rows","candidate_rows","pending_or_unobserved_rows","scored_groups")
            ic=("issued_vectors","pending_or_unobserved_vectors","verified_vectors","all_three_fits_active_vectors","distinct_verified_days","distinct_wet_valid_times","distinct_all_fits_active_days","distinct_all_fits_active_wet_times","current_cycle_vectors","current_cycle_all_fits_active")
            def count(o,k):
                v=o.get(k);return v if type(v) is int and v>=0 else None
            init=datetime.fromisoformat(x["initialization_utc"]);assert init.utcoffset() is not None
            i=x.get("integrated",{})
            d["shadow"]={"run":s["id"],"initialization_utc":init.astimezone(timezone.utc).isoformat(),"awos_freshness_gate_passed":x.get("awos_freshness_gate_passed") is True,**{k:count(x,k) for k in counts},"integrated":{**{k:count(i,k) for k in ic},"review_minimum_sample_met":i.get("review_minimum_sample_met") is True}}
    except Exception:
        print("TITAN_READINESS_PARTIAL: workflow metadata or counts-only artifact unavailable; no skill inferred",flush=True)
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(d,sort_keys=True,allow_nan=False))
    print("TITAN_WORKFLOW_READINESS_WRITTEN",flush=True)
if __name__=="__main__":main(sys.argv[1])
