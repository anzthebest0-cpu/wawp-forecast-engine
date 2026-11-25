"""Re-run one historical lead through unchanged ECMWF decoders, share exact ranges."""
import json,sys,hashlib
from pathlib import Path
import pilot_pwv_reflectivity as pwv
import pilot_circulation_comparison as circ
import pilot_instability_moisture as dyn
import pilot_vertical_profiles as vert
import pilot_rain_gust as rain
from shared_spatial_fetch import SharedFetch
ROOT=Path("shared-acquisition-pilot")
def historical():
    """Manifest records only: count identical byte ranges, reject conflicting hashes."""
    requests=[]
    for group in ("pwv","circulation","dynamics","rain","vertical"):
        c=json.loads((Path("review-inputs")/"sources"/group/"manifest.json").read_text())
        for lead,models in c["leads"].items():
            for model,m in models.items():
                if not model.startswith(("IFS","AIFS")):continue
                if group=="pwv":
                    requests.append((group,m["url"],m["range"][0],m["bytes"],m["sha256"]))
                    continue
                fields=m["fields"]
                for f in (fields.values() if isinstance(fields,dict) else fields):
                    requests.append((group,f.get("url",m.get("url")),f["offset"],f["bytes"],f["sha256"]))
    unique={};groups={}
    for group,url,offset,size,sha in requests:
        if not url or "data.ecmwf.int/forecasts/20261125/00z/" not in url:raise ValueError("Unexpected URL")
        k=(url,offset,size)
        if k in unique and unique[k]!=sha:raise ValueError("Historical hash conflict")
        unique[k]=sha;groups.setdefault(k,set()).add(group)
    return unique,{"requests":len(requests),"unique_ranges":len(unique),"duplicate_ranges":len(requests)-len(unique),"original_bytes":sum(x[3] for x in requests),"unique_bytes":sum(k[2] for k in unique),"shared_across_groups":sum(len(v)>1 for v in groups.values())}
def main():
    ROOT.mkdir(exist_ok=False)
    hashes,audit=historical()
    (ROOT/"historical-inventory.json").write_text(json.dumps(audit,indent=2))
    print("HISTORICAL_RANGE_AUDIT",json.dumps(audit),flush=True)
    shared=SharedFetch(pwv.fetch)
    for m in (pwv,circ,dyn,vert,rain):
        m.fetch=shared
        m.OUT=ROOT/("tmp-"+m.__name__);m.OUT.mkdir()
    # Existing decode and QC functions only, no changes to meteorological equations.
    results=[]
    for model,res in (("ifs","0p25"),("ifs","0p1"),("aifs-single","0p25")):
        if model=="ifs":pwv.ifs(6,res)
        a,dx,meta=circ.ecmwf(model,res,6)
        b,dx,meta=dyn.read_ecmwf(model,res,6)
        if meta["missing"] or meta["errors"]:raise ValueError("Incomplete dynamics")
        q,qc=dyn.diagnostics(b,dx)
        c,dx,meta=vert.read_ecmwf(model,res,6)
        if meta["missing"] or meta["errors"]:raise ValueError("Incomplete vertical profile")
        q,qc=vert.diagnostics(c,dx)
        d,meta=rain.ecmwf(model,res,6)
        if meta["errors"] or rain.rain_window(d["rain"],[],6) is None:raise ValueError("Incomplete rain")
        results.append(model+"/"+res)
    for e in shared.events:
        if e["interval"]:
            k=(e["url"],e["interval"][0],e["interval"][1])
            if hashes.get(k)!=e["sha256"]:raise ValueError("Historical source range hash mismatch")
    hits=[x for x in shared.events if x["hit"]]
    if not hits:raise ValueError("No actual reuse")
    report={"mode":"fixed_historical_lead_retrieval","lead":6,"validated_models":results,"requests":len(shared.events),"cache_hits":len(hits),"network_payload_bytes":shared.bytes,"avoided_payload_bytes":sum(x["bytes"] for x in hits),"range_sha256_matches":sum(x["interval"] is not None for x in shared.events),"historical_inventory":audit}
    (ROOT/"report.json").write_text(json.dumps(report,indent=2))
    print("SHARED_ACQUISITION_PASS",json.dumps(report),flush=True)
if __name__=="__main__":main()
