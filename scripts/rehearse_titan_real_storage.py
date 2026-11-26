"""Disposable real-artifact storage rehearsal. No remote writes or production deletion."""
import hashlib,json,os,re,shutil,tempfile,zipfile
from pathlib import Path
from datetime import datetime,timedelta
from collections import Counter
from PIL import Image
import numpy as np

MODELS=("gfs","ifs-025","ifs-010","aifs-025","icon")
CACHE_SHA="5ba074efa1010f1dbf50006da97b313b6ed8f6e2"
REFINED_SHA="314d815e2108ea639e05f86e1b39773488b6934b"
HEX=re.compile(r"[0-9a-f]{64}")
def require(ok,msg):
    if not ok: raise ValueError(msg)
def sha(p):
    with p.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def dump(x):return (json.dumps(x,sort_keys=True,separators=(",",":"))+"\n").encode()
def atomic_json(p,x):
    tmp=p.with_suffix(".tmp")
    with tmp.open("xb") as f:f.write(dump(x));f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)
def load(p):return json.loads(p.read_bytes())
def files_size(p):return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
def utc(s):
    t=datetime.fromisoformat(s);require(t.utcoffset()==timedelta(0),"Non-UTC time");return t
def put(root,p,expected):
    require(HEX.fullmatch(expected) and sha(p)==expected,"Source hash mismatch")
    out=root/expected
    if out.exists():require(sha(out)==expected,"Existing object corrupted")
    else:
        stage=root/(expected+".tmp")
        shutil.copyfile(p,stage);require(sha(stage)==expected,"Copy mismatch");os.replace(stage,out)
    return out
def store_bytes(root,b):
    h=hashlib.sha256(b).hexdigest();p=root/h
    if p.exists():require(p.read_bytes()==b,"Immutable metadata mismatch")
    else:
        with p.open("xb") as f:f.write(b)
    return h
def numeric_archive(source,root):
    index={};manifest_refs={};input_bytes=0
    for model in MODELS:
        folder=source/("titan-full-cache-"+model);mf=folder/"manifest.json";m=load(mf)
        require(m["model"]==model and m["passed"] is True and m["code_sha"]==CACHE_SHA,"Unvalidated cache")
        require([e["lead"] for e in m["entries"]]==m["expected_leads"],"Incomplete lead schedule")
        require(len(m["entries"])==(13 if model=="aifs-025" else 17),"Unexpected lead count")
        manifest_refs[model]=sha(mf);put(root,mf,manifest_refs[model]);input_bytes+=mf.stat().st_size
        for e in m["entries"]:
            name=e["file"];require(re.fullmatch(r"f\d{3}\.npz",name),"Unsafe cache path")
            p=folder/name;h=e["sha256"];put(root,p,h);input_bytes+=p.stat().st_size
            require(utc(m["cycle"])+timedelta(hours=e["lead"])==utc(e["valid_utc"]),"Invalid numeric valid time")
            shape=(round(15/e["dx"])+1,round(22/e["dx"])+1)
            with np.load(root/h,allow_pickle=False) as data:
                require(set(data.files)==set(e["products"]),"Array inventory mismatch")
                for key in data.files:
                    a=data[key];require(a.shape==shape and np.isfinite(a).any() and not np.isinf(a).any(),"Invalid field")
            key=(model,m["cycle"],e["lead"]);require(key not in index,"Duplicate cache identity");index[key]=e
        require({e["file"] for e in m["entries"]}=={f.name for f in folder.glob("*.npz")},"Unindexed cache")
    require(len(index)==81,"Missing numeric entries")
    archive={"schema":1,"source_run":"45721468170","cache_code_sha":CACHE_SHA,"model_manifests":manifest_refs}
    archive_id=store_bytes(root,dump(archive))
    return index,archive_id,input_bytes
def validate_catalog(site,index,charts=None):
    c=load(site/"catalog.json");require(c["schema"]==2 and c["mode"]=="research_preview" and c["run"]=="45721468170" and c["code_sha"]==CACHE_SHA,"Wrong catalog")
    require(len(c["entries"])==1563 and len(c["products"])==25,"Incomplete chart collection")
    refs=set();identity={};files=set();size=0
    for e in c["entries"]:
        key=(e["product"],e["domain"],e["valid_utc"],tuple(e["window_utc"]))
        require(key not in identity,"Duplicate chart identity")
        require(re.fullmatch(r"sources/[a-z]+/[A-Za-z0-9_.-]+\.webp",e["image"]),"Unsafe chart path")
        require(e["image"] not in files,"Duplicate path");files.add(e["image"])
        p=site/e["image"];h=e["sha256"];require(p.stat().st_size==e["bytes"] and sha(p)==h,"Chart integrity mismatch")
        with Image.open(p) as im:require(im.format=="WEBP","Wrong chart format");im.verify()
        for panel in e["models"]:
            n=index[(panel["model"],panel["init_utc"],panel["lead"])]
            require(n["sha256"]==panel["numeric_sha256"] and n["valid_utc"]==e["valid_utc"] and e["product"] in n["products"],"Numerical link mismatch")
            require(n["windows"].get(e["product"],[])==e["window_utc"],"Accumulation identity mismatch")
        require(bool(e["models"]),"No panels")
        if charts is not None:put(charts,p,h)
        refs.add(h);size+=e["bytes"];identity[key]=e["models"]
    require(files=={p.relative_to(site).as_posix() for p in site.rglob("*.webp")},"Unindexed charts")
    return c,refs,identity,size
def stage(root,site,index,archive_id,label):
    c,refs,identity,size=validate_catalog(site,index,root/"charts")
    # Preserve the exact catalog and original source manifests, but not every old image.
    meta=root/"metadata"
    catalog=sha(site/"catalog.json");put(meta,site/"catalog.json",catalog)
    sources={}
    for p in sorted((site/"sources").glob("*/catalog.json")):
        h=sha(p);put(meta,p,h);sources[p.parent.name]=h
    require(len(sources)==6,"Missing render manifests")
    record={"schema":1,"fixture":label,"numeric_archive":archive_id,"catalog":catalog,"source_manifests":sources,"charts":sorted(refs)}
    gen=store_bytes(meta,dump(record));return gen,identity,size
def verify_generation(root,gen):
    m=root/"metadata";p=m/gen;require(sha(p)==gen,"Bad generation hash")
    g=load(p);require(sha(root/"numbers"/g["numeric_archive"])==g["numeric_archive"],"Missing numeric archive")
    a=load(root/"numbers"/g["numeric_archive"])
    for h in a["model_manifests"].values():
        f=root/"numbers"/h;require(sha(f)==h,"Missing numerical manifest")
        for e in load(f)["entries"]:require(sha(root/"numbers"/e["sha256"])==e["sha256"],"Missing or corrupt numerical object")
    require(sha(m/g["catalog"])==g["catalog"],"Catalog mismatch")
    c=load(m/g["catalog"])
    require(set(g["charts"])=={e["sha256"] for e in c["entries"]},"Generation chart references differ")
    for h in g["source_manifests"].values():require(sha(m/h)==h,"Missing source manifest")
    for h in g["charts"]:require(sha(root/"charts"/h)==h,"Missing or corrupted chart")
    return g
def publish(root,gen):
    pointer=root/"current.json";old=load(pointer) if pointer.exists() else None
    verify_generation(root,gen)
    if old and old["current"]==gen:return old
    if old:verify_generation(root,old["current"])
    new={"mode":"disposable_rehearsal","current":gen,"previous":old["current"] if old else None}
    atomic_json(pointer,new);require(load(pointer)==new,"Pointer readback mismatch")
    return new
def prune_fixture_charts(root,registered):
    pointer=load(root/"current.json");keep=set()
    for gen in (pointer["current"],pointer["previous"]):
        if gen:keep.update(verify_generation(root,gen)["charts"])
    eligible=set()
    for gen in registered:
        g=load(root/"metadata"/gen);eligible.update(g["charts"])
    # The entire store was created in TemporaryDirectory; never accept a live path.
    removed=0
    for h in sorted(eligible-keep):
        require(HEX.fullmatch(h),"Unsafe chart identity")
        p=root/"charts"/h
        if p.exists():
            require(sha(p)==h,"Refuse to retire corrupt object");p.unlink();removed+=1
    return removed
def main():
    with tempfile.TemporaryDirectory(prefix="titan-disposable-") as d:
        root=Path(d)
        for n in ("numbers","metadata","charts"): (root/n).mkdir()
        index,archive_id,original_bytes=numeric_archive(Path("cache-inputs"),root/"numbers")
        numeric_bytes=files_size(root/"numbers");numeric_before={p.name:sha(p) for p in (root/"numbers").iterdir()}
        _,repeat_id,_=numeric_archive(Path("cache-inputs"),root/"numbers")
        require(repeat_id==archive_id and files_size(root/"numbers")==numeric_bytes,"Retry duplicated numbers")
        old,old_id,old_bytes=stage(root,Path("old-site/spatial-preview"),index,archive_id,"original-render")
        publish(root,old)
        refined_site=Path("refined-site/spatial-preview")
        require(load(refined_site/"catalog.json")["render_sha"]==REFINED_SHA,"Unexpected refined version")
        new,new_id,new_bytes=stage(root,refined_site,index,archive_id,"refined-render")
        require(old_id==new_id,"Meteorological identity changed")
        pointer=publish(root,new);two_bytes=files_size(root/"charts")
        require(publish(root,new)==pointer,"Retry changed pointer")
        # A third replay is a storage fixture, not another meteorological run.
        third,_,_=stage(root,refined_site,index,archive_id,"simulated-next-publication-same-fields")
        before=load(root/"current.json")
        # Interrupted staging leaves both existing generations available.
        require(before==pointer and verify_generation(root,old) and verify_generation(root,new),"Staging altered publication")
        # A deliberately incomplete candidate must never become current.
        bad=load(root/"metadata"/third);bad["charts"]=bad["charts"][1:]
        bad_id=store_bytes(root/"metadata",dump(bad))
        try:publish(root,bad_id)
        except ValueError:pass
        else:raise AssertionError("Incomplete publication accepted")
        require(load(root/"current.json")==before,"Failed publication changed pointer")
        # A candidate with a corrupt chart hash is rejected without mutation.
        bad=load(root/"metadata"/third);bad["catalog"]="0"*64
        bad_id=store_bytes(root/"metadata",dump(bad))
        try:publish(root,bad_id)
        except (ValueError,FileNotFoundError):pass
        else:raise AssertionError("Invalid catalog accepted")
        require(load(root/"current.json")==before,"Corrupt publication changed pointer")
        p=publish(root,third);require(p["previous"]==new and p["current"]==third,"Rollback pointer mismatch")
        removed=prune_fixture_charts(root,[old,new,third])
        require(verify_generation(root,new) and verify_generation(root,third),"Retained generation invalid")
        require(numeric_before=={p.name:sha(p) for p in (root/"numbers").iterdir()},"Numerical archive changed")
        # Reconstruct all original NPZ payloads byte-for-byte from the archive.
        restored=root/"restored";restored.mkdir()
        a=load(root/"numbers"/archive_id);restored_count=0
        for model,h in a["model_manifests"].items():
            folder=restored/model;folder.mkdir()
            shutil.copyfile(root/"numbers"/h,folder/"manifest.json")
            require(sha(folder/"manifest.json")==h,"Manifest restore mismatch")
            for e in load(folder/"manifest.json")["entries"]:
                dest=folder/e["file"];shutil.copyfile(root/"numbers"/e["sha256"],dest)
                require(sha(dest)==e["sha256"],"Numeric restore mismatch");restored_count+=1
        require(restored_count==81,"Incomplete restore")
        report={"passed":True,"mode":"local_disposable_only","numeric_entries":len(index),"restored_entries":restored_count,"numeric_archive_bytes":numeric_bytes,"original_numeric_input_bytes":original_bytes,"original_chart_bytes":old_bytes,"refined_chart_bytes":new_bytes,"two_real_revisions_chart_bytes":two_bytes,"two_retained_replay_chart_bytes":files_size(root/"charts"),"metadata_bytes":files_size(root/"metadata"),"retired_fixture_chart_objects":removed,"retry_deduplicated":True,"invalid_candidate_rejected":True,"interrupted_stage_preserved_pointer":True,"previous_generation_verified":True,"numeric_archive_unchanged":True,"notes":"Three publications are replay fixtures from one weather dataset; no new model cycles or remote writes."}
        Path("spatial-storage-report.json").write_bytes(dump(report));print("SPATIAL_STORAGE_PASS",json.dumps(report),flush=True)
if __name__=="__main__":main()
