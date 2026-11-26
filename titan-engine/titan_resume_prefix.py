"""One strict same-cycle continuation after a failed ECMWF acquisition.
No relaxation of field QC; no substitute rain intervals."""
import json,time,hashlib
from pathlib import Path
from datetime import datetime,timedelta
import numpy as np
import pilot_cycle_discovery as discovery
def resume(acq,model,c,manifest):
    expected=list(manifest["expected_leads"]);entries=manifest["entries"];n=len(entries)
    if model not in ("ifs-025","ifs-010","aifs-025") or not 0<n<len(expected):raise ValueError("No resumable ECMWF prefix")
    root=acq.ROOT;dx=.1 if model=="ifs-010" else .25
    if [e["lead"] for e in entries]!=expected[:n]:raise ValueError("Noncontiguous prefix")
    if {p.name for p in root.glob("*.npz")}!={e["file"] for e in entries}:raise ValueError("Unexpected partial cache file")
    for e in entries:
        p=root/e["file"]
        if e["file"]!=f"f{e['lead']:03d}.npz" or hashlib.sha256(p.read_bytes()).hexdigest()!=e["sha256"] or e["dx"]!=dx or c+timedelta(hours=e["lead"])!=datetime.fromisoformat(e["valid_utc"]):raise ValueError("Prefix identity differs")
        with np.load(p,allow_pickle=False) as z:
            if set(z.files)!=set(e["products"]) or any(a.shape!=(round(15/dx)+1,round(22/dx)+1) or not np.isfinite(a).any() or np.isinf(a).any() for a in z.values()):raise ValueError("Prefix QC failed")
    old=[(e["file"],e["sha256"]) for e in entries];prev=entries[-1]["lead"]
    checks=[discovery.probe(model,c,h) for h in [prev,*expected[n:]]]
    if not all(discovery.acceptable(x) for x in checks):raise ValueError("Missing continuation metadata")
    rain=acq.rain;rain.INIT=c;rain.OUT=root;rain.fetch=acq.pwv.fetch;rain.used=0
    fam="aifs-single" if model=="aifs-025" else "ifs";res="0p1" if model=="ifs-010" else "0p25"
    boundary,meta=rain.ecmwf(fam,res,prev)
    orig=next(x for x in entries[-1]["provenance"]["rain_source"] if x["lead"]==prev)
    if meta["fields"]!=orig["fields"] or meta["errors"]!=orig["errors"]:raise ValueError("Native boundary GRIB changed")
    bb=sum(x["bytes"] for x in meta["fields"]);total=sum(e["provenance"]["network_payload_bytes"] for e in entries)
    report={"preserved_leads":expected[:n],"preserved_completed_payload_bytes":total,"boundary_bytes":bb,"original_error":manifest.get("error"),"new_leads":[],"qc_relaxed":False}
    manifest["recovery"]=report
    for e in acq.regular(model,c,expected[n:],previous_lead=prev,prior_records=boundary["rain"],initial_bytes=total+bb):
        entries.append(e);report["new_leads"].append(e["lead"]);print("AUTO_RESUMED_LEAD",model,e["lead"],flush=True)
    if [e["lead"] for e in entries]!=expected or any(hashlib.sha256((root/f).read_bytes()).hexdigest()!=h for f,h in old):raise ValueError("Recovered prefix or schedule differs")
    report["complete"]=True
    print("AUTO_PREFIX_RECOVERY_PASS",json.dumps({"model":model,**report}),flush=True)
