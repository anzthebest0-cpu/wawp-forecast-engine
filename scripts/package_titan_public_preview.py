"""Public frozen research preview. Only approved catalogue and image bytes.
No numerical cache, provider credentials, source evidence or QC raw files."""
import hashlib,json,re,shutil,sys,zipfile,tempfile
from pathlib import Path
CAT="cd6906d90afaaf90796cea6470e402da8e321d11dac076b7fb53d33d1116ceff"
HEX=re.compile(r"[0-9a-f]{64}")
MODELS={"gfs","ifs-025","ifs-010","aifs-025","icon"}
def need(ok,msg):
    if not ok:raise ValueError(msg)
def digest(p):
    with p.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def inventory(root):
    p=root/"catalog.json";need(digest(p)==CAT,"Unexpected catalogue identity")
    c=json.loads(p.read_bytes());r=c["report"]
    need(r["passed"] and r["native_leads"]==81 and r["panels"]==3567 and len(c["products"])==25 and set(r["domains"])=={"regional","sulawesi","kolaka"} and set(r["sources"])==MODELS,"Incomplete research snapshot")
    need(r["selection"]["aifs-025"]["status"]=="advanced" and r["sources"]["aifs-025"]["cycle"]=="2026-11-26T00:00:00+00:00","Unexpected AIFS cycle")
    files={"catalog.json":p};seen=set()
    for g in c["comparisons"]:
        for e in g["panels"]:
            i=e["id"];n=e["path"]
            need(HEX.fullmatch(i) and HEX.fullmatch(e["sha256"]) and n=="panels/"+i+".webp" and i not in seen and e["model"] in MODELS,"Invalid panel path/identity")
            f=root/n;need(f.is_file() and not f.is_symlink() and f.stat().st_size==e["bytes"] and digest(f)==e["sha256"],"Panel hash mismatch")
            with f.open("rb") as b:h=b.read(12)
            need(h[:4]==b"RIFF" and h[8:]==b"WEBP","Unexpected image format")
            files[n]=f;seen.add(i)
    need(len(seen)==3567 and {f.name for f in (root/"panels").glob("*")}=={i+".webp" for i in seen},"Extra/missing panels")
    return c,files
def pack(source,dest):
    c,files=inventory(source)
    with zipfile.ZipFile(dest,"x",compression=zipfile.ZIP_STORED,allowZip64=True) as z:
        for n,p in sorted(files.items()):
            info=zipfile.ZipInfo(n,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_STORED
            with p.open("rb") as f,z.open(info,"w",force_zip64=True) as b:shutil.copyfileobj(f,b,1024*1024)
def unpack(archive,dest):
    with zipfile.ZipFile(archive) as z:
        names=z.namelist();need(len(names)==3568 and len(set(names))==len(names),"Unexpected ZIP inventory")
        need(sum(x.file_size for x in z.infolist())<300_000_000,"Snapshot size cap")
        for n in names:
            need(n=="catalog.json" or re.fullmatch(r"panels/[0-9a-f]{64}\.webp",n),"Unexpected ZIP member")
            f=dest/n;f.parent.mkdir(parents=True,exist_ok=True)
            with z.open(n) as src,f.open("xb") as out:shutil.copyfileobj(src,out,1024*1024)
def publish(archive,site):
    need(site.is_dir(),"Pages site does not exist")
    dest=site/"titan";need(not dest.exists(),"Refuse to replace existing TITAN path")
    with tempfile.TemporaryDirectory(dir=site.parent) as td:
        root=Path(td)/"titan";root.mkdir();unpack(archive,root);c,files=inventory(root)
        template=Path(__file__).with_name("titan_public_preview.html")
        (root/"index.html").write_bytes(template.read_bytes())
        (root/"catalog.js").write_text("window.TITAN_CATALOG="+json.dumps(c,ensure_ascii=True).replace("</","<\\/")+";",encoding="utf-8")
        need((root/"index.html").is_file() and len(list((root/"panels").glob("*.webp")))==3567,"Incomplete site")
        shutil.move(str(root),dest)
    print("TITAN_PUBLIC_PREVIEW_PASS",json.dumps({"source_run":45764124872,"catalog_sha256":CAT,"panels":3567,"products":25,"domains":3,"mode":"frozen_research_snapshot","site_path":"titan/"}),flush=True)
if __name__=="__main__":
    cmd,src,dest=sys.argv[1:]
    if cmd=="pack":pack(Path(src),Path(dest))
    elif cmd=="publish":publish(Path(src),Path(dest))
    else:raise ValueError("Unknown command")
