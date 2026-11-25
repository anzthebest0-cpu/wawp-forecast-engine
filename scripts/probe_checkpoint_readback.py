"""Read-only retention diagnostics. Never write to Drive or GitHub release."""
import json,re,subprocess,hashlib,tempfile
from pathlib import Path
PREFIX="wawp:checkpoints/rolling"
PAT=re.compile(r"wawp_forecasts-\d+-\d+\.db\.zst(?:\.json|\.registered\.json|\.retired\.json)?$")
def run(*args):
    p=subprocess.run(args,capture_output=True,timeout=60)
    return p
def classify(p):
    s=p.stderr.decode(errors="replace").lower()
    return "ok" if p.returncode==0 else "not_found" if "not found" in s else "quota" if "quota" in s else "permission" if "403" in s else "other"
def main():
    report={}
    p=run("rclone","about","wawp:","--json")
    if p.returncode==0:
        q=json.loads(p.stdout);report["capacity"]={k:q[k] for k in ("total","used","free","trashed","other") if k in q}
    else:report["capacity_error"]=classify(p)
    p=run("rclone","lsjson",PREFIX,"--files-only","--no-modtime","--no-mimetype")
    if p.returncode:raise RuntimeError("Rolling inventory unavailable: "+classify(p))
    allfiles=json.loads(p.stdout)
    files=[{"name":x["Name"],"size":x["Size"]} for x in allfiles if PAT.fullmatch(x["Name"])]
    report["inventory"]=files
    from collections import Counter
    counts=Counter(x["name"] for x in files);report["duplicates"]=[n for n,c in counts.items() if c>1]
    report["managed_payloads"]=sum(x["name"].endswith(".db.zst") for x in files)
    names=sorted(n for n in counts if n.endswith(".registered.json"))
    report["registration_checks"]=[]
    with tempfile.TemporaryDirectory() as d:
        for n in names:
            rec={"name":n,"duplicate":counts[n]>1}
            if counts[n]>1:report["registration_checks"].append(rec);continue
            a=run("rclone","cat",PREFIX+"/"+n,"--retries","1","--low-level-retries","1","--timeout","20s")
            dest=Path(d)/n
            b=run("rclone","copyto","--immutable","--checksum","--retries","1","--low-level-retries","1","--timeout","20s",PREFIX+"/"+n,str(dest))
            rec.update(cat=classify(a),copyto=classify(b))
            if a.returncode==0:
                try:
                    j=json.loads(a.stdout);base=n.removesuffix(".registered.json")
                    rec["registration_shape_valid"]=j.get("version")==1 and j["data"]["name"]==base and j["manifest"]["name"]==base+".json"
                except (ValueError,KeyError,TypeError):rec["registration_shape_valid"]=False
            if a.returncode==0 and b.returncode==0:rec["byte_equal"]=a.stdout==dest.read_bytes()
            report["registration_checks"].append(rec)
    Path("storage-diagnostic.json").write_text(json.dumps(report,indent=2))
    print("STORAGE_DIAGNOSTIC",json.dumps(report),flush=True)
if __name__=="__main__":main()
