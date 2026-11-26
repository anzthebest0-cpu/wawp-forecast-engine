"""Choose latest completed successful rolling candidate, or exact validated seed.
Research only: Actions artifacts, no remote archive or Pages writes."""
import json,os,subprocess,tempfile
from pathlib import Path
from datetime import datetime,timezone
from titan_early_exit import api,MODELS
SEED=45764124872
SEED_REV="41b8a0235c21b1eea823cc58161f4cabe3eed913"
SEED_CAT="cd6906d90afaaf90796cea6470e402da8e321d11dac076b7fb53d33d1116ceff"
BRANCH="codex/titan-rolling-refresh"
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
        if r["name"]!="TITAN rolling spatial research refresh" or r["head_branch"]!=BRANCH:continue
        a=artifact_inventory(r["id"])
        if "titan-rolling-baseline" not in a:continue
        need(not a["titan-rolling-baseline"]["expired"],"Most recent published baseline expired")
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["gh","run","download",str(r["id"]),"--name","titan-rolling-baseline","--dir",td],check=True)
            p=json.loads((Path(td)/"rolling-baseline.json").read_text())
        need(p["run"]==r["id"] and p["revision"]==r["head_sha"] and p["advanced"] is True,"Baseline receipt lineage differs")
        chosen={**p,"artifacts":valid_artifacts(r["id"],a)};break
    if chosen is None:
        r=api(f"actions/runs/{SEED}");need(r["head_sha"]==SEED_REV and r["status"]=="completed" and r["conclusion"]=="success","Seed not verified")
        a=artifact_inventory(SEED)
        chosen={"run":SEED,"revision":SEED_REV,"catalog_sha256":SEED_CAT,"panels":3567,"advanced":True,"artifacts":valid_artifacts(SEED,a),"sources":{
          "gfs":{"cycle":"2026-11-26T00:00:00+00:00","acquisition_sha":"ff68052a7b71016f29d18b85f9af98426d81fb1c","manifest_sha256":"33d02956e14f72de42e3cefcb375ae193f68500e4332ce1c2c0340fb9affebf1"},
          "ifs-025":{"cycle":"2026-11-25T18:00:00+00:00","acquisition_sha":"ff68052a7b71016f29d18b85f9af98426d81fb1c","manifest_sha256":"b00dad830b638707554bacc04a7bd035d505a34a56f318dcd8ed32745afd17dd"},
          "ifs-010":{"cycle":"2026-11-25T18:00:00+00:00","acquisition_sha":"dac98fdf26f37aeaf811ec462f18f5134a806d36","manifest_sha256":"ee6857171b3dbfff5ee50317e9c7398f35a39cf0eebdd82a234ec77e4af148eb"},
          "aifs-025":{"cycle":"2026-11-26T00:00:00+00:00","acquisition_sha":SEED_REV,"manifest_sha256":"e4b329854a2fd67baf37e19a5b351825119de33823c69b50dc570da16e186f81"},
          "icon":{"cycle":"2026-11-26T00:00:00+00:00","acquisition_sha":"ff68052a7b71016f29d18b85f9af98426d81fb1c","manifest_sha256":"cf790868c5460232ab1997689bb6a66d84797499c579f00fcd5bd3ab908ec337"}}}
    need(set(chosen["sources"])==set(MODELS),"Incomplete baseline sources")
    Path("rolling-baseline.json").write_text(json.dumps(chosen,sort_keys=True))
    with open(os.environ["GITHUB_OUTPUT"],"a") as f:f.write("baseline_run="+str(chosen["run"])+"\n")
    print("ROLLING_BASELINE_SELECTED",json.dumps(chosen),flush=True)
if __name__=="__main__":main()
