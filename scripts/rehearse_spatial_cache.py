"""Local, isolated spatial cache rehearsal using an existing historical V3 artifact.
No live collection, remote writes, production paths or real archive deletion.
"""
import hashlib,json,os,re,shutil,tempfile
from pathlib import Path
from PIL import Image,ImageChops
ROOT=Path("spatial-cache-rehearsal")
SOURCE=Path("review-inputs")
def digest(p):return hashlib.file_digest(p.open("rb"),"sha256").hexdigest()
def write_json(p,o):p.write_text(json.dumps(o,indent=2,sort_keys=True)+"\n")
def load(p):return json.loads(p.read_text())
def convert(source,dest):
    c=load(source/"catalog.json")
    if c["cycle"]!="2026-11-25 00 UTC" or len(c["entries"])!=216:raise ValueError("Unexpected source cycle")
    keys=set()
    for e in c["entries"]:
        key=(e["product"],e["domain"],e["lead"])
        if key in keys:raise ValueError("Duplicate key")
        keys.add(key)
        p=e["image"]
        if not re.fullmatch(r"sources/[a-z]+/[A-Za-z0-9_.-]+\.png",p):raise ValueError("Bad image path")
        old=source/p;new=dest/Path(p).with_suffix(".webp")
        new.parent.mkdir(parents=True,exist_ok=True)
        with Image.open(old) as im:
            rgba=im.convert("RGBA")
            if rgba.getchannel("A").getextrema()!=(255,255):raise ValueError("Unexpected transparency")
            rgb=rgba.convert("RGB")
            rgb.save(new,format="WEBP",lossless=True,method=6)
            with Image.open(new) as out:
                if out.size!=rgb.size or ImageChops.difference(rgb,out.convert("RGB")).getbbox() is not None:raise ValueError("Pixel mismatch")
        e["original_png_bytes"]=old.stat().st_size;e["image"]=new.relative_to(dest).as_posix();e["sha256"]=digest(new);e["bytes"]=new.stat().st_size
    for p in (source/"sources").glob("*/manifest.json"):
        target=dest/p.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    c["mode"]="historical_rehearsal";write_json(dest/"catalog.json",c)
    return c
def validate(root):
    c=load(root/"catalog.json")
    if c["mode"]!="historical_rehearsal" or len(c["entries"])!=216 or c["cycle"]!="2026-11-25 00 UTC":raise ValueError("Bad catalog")
    if len({(e["product"],e["domain"],e["lead"]) for e in c["entries"]})!=216:raise ValueError("Duplicate key")
    for e in c["entries"]:
        if not re.fullmatch(r"sources/[a-z]+/[A-Za-z0-9_.-]+\.webp",e["image"]):raise ValueError("Bad path")
        p=root/e["image"]
        if p.stat().st_size!=e["bytes"] or digest(p)!=e["sha256"]:raise ValueError("Bad image digest")
        with Image.open(p) as im:im.verify()
    return c
def publish(root,stage,gen):
    if not re.fullmatch(r"fixture-\d{3}",gen):raise ValueError("Not a rehearsal generation")
    validate(stage)
    current=root/"current.json"
    prev=load(current)["current"] if current.exists() else None
    if prev:validate(root/prev)
    target=root/gen
    if target.exists():raise ValueError("Existing immutable generation")
    stage.rename(target)
    # Test scope only: atomic local pointer switch after complete validation.
    pointer={"current":gen,"previous":prev,"mode":"historical_rehearsal","catalog_sha256":digest(target/"catalog.json")}
    tmp=root/"pointer.tmp";write_json(tmp,pointer);os.replace(tmp,current)
    if load(current)!=pointer:raise ValueError("Pointer readback mismatch")
    # Only remove generations created in this disposable rehearsal.
    for p in root.iterdir():
        if p.is_dir() and re.fullmatch(r"fixture-\d{3}",p.name) and p.name not in (gen,prev):
            shutil.rmtree(p)
    return pointer
def main():
    ROOT.mkdir(exist_ok=False)
    with tempfile.TemporaryDirectory(dir=ROOT) as td:
        cache=Path(td)
        canonical=cache/"canonical";canonical.mkdir()
        c=convert(SOURCE,canonical);validate(canonical)
        # Simulated generations reuse the exact same frozen historical data;
        # generation IDs are not model run times.
        for i in range(1,4):
            stage=cache/"stage";shutil.copytree(canonical,stage)
            publish(cache,stage,f"fixture-{i:03d}")
        before=load(cache/"current.json")
        assert before["current"]=="fixture-003" and before["previous"]=="fixture-002"
        assert not (cache/"fixture-001").exists()
        stage=cache/"stage";shutil.copytree(canonical,stage)
        damaged=stage/c["entries"][0]["image"]
        with damaged.open("ab") as f:f.write(b"corrupt")
        try:publish(cache,stage,"fixture-004")
        except ValueError:pass
        else:raise AssertionError("Corrupt candidate accepted")
        assert load(cache/"current.json")==before
        validate(cache/before["current"]);validate(cache/before["previous"])
        shutil.rmtree(stage)
        # Publish one frozen preview artifact for browser integration testing.
        shutil.copytree(canonical,ROOT/"preview")
        report={"mode":"historical_rehearsal","images":len(c["entries"]),"products":len({e["product"] for e in c["entries"]}),"png_bytes":sum(e["original_png_bytes"] for e in c["entries"]),"webp_bytes":sum(e["bytes"] for e in c["entries"]),"pixel_identical":216,"two_generation_bound":True,"corrupt_candidate_rejected":True,"pointer_preserved":True,"previous_validated":True,"source_cycle":c["cycle"]}
        write_json(ROOT/"report.json",report);print("SPATIAL_CACHE_PASS",json.dumps(report),flush=True)
if __name__=="__main__":main()
