"""Isolated real Drive publication, two actual validated TITAN generations.
Immutable model-cycle packs, metadata-last commit chain and remote-only restore.
No deletion, no production mutation, no scheduled trigger."""
import os,re,json,hashlib,zipfile,shutil,tempfile
from pathlib import Path
from collections import defaultdict
from src import titan_fixture_drive_transport as remote
from pilot_multicycle_storage import read_model,folder,need,sha,dump,load
import titan_full_independent_panels as full
from pilot_full_spatial_render import MODELS,SPECS,DOMAINS
from PIL import Image
OLD_RUN="45759575549"
NEW_RUN="45764124872"
NEW_REV="41b8a0235c21b1eea823cc58161f4cabe3eed913"
MODE="isolated_independent_panel_archive"
HEX=re.compile(r"[0-9a-f]{64}")
NAME=re.compile(r"(numeric|charts|metadata|evidence)-[0-9a-f]{64}\.zip|generation-[0-9a-f]{64}\.json|commit-00000[12]\.json")
def prefix():
    run=os.environ["GITHUB_RUN_ID"];need(run.isdigit(),"Unsafe run ID")
    return "wawp:validation/titan-spatial-storage/run-"+run+"-1"
def pack(path,files):
    with zipfile.ZipFile(path,"x",compression=zipfile.ZIP_STORED,allowZip64=True) as z:
        for name,p in sorted(files.items()):
            need(not name.startswith("/") and ".." not in Path(name).parts and p.is_file(),"Unsafe pack member")
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_STORED
            with p.open("rb") as src,z.open(info,"w",force_zip64=True) as dst:
                shutil.copyfileobj(src,dst,1024*1024)
    return {n:sha(p) for n,p in files.items()}
def ref(kind,p):
    h=sha(p);return {"name":kind+"-"+h+p.suffix,"sha256":h,"bytes":p.stat().st_size}
def check(r):
    need(HEX.fullmatch(r["sha256"]) and r["bytes"]>0 and r["name"] in [k+"-"+r["sha256"]+s for k,s in (("numeric",".zip"),("charts",".zip"),("metadata",".zip"),("evidence",".zip"),("generation",".json"))],"Bad immutable ref")
def verify_pack(p,expected,dest=None):
    with zipfile.ZipFile(p) as z:
        need(len(z.namelist())==len(set(z.namelist())) and set(z.namelist())==set(expected),"Pack members differ")
        for n in z.namelist():
            need(not n.startswith("/") and ".." not in Path(n).parts,"Unsafe packed path")
            with z.open(n) as f:h=hashlib.file_digest(f,"sha256").hexdigest()
            need(h==expected[n],"Member hash differs")
            if dest is not None:
                out=dest/n;out.parent.mkdir(parents=True,exist_ok=True)
                need(not out.exists(),"Duplicate restored member")
                with z.open(n) as src,out.open("xb") as dst:shutil.copyfileobj(src,dst,1024*1024)
class Store:
    def __init__(self,root,create=False):
        self.root=root;self.prefix=prefix();remote.PREFIX=self.prefix
        if create:remote.retry_cli(["rclone","mkdir",self.prefix,"--tpslimit","0.5","--retries","1"])
        self.t=remote.RollingTransport();need(self.t.kind=="drive","Drive required")
        self.inv=self.t.inventory();self.read_bytes=0;self.upload_bytes=0;self.inventory_calls=1;self.cache={}
        self.check_inventory(self.inv)
    def check_inventory(self,inv):
        need(all(NAME.fullmatch(n) for n in inv),"Unknown fixture object")
    def refresh(self,added=()):
        actual=self.t.inventory();self.inventory_calls+=1;self.check_inventory(actual)
        need(set(actual)==set(self.inv)|set(added) and all(actual[n]==v for n,v in self.inv.items()),"Unexpected inventory change")
        self.inv=actual
    def read(self,r,fresh=False):
        check(r);n=r["name"];need(n in self.inv and self.inv[n]["size"]==r["bytes"],"Object missing or wrong size")
        p=self.root/("read-"+n)
        if not fresh and self.cache.get(n)==self.inv[n] and p.is_file():
            need(sha(p)==r["sha256"],"Local readback cache differs");return p
        self.t._get(self.inv[n],{"supportsAllDrives":"true","alt":"media"},p)
        need(sha(p)==r["sha256"] and p.stat().st_size==r["bytes"],"Remote digest differs")
        self.cache[n]=dict(self.inv[n]);self.read_bytes+=p.stat().st_size;return p
    def batch(self,objects):
        # Only upload immutable absent objects, never overwrite. Batch rclone
        # avoids a repeated folder listing per 3,567 individual image files.
        self.refresh()
        with tempfile.TemporaryDirectory(dir=self.root) as td:
            staging=Path(td);added=[]
            for r,p in objects:
                check(r);need(sha(p)==r["sha256"] and p.stat().st_size==r["bytes"],"Source digest differs")
                n=r["name"]
                if n not in self.inv and n not in added:
                    shutil.copyfile(p,staging/n);added.append(n);self.upload_bytes+=r["bytes"]
            if added:
                remote.retry_cli(["rclone","copy",str(staging),self.prefix,"--immutable","--checksum","--transfers","1","--checkers","1","--tpslimit","0.5","--tpslimit-burst","1","--retries","1","--low-level-retries","2"])
                self.refresh(added)
        for r,p in objects:self.read(r)
    def markers(self):
        names=sorted(n for n in self.inv if n.startswith("commit-"))
        need(names==[f"commit-{i:06}.json" for i in range(1,len(names)+1)],"Commit sequence gap")
        result=[]
        for n in names:
            p=self.root/n
            self.t._get(self.inv[n],{"supportsAllDrives":"true","alt":"media"},p)
            self.read_bytes+=p.stat().st_size;m=load(p)
            need(m["mode"]==MODE and m["sequence"]==len(result)+1 and m["previous_commit_sha256"]==(result[-1][1] if result else None),"Broken commit chain")
            check(m["generation"]);result.append((m,sha(p)))
        return result
    def commit(self,r,sequence):
        self.refresh();chain=self.markers()
        if len(chain)>=sequence:
            need(chain[sequence-1][0]["generation"]==r,"Existing marker differs");return
        need(len(chain)==sequence-1,"Unexpected commit sequence")
        self.verify_generation(r)
        m={"schema":1,"mode":MODE,"sequence":sequence,"generation":r,"previous_commit_sha256":chain[-1][1] if chain else None}
        n=f"commit-{sequence:06}.json";p=self.root/("upload-"+n);p.write_bytes(dump(m))
        self.refresh();need(n not in self.inv,"Commit name already exists")
        remote.rclone_copy(str(p),self.prefix+"/"+n);self.refresh([n]);self.upload_bytes+=p.stat().st_size
        chain=self.markers();need(len(chain)==sequence and chain[-1]==(m,sha(p)),"Commit readback differs")
    def verify_generation(self,r,restore=None):
        g=load(self.read(r))
        need(g["schema"]==1 and g["mode"]==MODE and set(g["numeric"])==set(MODELS)==set(g["charts"]) and g["native_entries"]==81 and g["panels"]==3567,"Invalid generation inventory")
        for kind in ("numeric","charts"):
            for m,x in g[kind].items():
                d=None
                if restore is not None:d=folder(restore/"numbers",m) if kind=="numeric" else restore/"site"
                verify_pack(self.read(x["object"]),x["members"],d)
        for k in ("metadata","evidence"):
            x=g[k];d=(restore/"site" if k=="metadata" else restore/"evidence") if restore is not None else None
            verify_pack(self.read(x["object"]),x["members"],d)
        return g
def entries(site,numbers,stage):
    data=load(site/"catalog.json");r=data["report"]
    need(r["passed"] and r["native_leads"]==81 and r["panels"]==3567 and r["products"]==25 and set(r["domains"])==set(DOMAINS),"Bad site catalogue")
    need(r["renderer"]==full.renderer_identity(),"Unexpected renderer")
    expected={};manifests={}
    for m in MODELS:
        f=folder(numbers,m);allowed={NEW_REV} if m=="aifs-025" and stage=="new" else {full.REVISIONS[m]}
        a,rows=read_model(f,m,allowed=allowed)
        cycle="2026-11-26T00:00:00+00:00" if m=="aifs-025" and stage=="new" else full.CYCLES[m]
        need(a["cycle"]==cycle and r["sources"][m]["manifest_sha256"]==sha(f/"manifest.json")==sha(site/"sources"/(m+".json")),"Site/source mismatch")
        manifests[m]=a
        for e in rows:
            for p in e["products"]:
                if p not in SPECS:continue
                q=[]
                if p.startswith("rh"):
                    q=[{"level_hpa":x["level_hpa"],"lead":x["lead"],"above_ground_masked":x["quarantine"]["above_ground_masked"],"raw_grib_sha256":x["rh_grib_sha256"]} for x in e["provenance"].get("rh_range_diagnostics",[]) if x["level_hpa"]==int(p[2:]) and x.get("quarantine",{}).get("above_ground_masked",0)>0]
                for d in DOMAINS:
                    i=full.identity(p,e["valid_utc"],tuple(e["windows"].get(p,[])),d,(m,cycle,e,f/e["file"],q),r["renderer"])
                    expected[full.key(i)]=i
    seen={}
    for c in data["comparisons"]:
        for e in c["panels"]:
            ident=e["id"];need(ident in expected and ident not in seen and {k:e[k] for k in expected[ident]}==expected[ident] and e["path"]=="panels/"+ident+".webp","Wrong panel identity")
            need((e["product"],e["domain"],e["valid_utc"],e["window_utc"])==(c["product"],c["domain"],c["valid_utc"],c["window_utc"]),"Wrong comparison grouping")
            p=site/e["path"];need(p.stat().st_size==e["bytes"] and sha(p)==e["sha256"],"Image checksum differs")
            with Image.open(p) as im:need(im.format=="WEBP" and list(im.size)==e["dimensions"],"Image metadata differs");im.load()
            seen[ident]=e
    need(set(seen)==set(expected) and len(seen)==3567 and {p.name for p in (site/"panels").glob("*.webp")}=={i+".webp" for i in seen},"Panel inventory differs")
    return data,seen,manifests
def write_generation(root,site,numbers,stage,evidence):
    data,panels,manifests=entries(site,numbers,stage)
    objects=[];g={"schema":1,"mode":MODE,"stage":stage,"source_run":OLD_RUN if stage=="old" else NEW_RUN,"native_entries":81,"panels":len(panels),"numeric":{},"charts":{}}
    def make(kind,label,files):
        p=root/(stage+"-"+label+".zip");members=pack(p,files);r=ref(kind,p);objects.append((r,p));return {"object":r,"members":members}
    for m in MODELS:
        f=folder(numbers,m);a=manifests[m]
        g["numeric"][m]=make("numeric",m+"-numeric",{"manifest.json":f/"manifest.json",**{e["file"]:f/e["file"] for e in a["entries"]}})
        g["charts"][m]=make("charts",m+"-charts",{e["path"]:site/e["path"] for e in panels.values() if e["model"]==m})
    g["metadata"]=make("metadata","metadata",{n:site/n for n in ("catalog.json","catalog.js","index.html","report.json",*("sources/"+m+".json" for m in MODELS))})
    g["evidence"]=make("evidence","evidence",{p.relative_to(evidence).as_posix():p for p in evidence.rglob("*") if p.is_file()})
    p=root/(stage+"-generation.json");p.write_bytes(dump(g));r=ref("generation",p);objects.append((r,p));return r,g,objects
def publish():
    proof=load(Path("live-proof/automated-publication-report.json"))
    need(proof["passed"] and proof["advanced"] and proof["archived_replay"] is False and proof["new_numeric_models"]==["aifs-025"] and proof["new_panels"]==582 and proof["reused_panels"]==2985 and sha(Path("new-site/catalog.json"))==proof["candidate_catalog_sha256"],"Unexpected live update proof")
    evidence=Path("qc-evidence");d=load(evidence/"report.json")
    need(d["status"]=="recovered_complete_with_experimental_rh_quarantine" and len(d["diagnostics"])==1,"Missing RH evidence")
    diag=d["diagnostics"][0]
    for key,name in (("rh_grib_sha256","h060-rh700.grib2"),("sp_grib_sha256","h060-surface-pressure.grib2"),("q_grib_sha256","h060-q700.grib2"),("t_grib_sha256","h060-t700.grib2")):need(sha(evidence/name)==diag[key],"Raw QC evidence differs")
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);s=Store(root,True)
        r1,g1,o1=write_generation(root,Path("old-site"),Path("old-numbers"),"old",evidence)
        r2,g2,o2=write_generation(root,Path("new-site"),Path("new-numbers"),"new",evidence)
        need(all((g1[k][m]==g2[k][m])==(m!="aifs-025") for k in ("numeric","charts") for m in MODELS),"Unexpected shared model pack identity")
        need(g1["evidence"]==g2["evidence"],"Evidence changed")
        s.batch(o1);s.verify_generation(r1);s.commit(r1,1)
        before=s.markers();before_upload=s.upload_bytes
        s.batch(o2);s.verify_generation(r2)
        need(s.markers()==before or len(before)==2 and s.markers()==before,"Staging changed commit chain")
        # Deliberately incomplete generation reference: fail before commit.
        bad={**r2,"name":"generation-"+"0"*64+".json","sha256":"0"*64}
        try:s.verify_generation(bad)
        except ValueError:pass
        else:raise ValueError("Missing generation accepted")
        need(s.markers()==before,"Rejected candidate changed chain")
        s.commit(r2,2);chain=s.markers();saved=s.upload_bytes
        s.batch(o2);s.commit(r2,2)
        need(s.markers()==chain and s.upload_bytes==saved,"Retry duplicated remote objects")
        s.verify_generation(r1);s.verify_generation(r2);s.refresh()
        keep={g[k][m]["object"]["name"] for g in (g1,g2) for k in ("charts","numeric") for m in MODELS}
        need(keep<=set(s.inv),"Protected packs missing")
        report={"passed":True,"mode":MODE,"prefix":s.prefix,"commits":2,"retained_chart_packs":len({g["charts"][m]["object"]["name"] for g in (g1,g2) for m in MODELS}),"permanent_numeric_packs":len({g["numeric"][m]["object"]["name"] for g in (g1,g2) for m in MODELS}),"new_chart_pack_bytes":g2["charts"]["aifs-025"]["object"]["bytes"],"new_numeric_pack_bytes":g2["numeric"]["aifs-025"]["object"]["bytes"],"remote_active_bytes":sum(v["size"] for v in s.inv.values()),"uploaded_bytes":s.upload_bytes,"second_generation_upload_bytes":saved-before_upload,"readback_bytes":s.read_bytes,"inventory_calls":s.inventory_calls,"direct_http_requests":remote.HTTP_REQUESTS,"invalid_rejected":True,"retry_idempotent":True,"retired_packs":0,"reason_no_retirement":"Both generations retained; all registered chart packs referenced."}
        Path("remote-publication-report.json").write_bytes(dump(report));print("INDEPENDENT_REMOTE_PUBLICATION_PASS",json.dumps(report),flush=True)
def restore():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);s=Store(root);chain=s.markers();need(len(chain)==2,"Expected two remote commits")
        results=[]
        for i,(m,h) in enumerate(chain):
            d=root/("restore-"+str(i));d.mkdir();g=s.verify_generation(m["generation"],d)
            stage="old" if i==0 else "new";need(g["stage"]==stage and g["source_run"]==(OLD_RUN if i==0 else NEW_RUN),"Wrong restored generation")
            data,panels,manifests=entries(d/"site",d/"numbers",stage)
            results.append({"stage":stage,"catalog_sha256":sha(d/"site/catalog.json"),"panels":len(panels),"native_entries":sum(len(x["entries"]) for x in manifests.values()),"cycle":manifests["aifs-025"]["cycle"]})
            if stage=="new":shutil.copytree(d/"site","restored-current-site")
        need(results[0]["cycle"]!=results[1]["cycle"],"Restored generations not distinct")
        s.refresh();need(s.markers()==chain,"Commit chain changed during independent restoration")
        report={"passed":True,"mode":"remote_only_independent_restore","prefix":s.prefix,"generations":results,"readback_bytes":s.read_bytes,"remote_writes":0,"current_previous_verified":True}
        Path("remote-restore-report.json").write_bytes(dump(report));print("INDEPENDENT_REMOTE_RESTORE_PASS",json.dumps(report),flush=True)
if __name__=="__main__":
    import sys
    if sys.argv[1]=="publish":publish()
    else:restore()
