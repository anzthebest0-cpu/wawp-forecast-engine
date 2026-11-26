"""Recover exact failed IFS cycle, preserving 13 original NPZs.
Strict RH limits retained above ground; only proven below-ground points may be masked.
If above-ground RH fails, explicitly use the previously verified baseline for comparison."""
import hashlib,json,os,shutil,time,traceback
from pathlib import Path
from datetime import datetime,timedelta
import numpy as np
import pilot_full_spatial_acquire as acq
import pilot_circulation_comparison as circ
import pilot_rain_gust as rain
import pilot_pwv_reflectivity as pwv
from pilot_multicycle_storage import BASE_SHA,EXPERIMENT_SHA,read_model,load,sha,need,folder,dump
from pilot_cycle_discovery import LEADS

CYCLE="2026-11-25T18:00:00+00:00"
MODEL="ifs-010"
DIAG=Path("ifs-rh-diagnostics")
class AboveGroundRH(ValueError):pass

def main():
    DIAG.mkdir(); report={"mode":"exact_cycle_targeted_recovery","source_run":"45752072415","cycle":CYCLE,"status":"incomplete","diagnostics":[]}
    start=time.monotonic();out=Path("regional-cache");base=folder("baseline",MODEL);partial=Path("partial")
    try:
        old,old_rows=read_model(base,MODEL,allowed={BASE_SHA})
        m=load(partial/"manifest.json")
        need(m["model"]==MODEL and m["cycle"]==CYCLE and m["code_sha"]==EXPERIMENT_SHA and m["passed"] is False and m["error"]=="Range QC ('r', 700)","Unexpected failed acquisition")
        completed=[e["lead"] for e in m["entries"]];need(completed==list(LEADS[:13]) and completed[-1]==48,"Unexpected prefix leads")
        need({p.name for p in partial.glob("*.npz")}=={e["file"] for e in m["entries"]},"Unexpected partial files")
        # Verify retained prefix using the same complete-reader checks individually
        # (its schedule gate is only relaxed for this explicitly incomplete artifact).
        for e in m["entries"]:
            p=partial/e["file"];need(e["file"]==f"f{e['lead']:03d}.npz" and sha(p)==e["sha256"] and datetime.fromisoformat(CYCLE)+timedelta(hours=e["lead"])==datetime.fromisoformat(e["valid_utc"]),"Partial cache digest or time mismatch")
            with np.load(p,allow_pickle=False) as f:
                need(set(f.files)==set(e["products"]),"Partial field inventory")
                for a in f.values():need(a.shape==(151,221) and np.isfinite(a).any() and not np.isinf(a).any(),"Partial array QC")
        shutil.copytree(partial,out)
        c=datetime.fromisoformat(CYCLE);acq.ROOT=out
        for mod in (circ,rain,pwv):mod.INIT=c;mod.OUT=out
        report["preserved_leads"]=completed;original_bytes=sum(e["provenance"]["network_payload_bytes"] for e in m["entries"])
        # Reconstruct the previous native cumulative rain records, checking the
        # exact original GRIB message hashes at H48, not just the resulting image.
        rain.used=0;rain.fetch=pwv.fetch
        prev,meta=rain.ecmwf("ifs","0p1",48)
        original_meta=next(x for x in m["entries"][-1]["provenance"]["rain_source"] if x["lead"]==48)
        need(meta["fields"]==original_meta["fields"] and meta["errors"]==original_meta["errors"],"H48 rain source changed")
        boundary_bytes=sum(x["bytes"] for x in meta["fields"])
        report["rain_boundary_verified"]=True;report["rain_boundary_payload_bytes"]=boundary_bytes
        # Fetch only the exact surface pressure message if RH is out of range.
        # In the original circulation loop pressure arrives after RH, so validate
        # it here independently through the same strict GRIB decoder.
        pressures={}
        def hook(data,k,h,dx,arr,lo,hi):
            bad=np.isfinite(arr)&((arr<lo)|(arr>hi))
            if not bad.any():return arr
            if h not in pressures:
                url=f"https://data.ecmwf.int/forecasts/{c:%Y%m%d}/{c:%H}z/ifs/0p1/oper/{c:%Y%m%d%H}0000-{h}h-oper-fc-sfc.grib2"
                rows=[json.loads(x) for x in circ.fetch(url.removesuffix(".grib2")+".index",4*1024**2).splitlines() if x.strip()]
                chosen=[x for x in rows if x.get("param")=="sp" and x.get("levtype")=="sfc"]
                need(len(chosen)==1,"Surface pressure index ambiguous")
                r=chosen[0];off,size=r["_offset"],r["_length"]
                need(isinstance(off,int) and off>=0 and isinstance(size,int) and 0<size<=8*1024**2,"Pressure byte range invalid")
                raw=circ.fetch(url,size,(off,size))
                pressures[h]=(circ.crop(raw,("sp",0),h,dx),hashlib.sha256(raw).hexdigest())
                (DIAG/f"h{h:03d}-surface-pressure.grib2").write_bytes(raw)
            sp,sp_hash=pressures[h]
            above=bad&(sp>=k[1]);below=bad&(sp<k[1]);unknown=bad&~np.isfinite(sp)
            ii,jj=np.where(bad);values=arr[bad]
            positions=[{"lon":round(110+j*dx,5),"lat":round(-10+i*dx,5),"rh_pct":float(arr[i,j]),"surface_pressure_hpa":float(sp[i,j])} for i,j in zip(ii[:50],jj[:50])]
            row={"lead":h,"level_hpa":k[1],"finite_points":int(np.isfinite(arr).sum()),"min_pct":float(np.nanmin(arr)),"max_pct":float(np.nanmax(arr)),"outside_original_qc":int(bad.sum()),"below_ground":int(below.sum()),"above_ground":int(above.sum()),"unknown_pressure":int(unknown.sum()),"bad_min_pct":float(values.min()),"bad_max_pct":float(values.max()),"examples":positions,"rh_grib_sha256":hashlib.sha256(data).hexdigest(),"sp_grib_sha256":sp_hash}
            (DIAG/f"h{h:03d}-rh{k[1]}.grib2").write_bytes(data)
            np.savez_compressed(DIAG/f"h{h:03d}-rh{k[1]}-diagnostic.npz",rh=arr,sp=sp,bad=bad,above_ground=above)
            report["diagnostics"].append(row);print("RH_RANGE_DIAGNOSTIC",json.dumps(row),flush=True)
            if above.any() or unknown.any():raise AboveGroundRH("Out-of-range RH at an above-ground or unknown-pressure cell")
            # Only values in locations already excluded by the original terrain
            # mask are removed, before applying the unchanged range rule.
            result=arr.copy();result[below]=np.nan;return result
        circ.RH_QC_HOOK=hook
        entries=list(m["entries"]);report["status"]="resuming"
        try:
            for e in acq.regular(MODEL,c,LEADS[13:],previous_lead=48,prior_records=prev["rain"],initial_bytes=original_bytes+boundary_bytes):
                e["provenance"]["acquisition_code_sha"]=os.environ["GITHUB_SHA"]
                e["provenance"]["rh_range_diagnostics"]=[d for d in report["diagnostics"] if d["lead"]==e["lead"]]
                entries.append(e);print("RECOVERED_LEAD",e["lead"],flush=True)
        except AboveGroundRH:
            # No threshold relaxation or silent interpolation. Keep partial evidence
            # and use the last fully validated older model in the mixed-cycle study.
            report["status"]="new_cycle_rejected_above_ground_rh"
            report["new_leads_completed_before_rejection"]=[e["lead"] for e in entries[13:]]
            (out/"manifest.json").write_text(json.dumps({**m,"entries":entries,"passed":False,"recovery_status":report["status"]},indent=2))
            shutil.move(str(out),str(DIAG/"rejected-regional-cache"))
            shutil.copytree(base,out)
            receipt={"model":MODEL,"mode":"same_cycle_reused","baseline_cycle":old["cycle"],"selected_cycle":old["cycle"],"rejected_new_cycle":CYCLE,"network_payload_bytes":0,"original_acquisition_revision":BASE_SHA,"recovery_status":report["status"],"failed_cycle_completed_payload_bytes":original_bytes,"recovery_boundary_payload_bytes":boundary_bytes}
        else:
            need([e["lead"] for e in entries]==list(LEADS),"Incomplete recovery")
            for e in entries[:13]:need(sha(out/e["file"])==e["sha256"],"Retained prefix changed")
            report["status"]="recovered_complete"
            new={**m,"passed":True,"entries":entries,"code_sha":os.environ["GITHUB_SHA"],"original_prefix_code_sha":EXPERIMENT_SHA,"recovery_status":report["status"],"elapsed_seconds":round(time.monotonic()-start,2)}
            new.pop("error",None)
            (out/"manifest.json").write_text(json.dumps(new,indent=2,allow_nan=False))
            read_model(out,MODEL,allowed={os.environ["GITHUB_SHA"]})
            receipt={"model":MODEL,"mode":"new_cycle_acquired","baseline_cycle":old["cycle"],"selected_cycle":CYCLE,"network_payload_bytes":sum(e["provenance"]["network_payload_bytes"] for e in entries),"original_acquisition_revision":EXPERIMENT_SHA,"recovery_revision":os.environ["GITHUB_SHA"],"recovery_status":report["status"],"recovery_boundary_payload_bytes":boundary_bytes,"retained_prefix_leads":13,"newly_acquired_leads":4}
        Path("reuse-receipt.json").write_bytes(dump(receipt));report["receipt"]=receipt
    except Exception as e:
        report["error"]=str(e)[:500];raise
    finally:
        (DIAG/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False))
        print("IFS_RECOVERY",json.dumps({k:v for k,v in report.items() if k!="diagnostics"}),flush=True)
if __name__=="__main__":main()
