"""Bounded Drive-ID reads and exact-object trash for the rolling checkpoint prefix only."""
from __future__ import annotations
import json,re,subprocess,time
from pathlib import Path
from urllib.parse import quote
import requests
from src.storage_incremental_archive import rclone_copy
from src.storage_snapshot import file_digest

PREFIX="wawp:checkpoints/rolling"
def need(ok,msg):
    if not ok:raise ValueError(msg)
def config():
    # Credentials stay inside this process; never log config or HTTP headers.
    return json.loads(subprocess.check_output(["rclone","config","dump"],stderr=subprocess.DEVNULL,timeout=30))["wawp"]
class RollingTransport:
    def __init__(self):
        self.kind=config()["type"]
        need(self.kind in ("drive","local"),"Unsupported rolling archive backend")
    def inventory(self):
        rows=json.loads(subprocess.check_output(["rclone","lsjson",PREFIX,"--files-only","--no-modtime","--no-mimetype"],text=True,timeout=120))
        names=[x["Name"] for x in rows];need(len(names)==len(set(names)),"Duplicate rolling names")
        out={x["Name"]:{"id":x.get("ID") if self.kind=="drive" else x["Name"],"size":x["Size"]} for x in rows}
        need(all(isinstance(x["id"],str) and x["id"] for x in out.values()),"Missing rolling object ID")
        return out
    def _headers(self):
        t=config()["token"]
        if isinstance(t,str):t=json.loads(t)
        need(bool(t.get("access_token")),"Missing Drive OAuth token")
        return {"Authorization":"Bearer "+t["access_token"],"Accept-Encoding":"identity"}
    def _url(self,info):
        fid=info["id"];need(re.fullmatch(r"[A-Za-z0-9_-]+",fid),"Invalid Drive object ID")
        return "https://www.googleapis.com/drive/v3/files/"+quote(fid,safe="")
    def _get(self,info,params,dest=None):
        last="unknown"
        for delay in (0,4,12,25):
            if delay:time.sleep(delay)
            if dest is not None and dest.exists():dest.unlink()
            try:
                with requests.get(self._url(info),params=params,headers=self._headers(),stream=dest is not None,timeout=(20,60),allow_redirects=False) as resp:
                    if resp.status_code==200:
                        if dest is None:return resp.json()
                        count=0
                        with dest.open("xb") as f:
                            for chunk in resp.iter_content(1024*1024):
                                count+=len(chunk);need(count<=info["size"],"Oversized Drive payload");f.write(chunk)
                        need(count==info["size"],"Drive size mismatch");return
                    last=str(resp.status_code)
                    if resp.status_code==401:self.inventory()
                    elif resp.status_code not in (403,404,408,429,500,502,503,504):break
            except requests.RequestException as e:last=type(e).__name__
        raise ValueError("Drive ID readback failed: "+last)
    def metadata(self,name,info,*,trashed=False):
        if self.kind=="local":
            if trashed:
                p="wawp:checkpoints/retention-test-trash"
                rows=json.loads(subprocess.check_output(["rclone","lsjson",p,"--files-only"],text=True,timeout=60))
                need(any(x["Name"]==name and x["Size"]==info["size"] for x in rows),"Fixture trash not confirmed")
            else:need(self.inventory().get(name)==info,"Local object changed")
            return
        data=self._get(info,{"supportsAllDrives":"true","fields":"id,name,size,trashed,capabilities(canDownload)"})
        need(data.get("id")==info["id"] and data.get("name")==name and int(data.get("size",-1))==info["size"] and data.get("trashed") is trashed,"Drive metadata identity differs")
        if not trashed:need(data.get("capabilities",{}).get("canDownload") is True,"Drive download not permitted")
    def read(self,name,info,dest):
        need(Path(name).name==name,"Unsafe rolling name")
        self.metadata(name,info)
        if self.kind=="local":rclone_copy(PREFIX+"/"+name,str(dest))
        else:self._get(info,{"supportsAllDrives":"true","alt":"media"},dest)
        need(dest.stat().st_size==info["size"],"Rolling payload size mismatch")
    def read_json(self,name,info,work):
        p=work/name
        if p.exists():p.unlink()
        self.read(name,info,p)
        return json.loads(p.read_bytes())
    def put_json(self,name,value,work):
        need(Path(name).name==name,"Unsafe record name")
        p=work/("upload-"+name)
        p.write_text(json.dumps(value,sort_keys=True,indent=2)+"\n")
        inv=self.inventory()
        if name not in inv:rclone_copy(str(p),PREFIX+"/"+name)
        inv=self.inventory();need(name in inv,"Record upload missing")
        back=work/("readback-"+name)
        if back.exists():back.unlink()
        self.read(name,inv[name],back)
        # Existing immutable records may have different JSON whitespace only.
        need(json.loads(back.read_bytes())==value,"Existing immutable record differs")
        return inv[name]
    def trash(self,name,info):
        need(re.fullmatch(r"wawp_forecasts-\d+-\d+\.db\.zst",name),"Unsafe trash scope")
        self.metadata(name,info)
        if self.kind=="local":
            # Only the local fixture transport simulates Trash.
            subprocess.run(["rclone","moveto",PREFIX+"/"+name,"wawp:checkpoints/retention-test-trash/"+name],check=True,timeout=60)
        else:
            # One mutation attempt only; ambiguous responses are reconciled via journal.
            with requests.patch(self._url(info),params={"supportsAllDrives":"true","fields":"id,name,size,trashed"},headers=self._headers(),json={"trashed":True},timeout=(20,60),allow_redirects=False) as resp:
                need(resp.status_code==200,"Drive trash request not confirmed")
                data=resp.json()
                need(data.get("id")==info["id"] and data.get("name")==name and int(data.get("size",-1))==info["size"] and data.get("trashed") is True,"Drive trash response differs")
        self.metadata(name,info,trashed=True)
        need(name not in self.inventory(),"Trash still active")

def replicate_rolling(archive,work):
    """Publish payload then manifest, read each by exact object ID, verify bytes."""
    from src.storage_snapshot import manifest_path
    t=RollingTransport()
    readback=work/"readback";readback.mkdir()
    for p in (archive,manifest_path(archive)):
        inv=t.inventory()
        if p.name not in inv:rclone_copy(str(p),PREFIX+"/"+p.name)
        inv=t.inventory()
        need(p.name in inv and inv[p.name]["size"]==p.stat().st_size,"Rolling upload inventory mismatch")
        back=readback/p.name;t.read(p.name,inv[p.name],back)
        need(file_digest(back)==file_digest(p),"Rolling upload readback differs")
    return readback/archive.name
