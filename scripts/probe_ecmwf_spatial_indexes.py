"""One-time metadata-only inventory: no global GRIB downloads or production changes."""
import json
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
ROOT="https://data.ecmwf.int/forecasts"
CYCLE="20261125000000"
LEADS=(0,6,12)
FIELDS={("u","925"),("v","925"),("u","850"),("v","850"),("r","850"),("r","700"),("sp","sfc"),("msl","sfc")}
EXTRA={("t","925"),("t","850"),("t","700"),("t","500"),("q","925"),("q","850"),("q","700"),("z","500"),("u","200"),("v","200"),("cape","sfc"),("cin","sfc"),("tp","sfc"),("tcwv","sfc"),("tcw","sfc"),("ttr","sfc"),("mucape","sfc"),("tciw","sfc"),("tclw","sfc"),("tcrw","sfc"),("tcc","sfc")}
def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":"TITAN-WAWP-regional-pilot/1.0"})
    with urllib.request.urlopen(req,timeout=30) as res:
        body=res.read(4*1024*1024+1)
        if len(body)>4*1024*1024:raise ValueError("Oversized index")
    rows=[]
    for line in body.splitlines():
        if not line.strip():continue
        row=json.loads(line)
        if not isinstance(row.get("_length"),int) or row["_length"]<=0 or not isinstance(row.get("_offset"),int) or row["_offset"]<0:raise ValueError("Invalid index")
        rows.append(row)
    if not rows:raise ValueError("Empty index")
    return rows
def key(row):
    return (row.get("param"),str(row.get("levelist")) if row.get("levtype")=="pl" else "sfc" if row.get("levtype")=="sfc" else "other")
def main():
    out=[]
    for model,res,suffixes in [("ifs","0p25",("",)),("ifs","0p1",("-pl","-sfc")),("aifs-single","0p25",("",))]:
        for lead in LEADS:
            selected={};extras={};errors=[];urls=[]
            for suffix in suffixes:
                url=f"{ROOT}/20261125/00z/{model}/{res}/oper/{CYCLE}-{lead}h-oper-fc{suffix}.index"
                urls.append(url)
                try:
                    for row in fetch(url):
                        k=key(row)
                        if k in FIELDS or k in EXTRA:
                            dest=selected if k in FIELDS else extras
                            label="/".join(k)
                            if label in dest:raise ValueError("Duplicate target field "+label)
                            dest[label]=row["_length"]
                except Exception as e:errors.append({"file":suffix,"error":str(e)[:250]})
            expected={"/".join(k) for k in FIELDS}
            result={"model":model,"grid":res,"lead":lead,"cycle":CYCLE,"missing":sorted(expected-set(selected)),"selected":selected,"global_field_bytes":sum(selected.values()),"extra_fields":extras,"errors":errors,"index_urls":urls}
            out.append(result);print(json.dumps(result),flush=True)
    Path("multimodel-inventory.json").write_text(json.dumps({"retrieved_utc":datetime.now(timezone.utc).isoformat(),"results":out},indent=2))
if __name__=="__main__":main()
