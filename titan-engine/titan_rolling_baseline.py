"""Choose latest completed successful rolling candidate, or exact validated seed.
Research only: Actions artifacts, no remote archive or Pages writes."""
import json,os,subprocess,tempfile
from pathlib import Path
from datetime import datetime,timezone
from titan_early_exit import api,MODELS
SEED=45764124872
SEED_REV="41b8a0235c21b1eea823cc58161f4cabe3eed913"
SEED_CAT="cd6906d90afaaf90796cea6470e402da8e321d11dac076b7fb53d33d1116ceff"
BRANCH="main"
TRANSITION_RUN=45902498571
TRANSITION_REV="f0acc90bf3b1df25f7ccb6c18c2a8148e11e5fb3"
def need(ok,msg):
    if not ok:raise ValueError(msg)
def artifact_inventory(run):
    a=api(f"actions/runs/{run}/artifacts?per_page=100")
    need(a["total_count"]==len(a["artifacts"]),"Artifact inventory truncated")
    names={}
    for x in a["artifacts"]:
        need(x["name"] not in names,"Duplicate artifact")
        names[x["name"]]=x
    return names
def valid_artifacts(run,a):
    names=["titan-auto-validated-site"]+["titan-full-cache-"+m for m in MODELS]
    need(all(n in a and not a[n]["expired"] and a[n]["size_in_bytes"]>0 for n in names),"Baseline artifacts missing/expired; do not roll back silently")
    return {n:a[n]["id"] for n in names}
def main():
    runs=api("actions/runs?branch="+BRANCH+"&status=success&per_page=50")["workflow_runs"]
    chosen=None
    for r in runs:
        if r["name"]!="TITAN rolling spatial research refresh" or r["head_branch"]!=BRANCH or r.get("path","").split("@")[0]!=".github/workflows/titan_rolling_refresh.yml":continue
        a=artifact_inventory(r["id"])
        if "titan-rolling-baseline" not in a:continue
        need(not a["titan-rolling-baseline"]["expired"],"Most recent published baseline expired")
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["gh","run","download",str(r["id"]),"--name","titan-rolling-baseline","--dir",td],check=True)
            p=json.loads((Path(td)/"rolling-baseline.json").read_text())
        need(p["run"]==r["id"] and p["revision"]==r["head_sha"] and p["advanced"] is True,"Baseline receipt lineage differs")
        chosen={**p,"artifacts":valid_artifacts(r["id"],a)};break
    if chosen is None:
        # Start production from the last independently validated rolling run.
        # Do not fall back to the older fixed seed after its expiry.
        r=api(f"actions/runs/{TRANSITION_RUN}")
        need(r["head_sha"]==TRANSITION_REV and r["head_branch"]=="codex/titan-rolling-refresh" and r["status"]=="completed" and r["conclusion"]=="success","Transition run not verified")
        a=artifact_inventory(TRANSITION_RUN)
        need("titan-rolling-baseline" in a and not a["titan-rolling-baseline"]["expired"],"Transition receipt missing")
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["gh","run","download",str(TRANSITION_RUN),"--name","titan-rolling-baseline","--dir",td],check=True)
            p=json.loads((Path(td)/"rolling-baseline.json").read_text())
        need(p["run"]==TRANSITION_RUN and p["revision"]==TRANSITION_REV and p["advanced"] is True,"Transition receipt differs")
        chosen={**p,"artifacts":valid_artifacts(TRANSITION_RUN,a)}
    need(set(chosen["sources"])==set(MODELS),"Incomplete baseline sources")
    Path("rolling-baseline.json").write_text(json.dumps(chosen,sort_keys=True))
    with open(os.environ["GITHUB_OUTPUT"],"a") as f:f.write("baseline_run="+str(chosen["run"])+"\n")
    print("ROLLING_BASELINE_SELECTED",json.dumps(chosen),flush=True)
if __name__=="__main__":main()
