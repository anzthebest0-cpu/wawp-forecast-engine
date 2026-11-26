"""Metadata-only gate for the exact verified research baseline. No payload reads."""
import json,os,sys,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
from pilot_cycle_discovery import LEADS,MODELS
RUN=45759575549
REV="156ee88e4bd101d460964130dae42a07858d4f49"
ARTIFACTS={15090033322:"titan-full-independent-review-site",15089271806:"titan-independent-inputs",15089137091:"titan-panel-runtime-lock"}
CYCLES={"gfs":"2026-11-26T00:00:00+00:00","ifs-025":"2026-11-25T18:00:00+00:00","ifs-010":"2026-11-25T18:00:00+00:00","aifs-025":"2026-11-25T18:00:00+00:00","icon":"2026-11-26T00:00:00+00:00"}
def utc(x):
    t=datetime.fromisoformat(x)
    if t.utcoffset()!=timedelta(0):raise ValueError("Non UTC")
    return t
def plan(d):
    asof=utc(d["as_of_utc"]);rs=d["results"]
    if len(rs)!=len(MODELS) or {r["model"] for r in rs}!=set(MODELS):raise ValueError("Incomplete discovery inventory")
    models={};advance=False;degraded=False;stale=False
    for r in rs:
        m=r["model"];prior=utc(CYCLES[m]);selected=r.get("selected")
        expected=[h for h in LEADS if m!="aifs-025" or h%6==0]
        age=(asof-prior).total_seconds()/3600
        if age<0:raise ValueError("Baseline is in the future")
        if selected and utc(selected)>asof:raise ValueError("Discovery returned future cycle")
        if not selected:status="provider_unavailable"
        elif utc(selected)<prior:status="provider_regression"
        elif r.get("available_leads")!=expected:status="incomplete_native_schedule"
        elif utc(selected)>prior:status="new_complete_metadata";advance=True
        else:status="unchanged"
        if status not in ("new_complete_metadata","unchanged"):degraded=True
        if status!="new_complete_metadata" and age>24:stale=True
        models[m]={"status":status,"baseline_cycle":CYCLES[m],"discovered_cycle":selected,"baseline_age_hours":round(age,3)}
    decision="update" if advance else "blocked_stale" if stale else "degraded_no_advance" if degraded else "no_change"
    return {"decision":decision,"models":models,"as_of_utc":d["as_of_utc"],"has_new_complete_metadata":advance,"stale_fallback_present":stale}
def api(path):
    base=os.environ["GITHUB_API_URL"];repo=os.environ["GITHUB_REPOSITORY"]
    req=urllib.request.Request(f"{base}/repos/{repo}/{path}",headers={"Authorization":"Bearer "+os.environ["GH_TOKEN"],"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
def main():
    run=api(f"actions/runs/{RUN}")
    if run["id"]!=RUN or run["head_sha"]!=REV or run["status"]!="completed" or run["conclusion"]!="success":raise ValueError("Baseline run not verified")
    artifacts=[]
    now=datetime.now(timezone.utc)
    for ident,name in ARTIFACTS.items():
        a=api(f"actions/artifacts/{ident}")
        if a["id"]!=ident or a["name"]!=name or a["expired"] or utc(a["expires_at"])<=now or a["size_in_bytes"]<=0 or a["workflow_run"]["id"]!=RUN:raise ValueError("Missing/expired baseline artifact "+name)
        artifacts.append({"id":ident,"name":name,"size_in_bytes":a["size_in_bytes"],"expires_at":a["expires_at"]})
    p=plan(json.loads(Path("cycle-discovery.json").read_text()))
    p.update({"baseline_run":RUN,"baseline_revision":REV,"baseline_artifacts":artifacts,"mode":"metadata_only_live_discovery","payload_revalidated":False,"new_payload_bytes":0,"new_images":0,"publication_changed":False})
    Path("early-exit-report.json").write_text(json.dumps(p,indent=2))
    with open(os.environ["GITHUB_OUTPUT"],"a") as f:f.write("decision="+p["decision"]+"\n")
    print("AUTO_EARLY_GATE",json.dumps(p),flush=True)
    if p["decision"]=="blocked_stale":raise SystemExit("Stale baseline; preserve publication but block update")
if __name__=="__main__":main()
