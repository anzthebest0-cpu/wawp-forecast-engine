"""Exact-folder Drive recovery transport: bounded paced resumable uploads.
No overwrite, no delete, no arbitrary target. Existing files read by exact ID."""
import json,re,time
from pathlib import Path
import requests
from src import titan_fixture_drive_transport as remote
PARENT_PATH="wawp:validation/titan-spatial-storage"
FOLDER_NAME="run-45767345982-1"
PREFIX=PARENT_PATH+"/"+FOLDER_NAME
API="https://www.googleapis.com/drive/v3"
UPLOAD="https://www.googleapis.com/upload/drive/v3"
RETRY={"rateLimitExceeded","userRateLimitExceeded","backendError"}
def need(ok,msg):
    if not ok:raise ValueError(msg)
def why(resp):
    try:return {x.get("reason") for x in resp.json().get("error",{}).get("errors",[]) if isinstance(x.get("reason"),str)}
    except (ValueError,TypeError,AttributeError):return set()
def retryable(resp):return resp.status_code in (429,500,502,503,504) or resp.status_code==403 and bool(why(resp)&RETRY)
class ExactFolder:
    def __init__(self):
        need(remote.PREFIX==PREFIX,"Recovery prefix not authorized")
        self.t=remote.RollingTransport();need(self.t.kind=="drive","Drive required")
        # One path-based read establishes the exact isolated folder, not the root.
        rows=json.loads(remote.retry_cli(["rclone","lsjson",PARENT_PATH,"--dirs-only","--no-modtime","--no-mimetype","--tpslimit","0.25","--tpslimit-burst","1","--retries","1","--low-level-retries","2"]))
        found=[x for x in rows if x.get("Name")==FOLDER_NAME and x.get("IsDir") is True]
        need(len(found)==1 and re.fullmatch(r"[A-Za-z0-9_-]+",found[0].get("ID","")),"Cannot establish unique exact recovery folder")
        self.parent=found[0]["ID"];self.calls=0;self.reserved=[];self.list_calls=0
        x=self.get(API+"/files/"+self.parent,{"fields":"id,name,mimeType,trashed","supportsAllDrives":"true"})
        need(x["id"]==self.parent and x["name"]==FOLDER_NAME and x["mimeType"]=="application/vnd.google-apps.folder" and x["trashed"] is False,"Folder identity differs")
    def pace(self):
        # Shared API requests remain sequential, with extra spacing for uploads.
        time.sleep(8);remote.pace();self.calls+=1
    def get(self,url,params,*,allow_missing=False):
        kind="list" if url==API+"/files" else "reserve" if url.endswith("/generateIds") else "exact_id"
        for delay in (0,30,90,180,300):
            if delay:time.sleep(delay)
            self.pace()
            try:
                with requests.get(url,params=params,headers=self.t._headers(),timeout=(20,60),allow_redirects=False) as r:
                    if r.status_code==200:return r.json()
                    status=r.status_code
                    print("DRIVE_GET_RETRY",json.dumps({"kind":kind,"status":status,"reasons":sorted(x for x in why(r) if re.fullmatch(r"[A-Za-z0-9_]{1,80}",x)),"backoff_seconds":delay}),flush=True)
                    if status==404 and allow_missing:continue
                    if status==401:self.t.inventory()
                    elif not retryable(r):raise ValueError("Drive "+kind+" GET failed: "+str(status))
            except requests.RequestException:
                print("DRIVE_GET_TRANSPORT_RETRY",json.dumps({"kind":kind}),flush=True)
        raise ValueError("Drive "+kind+" GET retry budget exhausted")
    def reserve(self,count):
        if count<=0:return
        need(count<=100 and not self.reserved,"Invalid ID reservation count")
        ids=self.get(API+"/files/generateIds",{"count":count,"space":"drive","type":"files"})["ids"]
        need(len(ids)==count and len(set(ids))==count and all(re.fullmatch(r"[A-Za-z0-9_-]+",x) for x in ids),"Invalid reserved IDs")
        self.reserved=list(ids)
    def verify_created(self,fid,name,size):
        # No folder-wide listing after a write, even if the PUT was ambiguous.
        x=self.get(API+"/files/"+fid,{"fields":"id,name,size,parents,trashed","supportsAllDrives":"true"},allow_missing=True)
        need(x["id"]==fid and x["name"]==name and int(x["size"])==size and x.get("parents")==[self.parent] and x.get("trashed") is False,"Created object identity differs")
        return {"id":fid,"size":size}
    def inventory(self):
        self.list_calls+=1
        q={"q":"'"+self.parent+"' in parents and trashed = false","fields":"nextPageToken,files(id,name,size,parents,mimeType,trashed)","pageSize":100,"supportsAllDrives":"true","includeItemsFromAllDrives":"true"}
        result={}
        while True:
            x=self.get(API+"/files",q)
            for f in x.get("files",[]):
                n=f["name"];need(n not in result and f.get("parents")==[self.parent] and f.get("trashed") is False and f.get("size") is not None and re.fullmatch(r"[A-Za-z0-9_-]+",f["id"]),"Unknown/duplicate non-file object in fixture")
                result[n]={"id":f["id"],"size":int(f["size"])}
            if not x.get("nextPageToken"):return result
            q["pageToken"]=x["nextPageToken"]
    def create(self,name,path):
        # Generate ID first: a fixed identity makes ambiguous creation recoverable.
        need(re.fullmatch(r"(numeric|charts|metadata|evidence)-[0-9a-f]{64}\.zip|generation-[0-9a-f]{64}\.json|commit-00000[12]\.json",name),"Unsafe upload target")
        size=path.stat().st_size;need(size>0,"Empty payload")
        if not self.reserved:self.reserve(1)
        fid=self.reserved.pop(0)
        params={"uploadType":"resumable","supportsAllDrives":"true","fields":"id,name,size,parents,trashed"}
        mime="application/zip" if name.endswith(".zip") else "application/json"
        headers={**self.t._headers(),"X-Upload-Content-Type":mime,"X-Upload-Content-Length":str(size)}
        session=None
        # Retry initiation only on explicit throttle rejection; ambiguous
        # responses stop safely and a later recovery inspects the folder.
        for delay in (0,30,90,180,300):
            if delay:time.sleep(delay)
            self.pace()
            with requests.post(UPLOAD+"/files",params=params,headers=headers,json={"id":fid,"name":name,"parents":[self.parent],"mimeType":mime},timeout=(20,60),allow_redirects=False) as r:
                if r.status_code in (200,201):session=r.headers.get("Location");break
                if r.status_code==401:self.t.inventory();headers.update(self.t._headers())
                elif r.status_code==429 or r.status_code==403 and bool(why(r)&RETRY):
                    print("DRIVE_UPLOAD_THROTTLE",json.dumps({"status":r.status_code,"backoff_seconds":delay}),flush=True)
                else:raise ValueError("Drive upload initiation refused: "+str(r.status_code))
        need(isinstance(session,str) and session.startswith("https://www.googleapis.com/"),"No trusted resumable session")
        # One streaming media PUT; never blindly recreate on ambiguous completion.
        self.pace()
        try:
            with path.open("rb") as f,requests.put(session,headers={**self.t._headers(),"Content-Type":mime,"Content-Length":str(size),"Content-Range":f"bytes 0-{size-1}/{size}"},data=f,timeout=(30,300),allow_redirects=False) as r:
                status=r.status_code
                if status in (200,201):
                    x=r.json();need(x["id"]==fid and x["name"]==name and int(x["size"])==size and x.get("parents")==[self.parent] and x.get("trashed") is False,"Upload response identity differs")
                else:print("UPLOAD_MEDIA_STATUS",json.dumps({"name":name,"status":status}),flush=True)
        except requests.RequestException:status=0
        # Direct file-ID metadata read confirms both definite and ambiguous PUT.
        # If absent after bounded retries, stop; do not allocate a replacement.
        return self.verify_created(fid,name,size)
