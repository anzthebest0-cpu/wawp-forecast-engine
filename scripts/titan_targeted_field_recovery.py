"""Exact rejected prefixes from rolling run 45822462754. Strict recovery only.
Keep original validated NPZ bytes and native rain-boundary provenance.
This is a diagnostic experiment, not a public update."""
import json,os,shutil,sys,time
from pathlib import Path
from datetime import datetime,timedelta
import numpy as np
import pilot_full_spatial_acquire as acq
import pilot_rain_gust as rain
import pilot_pwv_reflectivity as pwv
import pilot_cycle_discovery as discovery
from targeted_ecmwf_retry import wrap
from pilot_multicycle_storage import need,sha,load,dump,read_model
SRC="63802dd909993944be4a29304bb56f24d3c99e26"
CONFIG={"ifs-010":("2026-11-26T00:00:00+00:00",13,60,.1),"aifs-025":("2026-11-26T06:00:00+00:00",10,72,.25)}
def main(model):
    cycle,n,failed,dx=CONFIG[model];c=datetime.fromisoformat(cycle)
    partial=Path("partial");out=Path("regional-cache");proof=Path("recovery-evidence");proof.mkdir()
    report={"model":model,"cycle":cycle,"source_run":45822462754,"original_revision":SRC,"failed_lead":failed,"status":"incomplete","retry_events":[]}
    start=time.monotonic()
    try:
        m=load(partial/"manifest.json")
        expected=[h for h in discovery.LEADS if model!="aifs-025" or h%6==0]
        need(m["model"]==model and m["cycle"]==cycle and m["code_sha"]==SRC and m["passed"] is False and m["error"]=="Required fields missing","Unexpected rejected manifest")
        entries=list(m["entries"])
        need(m["expected_leads"]==expected and [e["lead"] for e in entries]==expected[:n] and expected[n]==failed,"Unexpected partial native schedule")
        need({p.name for p in partial.glob("*.npz")}=={e["file"] for e in entries},"Extra numerical files")
        for e in entries:
            p=partial/e["file"]
            need(e["file"]==f"f{e['lead']:03d}.npz" and sha(p)==e["sha256"] and e["dx"]==dx and c+timedelta(hours=e["lead"])==datetime.fromisoformat(e["valid_utc"]),"Partial source identity mismatch")
            with np.load(p,allow_pickle=False) as z:
                need(set(z.files)==set(e["products"]),"Partial product inventory")
                for a in z.values():need(a.shape==(round(15/dx)+1,round(22/dx)+1) and np.isfinite(a).any() and not np.isinf(a).any(),"Partial QC")
        report["preserved_leads"]=[e["lead"] for e in entries]
        report["preserved_npz_bytes"]=sum((partial/e["file"]).stat().st_size for e in entries)
        report["original_completed_payload_bytes"]=sum(e["provenance"]["network_payload_bytes"] for e in entries)
        # Metadata recheck only for unresolved leads and boundary. Retain exact
        # provider responses in report; don't attempt payload if incomplete.
        prev_h=entries[-1]["lead"]
        checks=[discovery.probe(model,c,h) for h in [prev_h,*expected[n:]]]
        report["metadata_checks"]=checks
        print("TARGETED_METADATA",json.dumps({"model":model,"checks":checks}),flush=True)
        need(all(discovery.acceptable(x) for x in checks),"Original cycle now lacks required metadata")
        shutil.copytree(partial,out);acq.ROOT=out
        def event(x):
            report["retry_events"].append(x);print("TARGETED_TRANSFER_RETRY",json.dumps({"model":model,**x}),flush=True)
        pwv.fetch=wrap(pwv.fetch,report=event)
        rain.INIT=c;rain.OUT=out;rain.fetch=pwv.fetch;rain.used=0
        family="aifs-single" if model=="aifs-025" else "ifs";res="0p1" if model=="ifs-010" else "0p25"
        boundary,meta=rain.ecmwf(family,res,prev_h)
        orig=next(x for x in entries[-1]["provenance"]["rain_source"] if x["lead"]==prev_h)
        need(meta["fields"]==orig["fields"] and meta["errors"]==orig["errors"],"Native rain boundary message provenance changed")
        boundary_bytes=sum(x["bytes"] for x in meta["fields"])
        report["rain_boundary_verified"]=True;report["rain_boundary_bytes"]=boundary_bytes
        for e in acq.regular(model,c,expected[n:],previous_lead=prev_h,prior_records=boundary["rain"],initial_bytes=report["original_completed_payload_bytes"]+boundary_bytes):
            e["provenance"]["acquisition_code_sha"]=os.environ["GITHUB_SHA"]
            entries.append(e);print("TARGETED_RECOVERED_LEAD",model,e["lead"],flush=True)
        need([e["lead"] for e in entries]==expected,"Incomplete recovery")
        for e in entries[:n]:need(sha(out/e["file"])==e["sha256"],"Original prefix changed")
        new={**m,"passed":True,"entries":entries,"code_sha":os.environ["GITHUB_SHA"],"original_prefix_code_sha":SRC,"recovery_status":"strict_targeted_recovery","elapsed_seconds":round(time.monotonic()-start,2)}
        new.pop("error",None)
        (out/"manifest.json").write_text(json.dumps(new,indent=2,allow_nan=False))
        read_model(out,model,allowed={os.environ["GITHUB_SHA"]})
        report.update({"status":"recovered_complete","new_leads":[e["lead"] for e in entries[n:]],"new_completed_payload_bytes":sum(e["provenance"]["network_payload_bytes"] for e in entries[n:]),"manifest_sha256":sha(out/"manifest.json"),"all_native_leads":len(entries),"qc_relaxed":False})
    except Exception as e:
        report["error"]=str(e)[:1500];report["new_leads"]=[v["lead"] for v in locals().get("entries",[])[n:]]
        fail=out/"required-field-failure.json"
        if fail.is_file():report["field_failure"]=load(fail)
        if out.is_dir():
            # Never leave a partial cache looking complete.
            p=out/"manifest.json"
            if p.exists():
                p.write_text(json.dumps({**load(p),"passed":False,"entries":locals().get("entries",[]),"recovery_error":report["error"]},indent=2))
    finally:
        (proof/"report.json").write_bytes(dump(report));print("TARGETED_FIELD_RECOVERY",json.dumps(report),flush=True)
    if report["status"]!="recovered_complete":raise SystemExit("Strict recovery incomplete; retain old verified model")
if __name__=="__main__":main(sys.argv[1])
