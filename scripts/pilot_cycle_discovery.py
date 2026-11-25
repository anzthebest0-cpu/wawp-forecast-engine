"""Bounded metadata-only discovery of spatial model cycles. No live publication."""
import json,urllib.request,urllib.error,urllib.parse
from datetime import datetime,timedelta,timezone
from pathlib import Path
UTC=timezone.utc
LEADS=tuple(range(0,25,3))+tuple(range(30,49,6))+tuple(range(60,97,12))
MODELS=("gfs","ifs-025","ifs-010","aifs-025","icon")
BASE={"u/925","v/925","u/850","v/850","u/200","v/200","q/925","q/850","q/700","sp/sfc","msl/sfc","2t/sfc","2d/sfc"}|{f"{v}/{p}" for v in ("t","z") for p in (925,850,700,600,500,400)}
EC_EXPECTED={m:BASE|({"r/850","r/700","tcwv/sfc","mucape/sfc"} if m.startswith("ifs") else set()) for m in MODELS if m.startswith(("ifs","aifs"))}
GFS_REQUIRED={(v,f"{p} mb") for v in ("UGRD","VGRD") for p in (925,850,200)}|{(v,f"{p} mb") for v in ("TMP","HGT") for p in (925,850,700,600,500,400)}|{("SPFH",f"{p} mb") for p in (925,850,700)}|{("RH","850 mb"),("RH","700 mb"),("PRES","surface"),("PRMSL","mean sea level"),("TMP","2 m above ground"),("DPT","2 m above ground"),("CAPE","surface"),("CIN","surface"),("PWAT","entire atmosphere (considered as a single layer)"),("REFC","entire atmosphere")}
CAP=4*1024**2
def candidates(now):
    first=now.replace(hour=now.hour//6*6,minute=0,second=0,microsecond=0)
    return [first-timedelta(hours=6*i) for i in range(5)]
def request(url,head=False):
    req=urllib.request.Request(url,method="HEAD" if head else "GET",headers={"User-Agent":"TITAN-WAWP-cycle-discovery/1.0","Accept-Encoding":"identity"})
    try:
        with urllib.request.urlopen(req,timeout=18) as res:
            if urllib.parse.urlsplit(res.geturl()).hostname!=urllib.parse.urlsplit(url).hostname:raise ValueError("Unexpected redirect")
            if head:
                size=res.headers.get("Content-Length")
                return {"status":"present","bytes":int(size) if size and size.isdigit() else None}
            b=res.read(CAP+1)
            if not b or len(b)>CAP:raise ValueError("Invalid metadata size")
            return b
    except urllib.error.HTTPError as e:
        return {"status":"absent" if e.code==404 else "provider_error","http":e.code}
    except (OSError,ValueError) as e:
        return {"status":"provider_error","reason":type(e).__name__}
def ec(model,cycle,h):
    family="aifs-single" if model=="aifs-025" else "ifs"
    res="0p1" if model=="ifs-010" else "0p25"
    root=f"https://data.ecmwf.int/forecasts/{cycle:%Y%m%d}/{cycle:%H}z/{family}/{res}/oper/{cycle:%Y%m%d%H}0000-{h}h-oper-fc"
    fields={};errors=[]
    for suffix in ("-pl","-sfc") if res=="0p1" else ("",):
        raw=request(root+suffix+".index")
        if isinstance(raw,dict):errors.append({"suffix":suffix,**raw});continue
        try:
            for line in raw.splitlines():
                if not line.strip():continue
                row=json.loads(line);lev=row.get("levtype")
                if lev not in ("pl","sfc"):continue
                k=row["param"]+"/"+(str(row["levelist"]) if lev=="pl" else "sfc")
                if k in fields and k in EC_EXPECTED[model]:raise ValueError("Duplicate required field")
                a,b=row["_offset"],row["_length"]
                if not isinstance(a,int) or not isinstance(b,int) or a<0 or b<=0:raise ValueError("Invalid range")
                fields[k]=b
        except (ValueError,KeyError,TypeError) as e:errors.append({"suffix":suffix,"status":"invalid_index","reason":type(e).__name__})
    expected=EC_EXPECTED[model]|({"tp/sfc","10fg/sfc"} if h and family=="ifs" else {"tp/sfc"} if h else set())
    missing=sorted(expected-set(fields))
    return {"status":"metadata_complete" if not errors and not missing else "partial" if fields else "unavailable","missing":missing,"errors":errors,"estimated_selected_bytes":sum(fields[k] for k in expected&set(fields)),"available":sorted(expected&set(fields))}
def gfs(cycle,h):
    url=f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f{h:03d}.idx"
    raw=request(url)
    if isinstance(raw,dict):return raw
    entries=set()
    try:
        for line in raw.decode("ascii").splitlines():
            p=line.split(":")
            if len(p)<6 or not p[1].isdigit() or p[2]!=f"d={cycle:%Y%m%d%H}":raise ValueError("Unexpected GFS index")
            entries.add((p[3],p[4]))
    except (ValueError,UnicodeError):return {"status":"invalid_index"}
    expected=GFS_REQUIRED|({("APCP","surface"),("GUST","surface")} if h else set())
    # Provider names vary; aliases explicitly limited to identical layer labels.
    if ("REFC","entire atmosphere (considered as a single layer)") in entries:entries.add(("REFC","entire atmosphere"))
    missing=sorted("/".join(k) for k in expected-entries)
    return {"status":"metadata_complete" if not missing else "partial","missing":missing,"available_count":len(expected&entries)}
def icon(cycle,h):
    stamp=cycle.strftime("%Y-%m-%dT%H")+"%3A00"
    results={}
    for p in ("TQV","PMSL","CAPE_ML"):
        results[p]=request(f"https://opendata.dwd.de/weather/nwp/v1/m/icon/p/{p}/r/{stamp}/s/PT{h:03d}H00M.grib2",head=True)
    return {"status":"objects_present" if all(x["status"]=="present" for x in results.values()) else "partial","objects":results}
def probe(model,cycle,h):
    try:
        return {**(gfs(cycle,h) if model=="gfs" else icon(cycle,h) if model=="icon" else ec(model,cycle,h)),"lead":h,"valid_utc":(cycle+timedelta(hours=h)).isoformat()}
    except Exception as e:return {"status":"probe_error","lead":h,"reason":type(e).__name__}
def acceptable(x):
    return x["status"] in ("metadata_complete","objects_present")
def discover(model,now):
    attempts=[];selected=None
    for c in candidates(now):
        first=probe(model,c,0);last=probe(model,c,96)
        attempts.append({"cycle":c.isoformat(),"h0":first,"h96":last})
        if acceptable(first) and acceptable(last):
            selected=c;break
    if selected is None:return {"model":model,"selected":None,"attempts":attempts,"leads":[]}
    # Sequential requests per provider/model; no fabricated missing leads.
    results=[probe(model,selected,h) for h in LEADS]
    complete=[x["lead"] for x in results if acceptable(x)]
    return {"model":model,"selected":selected.isoformat(),"attempts":attempts,"leads":results,"available_leads":complete,"missing_leads":[h for h in LEADS if h not in complete],"all_requested_metadata_present":len(complete)==len(LEADS)}
def main():
    now=datetime.now(UTC)
    # Run providers sequentially to avoid hammering shared ECMWF origin.
    results=[]
    for m in MODELS:
        r=discover(m,now);results.append(r)
        print("CYCLE_DISCOVERY",json.dumps({k:v for k,v in r.items() if k not in ("attempts","leads")}),flush=True)
    report={"mode":"metadata_only_not_forecast_validation","as_of_utc":now.isoformat(),"requested_leads":LEADS,"results":results,"notes":"Selection requires H0 and H96 metadata. ICON HEAD only; coordinates and decoded fields not validated. No interpolation or automatic publication. Product-specific availability and GRIB step ranges need decode QC."}
    Path("cycle-discovery.json").write_text(json.dumps(report,indent=2))
    print("DISCOVERY_COMPLETE",len(results),flush=True)
if __name__=="__main__":main()
