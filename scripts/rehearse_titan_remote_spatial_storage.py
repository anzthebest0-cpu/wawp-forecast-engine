"""Real spatial storage experiment: isolated Drive, immutable packs and manifest-last commits.
Frozen real artifacts, not a live forecast; never touches the operational checkpoint directory."""
import hashlib,json,os,re,subprocess,tempfile,zipfile
from pathlib import Path
from unittest.mock import patch
from collections import Counter
import requests
from src import titan_fixture_drive_transport as remote
from rehearse_titan_real_storage import (MODELS,CACHE_SHA,REFINED_SHA,require,sha,dump,load,
    numeric_archive,validate_catalog)
HEX=re.compile(r"[0-9a-f]{64}")
COMMIT=re.compile(r"commit-(\d{6})\.json")
def pack(path,files):
    # Store already-compressed NPZ/WebP without recompression, stable order and timestamps.
    with zipfile.ZipFile(path,"x",compression=zipfile.ZIP_STORED,allowZip64=True) as z:
        for name,p in sorted(files.items()):
            require(not name.startswith("/") and ".." not in Path(name).parts and p.is_file(),"Unsafe pack member")
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_STORED
            with p.open("rb") as src,z.open(info,"w",force_zip64=True) as dst:
                while chunk:=src.read(1024*1024):dst.write(chunk)
    return path
def verify_pack(p,expected):
    with zipfile.ZipFile(p) as z:
        require(len(z.namelist())==len(set(z.namelist())) and set(z.namelist())==set(expected),"Pack membership differs")
        for name in z.namelist():
            require(not name.startswith("/") and ".." not in Path(name).parts,"Unsafe pack member")
            with z.open(name) as f:h=hashlib.file_digest(f,"sha256").hexdigest()
            require(h==expected[name],"Packed member hash differs")
def members(files):return {n:sha(p) for n,p in sorted(files.items())}
def ref(kind,path):
    h=sha(path);return {"name":kind+"-"+h+(".zip" if path.suffix==".zip" else ".json"),"sha256":h,"bytes":path.stat().st_size}
def check_ref(r):
    require(r["name"] in [k+"-"+r["sha256"]+e for k,e in (("numeric",".zip"),("charts",".zip"),("metadata",".zip"),("generation",".json"))] and HEX.fullmatch(r["sha256"]) and r["bytes"]>0,"Unsafe object ref")
class Store:
    def __init__(self,prefix,root):
        self.prefix=prefix;self.root=root;self.t=remote.RollingTransport();require(self.t.kind=="drive","Drive required")
        self.inv=self.t.inventory()
        self.initial=dict(self.inv);self.uploaded=0;self.reused=0;self.read_bytes=0
        self.inventory_calls=1;self.cache_hits=0;self.verified={};self.member_proofs={}
        require(all(COMMIT.fullmatch(n) or re.fullmatch(r"(numeric|charts|metadata)-[0-9a-f]{64}\.zip|generation-[0-9a-f]{64}\.json",n) for n in self.inv),"Unknown object in resume directory")
    def refresh(self,*,added=None,removed=None):
        fresh=self.t.inventory();self.inventory_calls+=1
        expected=dict(self.inv)
        if removed:expected.pop(removed)
        if added:
            require(added not in expected and added in fresh,"Expected uploaded object missing")
            expected[added]=fresh[added]
        require(fresh==expected,"Remote inventory changed unexpectedly")
        self.inv=fresh;return fresh
    def read(self,r,*,fresh=False):
        check_ref(r);n=r["name"];require(n in self.inv and self.inv[n]["size"]==r["bytes"],"Remote reference missing")
        dest=self.root/("read-"+n)
        if not fresh and self.verified.get(n)==self.inv[n] and dest.is_file():
            require(sha(dest)==r["sha256"],"Local verified cache changed");self.cache_hits+=1;return dest
        if dest.exists():dest.unlink()
        # Listing establishes the exact ID, size and membership; verify content by
        # ID directly, then reconcile the complete inventory at commit boundaries.
        self.t._get(self.inv[n],{"supportsAllDrives":"true","alt":"media"},dest)
        require(sha(dest)==r["sha256"] and dest.stat().st_size==r["bytes"],"Remote object hash differs")
        self.verified[n]=dict(self.inv[n]);self.read_bytes+=dest.stat().st_size;return dest
    def put(self,r,p):
        check_ref(r);require(sha(p)==r["sha256"] and p.stat().st_size==r["bytes"],"Source object differs")
        n=r["name"]
        if n not in self.inv:
            self.refresh()
            remote.rclone_copy(str(p),self.prefix+"/"+n)
            self.refresh(added=n);self.uploaded+=r["bytes"]
        else:self.reused+=r["bytes"]
        self.read(r)
    def markers(self):
        names=sorted(n for n in self.inv if COMMIT.fullmatch(n))
        require(names==[f"commit-{i:06}.json" for i in range(1,len(names)+1)],"Commit sequence has gaps")
        out=[]
        for n in names:
            p=self.root/n
            if self.verified.get(n)!=self.inv[n] or not p.is_file():
                self.t._get(self.inv[n],{"supportsAllDrives":"true","alt":"media"},p)
                self.verified[n]=dict(self.inv[n]);self.read_bytes+=p.stat().st_size
            m=load(p)
            require(m["sequence"]==int(COMMIT.fullmatch(n).group(1)) and m["previous_commit_sha256"]==(out[-1][1] if out else None),"Broken commit chain")
            require(m["mode"]=="frozen_research_preview","Unexpected publication mode")
            check_ref(m["generation"]);out.append((m,sha(p)))
        return out
    def verify_gen(self,r,*,deep=False):
        g=load(self.read(r))
        require(g["schema"]==1 and g["mode"]=="frozen_research_preview","Invalid generation")
        require(len(g["numeric"])==5 and len(g["numeric_entries"])==81 and len(g["chart_members"])==1563,"Incomplete generation")
        refs=[(x["object"],x["members"]) for x in g["numeric"].values()]+[(g["chart_object"],g["chart_members"]),(g["metadata_object"],g["metadata_members"])]
        for obj,expected in refs:
            check_ref(obj);n=obj["name"]
            require(n in self.inv and self.inv[n]["size"]==obj["bytes"],"Incomplete generation object inventory")
            if deep:
                p=self.read(obj)
                signature=(obj["sha256"],hashlib.sha256(dump(expected)).hexdigest())
                if self.member_proofs.get(n)!=signature:
                    verify_pack(p,expected);self.member_proofs[n]=signature
        return g
    def publish(self,r,sequence):
        self.refresh();prior=self.markers()
        # Reconcile an existing immutable marker; never republish an old generation
        # as the newest when restarting a partially completed run.
        if len(prior)>=sequence:
            require(prior[sequence-1][0]["generation"]==r,"Existing commit refers to unexpected generation")
            self.verify_gen(r,deep=True);return prior[sequence-1][0]
        require(len(prior)==sequence-1,"Unexpected publication sequence")
        self.verify_gen(r,deep=True)
        if prior:self.verify_gen(prior[-1][0]["generation"])
        m={"schema":1,"mode":"frozen_research_preview","sequence":sequence,"generation":r,"previous_commit_sha256":prior[-1][1] if prior else None}
        n=f"commit-{sequence:06}.json";p=self.root/("upload-"+n)
        # Preserve exact original commit serialization and chain hash.
        p.write_text(json.dumps(m,sort_keys=True,indent=2)+"\n")
        self.refresh();require(n not in self.inv,"Commit already exists")
        remote.rclone_copy(str(p),self.prefix+"/"+n)
        self.refresh(added=n)
        observed=self.markers();require(len(observed)==sequence and observed[-1][0]==m and sha(self.root/n)==sha(p),"Commit readback differs")
        return m
    def trash_old_chart(self,r,keep,registered):
        # Only one exact, previously registered fixture chart pack; never numeric or metadata.
        check_ref(r);n=r["name"];require(n.startswith("charts-") and n not in keep and n in registered,"Chart not eligible")
        inv=self.refresh();require(n in inv,"Missing retired chart")
        info=inv[n]
        self.read(r,fresh=True);self.t.metadata(n,info)
        require(self.prefix.startswith("wawp:validation/titan-spatial-storage/run-"),"Not a disposable prefix")
        remote.pace()
        with requests.patch(self.t._url(info),params={"supportsAllDrives":"true","fields":"id,name,size,trashed"},headers=self.t._headers(),json={"trashed":True},timeout=(20,60),allow_redirects=False) as resp:
            require(resp.status_code==200,"Fixture Trash not confirmed")
            data=resp.json();require(data.get("id")==info["id"] and data.get("name")==n and int(data.get("size",-1))==info["size"] and data.get("trashed") is True,"Trash identity differs")
        self.t.metadata(n,info,trashed=True)
        self.refresh(removed=n);require(n not in self.inv,"Retired chart still active")
        return r["bytes"]
def main():
    run=os.environ["GITHUB_RUN_ID"];attempt=os.environ["GITHUB_RUN_ATTEMPT"]
    require(re.fullmatch(r"\d+",run) and re.fullmatch(r"\d+",attempt),"Invalid run identity")
    prefix="wawp:validation/titan-spatial-storage/run-45743437475-1" # exact failed fixture, not an arbitrary path
    report={"mode":"isolated_real_drive_frozen_weather","prefix":prefix,"passed":False,"stage":"validate_local","checks":{}}
    try:
        with tempfile.TemporaryDirectory(prefix="titan-remote-spatial-") as td:
            root=Path(td);numbers=root/"numbers";numbers.mkdir();work=root/"remote";work.mkdir()
            cache=Path("cache-inputs")
            index,archive_id,input_bytes=numeric_archive(cache,numbers)
            old_site=Path("old-site/spatial-preview");new_site=Path("refined-site/spatial-preview")
            old_c,_,old_id,old_bytes=validate_catalog(old_site,index)
            new_c,_,new_id,new_bytes=validate_catalog(new_site,index)
            require(old_id==new_id and new_c["render_sha"]==REFINED_SHA,"Chart provenance differs")
            report["checks"]["real_artifact_validation"]=True
            require(prefix=="wawp:validation/titan-spatial-storage/run-45743437475-1","Unexpected resume prefix")
            with patch.object(remote,"PREFIX",prefix):
                s=Store(prefix,work)
                initial_markers=s.markers();require(1<=len(initial_markers)<=3,"Expected interrupted fixture publication")
                report["resumed_from_run"]="45743437475";report["initial_objects"]=len(s.initial);report["initial_commits"]=len(initial_markers)
                report["stage"]="numeric_archive"
                numeric={};entries=[]
                for model in MODELS:
                    folder=cache/("titan-full-cache-"+model);m=load(folder/"manifest.json")
                    files={"manifest.json":folder/"manifest.json",**{e["file"]:folder/e["file"] for e in m["entries"]}}
                    p=pack(root/(model+".zip"),files);r=ref("numeric",p);s.put(r,p)
                    numeric[model]={"cycle":m["cycle"],"object":r,"members":members(files)}
                    entries.extend({"model":model,"cycle":m["cycle"],"lead":e["lead"],"sha256":e["sha256"]} for e in m["entries"])
                require(len(entries)==81,"Wrong number of archived entries")
                require(all(x["object"]["name"] in s.initial for x in numeric.values()),"Previously verified numeric package missing")
                require(s.uploaded==0,"Numeric archive was uploaded again")
                report["numeric_pack_bytes"]=sum(x["object"]["bytes"] for x in numeric.values())
                report["checks"]["all_numeric_packs_remote_readback"]=True
                def stage(site,c,label):
                    chart_files={e["image"]:site/e["image"] for e in c["entries"]}
                    meta_files={"catalog.json":site/"catalog.json",**{p.relative_to(site).as_posix():p for p in (site/"sources").glob("*/catalog.json")}}
                    require(len(meta_files)==7,"Wrong source manifest count")
                    chart=pack(root/(label+"-charts.zip"),chart_files);meta=pack(root/(label+"-meta.zip"),meta_files)
                    cr=ref("charts",chart);mr=ref("metadata",meta);s.put(cr,chart);s.put(mr,meta)
                    return {"schema":1,"mode":"frozen_research_preview","source_run":"45721468170","cache_code_sha":CACHE_SHA,"render_sha":c.get("render_sha",c["code_sha"]),"label":label,"numeric":numeric,"numeric_entries":entries,"chart_object":cr,"chart_members":members(chart_files),"metadata_object":mr,"metadata_members":members(meta_files)}
                def put_generation(g):
                    p=root/"generation.json";p.write_bytes(dump(g));r=ref("generation",p);s.put(r,p);return r
                report["stage"]="first_publication"
                old=stage(old_site,old_c,"original");g1=put_generation(old);s.publish(g1,1)
                report["stage"]="second_publication"
                new=stage(new_site,new_c,"refined");g2=put_generation(new);s.publish(g2,2)
                before=s.markers();require(len(before) in (2,3),"Unexpected publication count")
                report["checks"]["two_real_chart_revisions_verified"]=True
                # Staging new manifest without commit leaves previous public state intact.
                report["stage"]="invalid_candidate"
                bad={**new,"label":"deliberate-invalid","chart_object":{"name":"charts-"+"0"*64+".zip","sha256":"0"*64,"bytes":1}}
                badref=put_generation(bad)
                try:s.verify_gen(badref,deep=True)
                except ValueError as e:require("Incomplete generation object inventory" in str(e),"Unexpected invalid-candidate rejection")
                else:raise AssertionError("Incomplete generation accepted")
                require(s.markers()==before,"Invalid staging changed commit chain")
                report["checks"]["invalid_candidate_preserved_current"]=True
                report["stage"]="replay_publication"
                # Same forecast and chart bytes: third marker tests retention, not independent-cycle compression.
                nup=s.uploaded
                for x in numeric.values():s.put(x["object"],root/(next(k for k,v in numeric.items() if v is x)+".zip"))
                s.put(new["chart_object"],root/"refined-charts.zip");s.put(new["metadata_object"],root/"refined-meta.zip")
                require(s.uploaded==nup,"Identical content uploaded twice")
                g3=put_generation({**new,"label":"replay-same-frozen-fields"})
                require(s.markers()==before,"Staging altered committed state")
                s.publish(g3,3);saved=s.markers();s.publish(g3,3)
                require(s.markers()==saved and len(saved)==3,"Retry created duplicate marker")
                report["checks"]["staged_publication_and_retry_idempotent"]=True
                report["stage"]="fixture_chart_retention"
                previous=s.verify_gen(g2,deep=True);current=s.verify_gen(g3,deep=True)
                keep={previous["chart_object"]["name"],current["chart_object"]["name"]}
                retired=s.trash_old_chart(old["chart_object"],keep,{old["chart_object"]["name"],new["chart_object"]["name"]})
                for g in (g2,g3):s.verify_gen(g)
                inv=s.refresh()
                require(all(x["object"]["name"] in inv for x in numeric.values()) and all(r["name"] in inv for r in (old["metadata_object"],new["metadata_object"],g1,g2,g3,badref)),"Permanent archive objects disappeared")
                require(s.markers()==saved,"Chart retirement changed publication chain")
                report["checks"]["current_previous_verified_after_retirement"]=True
                report["checks"]["permanent_numeric_metadata_preserved"]=True
                report["inventory_calls"]=s.inventory_calls;report["direct_http_requests"]=remote.HTTP_REQUESTS;report["local_verified_cache_hits"]=s.cache_hits;report["numeric_reupload_bytes"]=0
                report.update({"numeric_entries":len(entries),"chart_entries_per_revision":len(new["chart_members"]),"original_chart_bytes":old_bytes,"refined_chart_bytes":new_bytes,"retired_fixture_chart_pack_bytes":retired,"active_remote_bytes":sum(x["size"] for x in inv.values()),"uploaded_bytes":s.uploaded,"deduplicated_retry_bytes":s.reused,"readback_bytes":s.read_bytes,"commit_count":len(saved),"active_chart_packs":sum(n.startswith("charts-") for n in inv),"stage":"complete","passed":True})
    except Exception as e:
        report["error"]=str(e)[:180] if type(e) is ValueError else type(e).__name__
    finally:
        Path("remote-spatial-storage-report.json").write_bytes(dump(report));print("REMOTE_SPATIAL_STORAGE",json.dumps(report),flush=True)
    if not report["passed"]:raise SystemExit("Remote spatial rehearsal incomplete; preserve all remaining objects")
if __name__=="__main__":main()
