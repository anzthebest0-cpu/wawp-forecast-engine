"""Read-only TITAN reliability audit. Counts and status metadata only."""
import json, os, subprocess, sys
from datetime import datetime, timezone, timedelta
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen

NOW=datetime.now(timezone.utc)
ORIGIN="https://anzthebest0-cpu.github.io"
SITE=ORIGIN+"/wawp-forecast-engine/data/"
AWOS="https://wawp-forecast-engine.anzthebest0.workers.dev"
NAMES={"WAWP Model-Only Refresh","WAWP Publish Verified Model Preview","TITAN rolling spatial research refresh","TITAN private numerical archive","TITAN numerical restore proof","WAWP Prospective Shadow Research"}
def utc(s):
    try:
        d=datetime.fromisoformat(s.replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,TypeError,AttributeError): return None
def age(d): return round((NOW-d).total_seconds()/60,1) if d else None
def api(path):
    return json.loads(subprocess.check_output(["gh","api","repos/"+os.environ["GITHUB_REPOSITORY"]+"/"+path],timeout=45,stderr=subprocess.DEVNULL))
def get(url):
    with urlopen(Request(url,headers={"Origin":ORIGIN,"Cache-Control":"no-cache","User-Agent":"TITAN-readonly-audit/1"}),timeout=25) as r:
        body=r.read(4_000_001)
        if len(body)>4_000_000: raise ValueError("response limit")
        return json.loads(body)
def check(label,fn):
    try: return fn()
    except Exception as e: return {"status":"unavailable","error_type":type(e).__name__}
def workflow_audit():
    since=(NOW-timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs=api("actions/runs?branch=main&per_page=100&created=%3E%3D"+since)["workflow_runs"]
    result={}
    for name in sorted(NAMES):
        rr=[r for r in runs if r.get("name")==name]
        item={"counts":dict(Counter(r.get("conclusion") or r["status"] for r in rr)),"recent":[]}
        for r in rr[:5]:
            rec={k:r.get(k) for k in ("id","event","status","conclusion","created_at","run_started_at","updated_at")}
            a,b=utc(r.get("created_at")),utc(r.get("run_started_at"))
            rec["queue_minutes"]=round((b-a).total_seconds()/60,1) if a and b else None
            if r.get("conclusion")=="failure":
                try:
                    jobs=api("actions/runs/"+str(r["id"])+"/jobs?per_page=100")["jobs"]
                    rec["failed_steps"]=[{"job":j["name"],"step":s["name"]} for j in jobs for s in j.get("steps",[]) if s.get("conclusion")=="failure"]
                except Exception: rec["failed_steps"]=None
            item["recent"].append(rec)
        result[name]=item
    return {"window_hours":48,"returned_runs":len(runs),"possibly_truncated":len(runs)==100,"workflows":result}
def model_audit():
    m=get(SITE+"model_preview_manifest.json")
    expiry=utc(m.get("expires_at_utc"))
    models=m.get("models",{})
    return {"contract":m.get("contract"),"status":m.get("status"),"source_rows":m.get("row_count"),"published_as_of":m.get("as_of_utc"),"expiry":m.get("expires_at_utc"),"currently_eligible":m.get("status")=="model_only" and expiry is not None and expiry>NOW and (utc(m.get("as_of_utc")) or NOW)<=NOW,"models":{k:{"collection_age_minutes":age(utc(v.get("scraped_at_utc"))),"expiry":v.get("expires_at_utc"),"rows":v.get("row_count")} for k,v in models.items()},"excluded_models":m.get("excluded_models",{}),"taf_status":m.get("taf_status")}
def awos_audit():
    h=get(AWOS+"/awos-history?hours=24")
    if h.get("schema")!="titan-awos-history-v1" or h.get("station")!="WAWP" or h.get("window_hours")!=24 or h.get("retention_hours")!=24: raise ValueError("schema")
    gen=utc(h.get("generated_at_utc"))
    rows=h["rows"]; stamps=[utc(r.get("observed_at_utc")) for r in rows]
    if not gen or any(t is None for t in stamps): raise ValueError("clock")
    times=sorted(set(stamps))
    gaps=[(b-a).total_seconds()/60 for a,b in zip(times,times[1:])]
    fields=["wind_speed_kt","wind_direction_deg","gust_kt","temperature_c","dewpoint_c","relative_humidity_pct","altimeter_setting_hpa","rain_current_hour_mm","solar_wm2","density_altitude_ft"]
    return {"status":"available" if rows else "empty","samples":len(rows),"reports":len(h["reports"]),"generated_at":h["generated_at_utc"],"oldest_age_minutes":age(times[0]) if times else None,"latest_age_minutes":age(times[-1]) if times else None,"duplicate_timestamps":len(stamps)-len(times),"outside_24h_at_query":sum(not gen-timedelta(hours=24)<=t<=gen for t in stamps),"gaps_over_3min":sum(g>3 for g in gaps),"max_gap_minutes":round(max(gaps),2) if gaps else None,"non_null_counts":{f:sum(type(r.get(f)) in (int,float) for r in rows) for f in fields},"wind_reference":h.get("wind_reference"),"physical_deletion_verified":False}
def checkpoint_audit():
    release=api("releases/tags/latest-db")
    assets=api("releases/"+str(release["id"])+"/assets?per_page=100")
    packs=[a for a in assets if a.get("name","").endswith(".db.zst")]
    return {"asset_count_first_page":len(assets),"checkpoint_count_first_page":len(packs),"checkpoint_bytes_first_page":sum(a.get("size",0) for a in packs),"latest_checkpoint_asset_update":max((a.get("updated_at","") for a in packs),default=None),"readback_not_performed":True,"possibly_truncated":len(assets)==100}
def main():
    out={"as_of_utc":NOW.isoformat(),"workflows":check("workflows",workflow_audit),"public_model":check("public_model",model_audit),"awos":check("awos",awos_audit),"checkpoint":check("checkpoint",checkpoint_audit)}
    path=Path(sys.argv[1]);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,indent=2,allow_nan=False))
    print(json.dumps(out,indent=2,allow_nan=False))
if __name__=="__main__":main()
