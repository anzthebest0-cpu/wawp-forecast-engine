"""Private TITAN numerical archive transport. One exact folder inventory.
Existing objects verified by ID; new objects use a reserved ID and a single
resumable session. Never overwrite, delete, or blindly recreate an object."""
import json,os,re,subprocess,time
from pathlib import Path
import requests

PREFIX="wawp:spatial-numerical-v1"
FOLDER="spatial-numerical-v1"
API="https://www.googleapis.com/drive/v3"
UPLOAD="https://www.googleapis.com/upload/drive/v3"
ID=re.compile(r"[A-Za-z0-9_-]+")
NAME=re.compile(r"model-(gfs|ifs-025|ifs-010|aifs-025|icon)-[0-9a-f]{64}\.zip|generation-[0-9]+\.json")
RETRY={408,429,500,502,503,504}
DELAYS=(0,30,90,180,300)

def need(ok,msg):
    if not ok:raise ValueError(msg)

def reasons(r):
    try:return {x["reason"] for x in r.json().get("error",{}).get("errors",[]) if isinstance(x.get("reason"),str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}",x["reason"])}
    except (ValueError,TypeError,KeyError,AttributeError):return set()

def transient(r):
    return r.status_code in RETRY or r.status_code==403 and bool(reasons(r)&{"rateLimitExceeded","userRateLimitExceeded","backendError"})

def cli(*args):
    for delay in DELAYS[:4]:
        if delay:time.sleep(delay)
        p=subprocess.run(["rclone",*map(str,args),"--tpslimit","0.25","--tpslimit-burst","1","--retries","1","--low-level-retries","2"],capture_output=True,text=True,timeout=600)
        if p.returncode==0:return p.stdout
        s=p.stderr.lower()
        if not any(x in s for x in ("ratelimitexceeded","rate limit","requests per minute","429","quota metric")):raise ValueError("Drive CLI initialization failed")
        print("TITAN_DRIVE_RETRY",json.dumps({"operation":args[0],"delay":delay}),flush=True)
    raise ValueError("Drive initialization throttled")

def valid_id(s):return isinstance(s,str) and ID.fullmatch(s) is not None

class DriveArchive:
    def __init__(self):
        root=os.environ["WAWP_ARCHIVE_FOLDER_ID"];need(valid_id(root),"Invalid configured archive root")
        self.root=root;self.parent=None;self.last=0.;self.calls=0;self.inventory_calls=0;self.reserved=[]
        # rclone refreshes its OAuth token when necessary. Only resolve the
        # configured root's direct child, never another Drive folder.
        cli("mkdir",PREFIX)
        rows=json.loads(cli("lsjson","wawp:","--dirs-only","--no-modtime","--no-mimetype"))
        ids=[x.get("ID") for x in rows if x.get("Name")==FOLDER and x.get("IsDir") is True]
        need(len(ids)==1 and valid_id(ids[0]),"Archive folder is not unique")
        self.parent=ids[0]
        x=self.get(API+"/files/"+self.parent,{"fields":"id,name,mimeType,parents,trashed","supportsAllDrives":"true"})
        need(x["id"]==self.parent and x["name"]==FOLDER and x["mimeType"]=="application/vnd.google-apps.folder" and x.get("parents")==[self.root] and x.get("trashed") is False,"Archive folder identity differs")
    def headers(self):
        # Credentials remain process-local, never emit token or session URL.
        c=json.loads(subprocess.check_output(["rclone","config","dump"],stderr=subprocess.DEVNULL,text=True,timeout=30))["wawp"]
        need(c["type"]=="drive","Drive backend required")
        t=c["token"];t=json.loads(t) if isinstance(t,str) else t
        need(bool(t.get("access_token")),"OAuth token unavailable")
        return {"Authorization":"Bearer "+t["access_token"],"Accept-Encoding":"identity"}
    def pace(self):
        time.sleep(max(0,8-(time.monotonic()-self.last)))
        self.last=time.monotonic();self.calls+=1
    def retry_log(self,op,r,delay):
        print("TITAN_DRIVE_RETRY",json.dumps({"operation":op,"status":r.status_code,"reasons":sorted(reasons(r)),"delay":delay}),flush=True)
    def get(self,url,params,dest=None,size=None,allow_missing=False):
        for delay in DELAYS:
            if delay:time.sleep(delay)
            self.pace()
            if dest is not None and dest.exists():dest.unlink()
            try:
                with requests.get(url,headers=self.headers(),params=params,stream=dest is not None,timeout=(20,90),allow_redirects=False) as r:
                    if r.status_code==200:
                        if dest is None:return r.json()
                        count=0
                        with dest.open("xb") as f:
                            for block in r.iter_content(1024*1024):
                                count+=len(block);need(count<=size,"Drive payload exceeds expected size");f.write(block)
                        need(count==size,"Drive payload size differs");return
                    self.retry_log("read",r,delay)
                    if r.status_code==401:cli("lsjson",PREFIX,"--files-only","--no-modtime","--no-mimetype")
                    elif not (transient(r) or allow_missing and r.status_code==404):raise ValueError("Drive read rejected: "+str(r.status_code))
            except requests.RequestException:print("TITAN_DRIVE_TRANSPORT_RETRY",json.dumps({"operation":"read","delay":delay}),flush=True)
        raise ValueError("Drive exact-ID read budget exhausted")
    def inventory(self):
        self.inventory_calls+=1;a={}
        q={"q":"'"+self.parent+"' in parents and trashed = false","fields":"nextPageToken,files(id,name,size,parents,trashed,mimeType)","pageSize":100,"supportsAllDrives":"true","includeItemsFromAllDrives":"true"}
        while True:
            x=self.get(API+"/files",q)
            for f in x.get("files",[]):
                n=f["name"];need(NAME.fullmatch(n) and n not in a and valid_id(f.get("id")) and f.get("parents")==[self.parent] and f.get("trashed") is False and f.get("size") is not None,"Unexpected/duplicate archive object; no mutation permitted")
                a[n]={"id":f["id"],"size":int(f["size"])}
            if not x.get("nextPageToken"):return a
            q["pageToken"]=x["nextPageToken"]
    def metadata(self,name,info,allow_missing=False):
        need(NAME.fullmatch(name) and valid_id(info.get("id")),"Unsafe archive target")
        x=self.get(API+"/files/"+info["id"],{"fields":"id,name,size,parents,trashed,capabilities(canDownload)","supportsAllDrives":"true"},allow_missing=allow_missing)
        need(x["id"]==info["id"] and x["name"]==name and int(x["size"])==info["size"] and x.get("parents")==[self.parent] and x.get("trashed") is False and x.get("capabilities",{}).get("canDownload") is True,"Drive file identity differs")
    def read(self,name,info,dest,verified_metadata=False):
        if not verified_metadata:self.metadata(name,info)
        self.get(API+"/files/"+info["id"],{"alt":"media","supportsAllDrives":"true"},dest,info["size"])
    def reserve(self,count):
        need(0<count<=20,"Invalid reservation count")
        ids=self.get(API+"/files/generateIds",{"count":count,"space":"drive","type":"files"})["ids"]
        need(len(ids)==count and len(set(ids))==count and all(valid_id(i) for i in ids),"Invalid reserved IDs")
        self.reserved.extend(ids)
    def create(self,name,path):
        need(NAME.fullmatch(name),"Unsafe archive upload target")
        size=path.stat().st_size;need(size>0,"Empty archive object")
        if not self.reserved:self.reserve(1)
        fid=self.reserved.pop(0);info={"id":fid,"size":size}
        mime="application/zip" if name.endswith(".zip") else "application/json"
        params={"uploadType":"resumable","supportsAllDrives":"true","fields":"id,name,size,parents,trashed"}
        session=None
        for delay in DELAYS:
            if delay:time.sleep(delay)
            self.pace()
            with requests.post(UPLOAD+"/files",params=params,headers={**self.headers(),"X-Upload-Content-Type":mime,"X-Upload-Content-Length":str(size)},json={"id":fid,"name":name,"parents":[self.parent],"mimeType":mime},timeout=(20,60),allow_redirects=False) as r:
                if r.status_code in (200,201):session=r.headers.get("Location");break
                self.retry_log("initiate",r,delay)
                if not transient(r):raise ValueError("Drive initiation rejected: "+str(r.status_code))
        need(isinstance(session,str) and session.startswith("https://www.googleapis.com/"),"Missing trusted upload session")
        # No blind recreation. A retry probes the same reserved session, then
        # continues only from the server-confirmed byte offset. Full-object
        # success still requires exact-ID metadata and hash readback.
        offset=0
        for attempt,delay in enumerate(DELAYS):
            if delay:time.sleep(delay)
            self.pace()
            try:
                if attempt:
                    with requests.put(session,headers={**self.headers(),"Content-Length":"0","Content-Range":f"bytes */{size}"},data=b"",timeout=(20,60),allow_redirects=False) as r:
                        if r.status_code in (200,201):break
                        if r.status_code==308:
                            m=re.fullmatch(r"bytes=0-(\d+)",r.headers.get("Range",""))
                            offset=int(m.group(1))+1 if m else 0
                            need(0<=offset<size,"Invalid resumable offset")
                        elif transient(r):self.retry_log("session_probe",r,delay);continue
                        else:break # ambiguous/expired: verify the reserved ID
                    self.pace()
                with path.open("rb") as f:
                    f.seek(offset)
                    with requests.put(session,headers={**self.headers(),"Content-Type":mime,"Content-Length":str(size-offset),"Content-Range":f"bytes {offset}-{size-1}/{size}"},data=f,timeout=(30,300),allow_redirects=False) as r:
                        if r.status_code in (200,201):
                            x=r.json();need(x["id"]==fid and x["name"]==name and int(x["size"])==size and x.get("parents")==[self.parent] and x.get("trashed") is False,"Upload response identity differs");break
                        self.retry_log("media",r,delay)
                        if r.status_code!=308 and not transient(r):break
            except requests.RequestException:print("TITAN_DRIVE_TRANSPORT_RETRY",json.dumps({"operation":"media","delay":delay}),flush=True)
        self.metadata(name,info,allow_missing=True)
        return info
