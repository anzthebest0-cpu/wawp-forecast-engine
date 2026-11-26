"""Read-only targeted Drive ID recovery; never changes remote objects."""
import json,re,subprocess,tempfile,time,hashlib
from pathlib import Path
from urllib.parse import quote
import requests
from src import checkpoint_retention as r
from src.storage_snapshot import restore,select_asset,file_digest
from rehearse_checkpoint_independent_proof import github_pair,matches,need

REPO="anzthebest0-cpu/wawp-forecast-engine"
RELEASE=351035767
TARGET="wawp_forecasts-45561446523-2.db.zst"
LATEST="wawp_forecasts-45581150608-1.db.zst"
def inventory():
    rows=json.loads(r.run("rclone","lsjson",r.PREFIX,"--files-only","--no-modtime","--no-mimetype"))
    names=[x["Name"] for x in rows]
    need(len(names)==len(set(names)),"Duplicate Drive names")
    records={x["Name"]:{"id":x.get("ID"),"size":x["Size"]} for x in rows}
    return records
def stable_release_inventory(assets):
    # Download counters, uploader profile and display labels are not object identity.
    # The fields match the existing checkpoint registration contract.
    return {name:r.identity(asset) for name,asset in assets.items()}

def inventory_diff(before,after):
    return {"added":sorted(set(after)-set(before)),
            "removed":sorted(set(before)-set(after)),
            "changed":{name:sorted(k for k in set(before[name])|set(after[name])
                                   if before[name].get(k)!=after[name].get(k))
                       for name in sorted(set(before)&set(after))
                       if before[name]!=after[name]}}

def fingerprint(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def token():
    # lsjson refreshes an expired rclone token; read locally, never log credentials.
    cfg=json.loads(subprocess.check_output(["rclone","config","dump"],stderr=subprocess.DEVNULL,timeout=30))
    t=cfg["wawp"]["token"]
    if isinstance(t,str):t=json.loads(t)
    need(isinstance(t.get("access_token"),str) and bool(t["access_token"]),"No OAuth access token")
    return t["access_token"]
def reason(resp):
    try:
        data=resp.json()["error"]
        codes=[str(x.get("reason","")) for x in data.get("errors",[])]
        code=next((x for x in codes if re.fullmatch(r"[A-Za-z0-9_]{1,80}",x)),"unspecified")
    except Exception:code="unspecified"
    return {"http":resp.status_code,"reason":code}
def fetch_id(name,info,dest,attempts,media=True):
    fid=info["id"];need(isinstance(fid,str) and re.fullmatch(r"[A-Za-z0-9_-]+",fid),"Drive file ID absent or invalid")
    params={"supportsAllDrives":"true","alt":"media"} if media else {"supportsAllDrives":"true","fields":"id,name,size,trashed,mimeType,capabilities(canDownload)"}
    url="https://www.googleapis.com/drive/v3/files/"+quote(fid,safe="")
    for i,delay in enumerate((0,4,12,25)):
        if delay:time.sleep(delay)
        if dest.exists():dest.unlink()
        try:
            with requests.get(url,params=params,headers={"Authorization":"Bearer "+token(),"Accept-Encoding":"identity"},stream=media,timeout=(20,60),allow_redirects=False) as resp:
                if resp.status_code!=200:
                    entry=reason(resp);attempts.append(entry)
                    if resp.status_code==401:
                        # Ask rclone to refresh its local token before retrying.
                        inventory()
                    elif resp.status_code not in (403,404,408,429,500,502,503,504):break
                    continue
                if not media:
                    data=resp.json()
                    need(data.get("id")==fid and data.get("name")==name and int(data.get("size",-1))==info["size"] and data.get("trashed") is False,"Drive ID metadata differs from inventory")
                    need(data.get("capabilities",{}).get("canDownload") is True,"Drive reports download unavailable")
                    attempts.append({"http":200,"metadata_verified":True})
                    return
                count=0
                with dest.open("xb") as f:
                    for chunk in resp.iter_content(1024*1024):
                        count+=len(chunk)
                        need(count<=info["size"],"Drive payload larger than registered size")
                        f.write(chunk)
                need(count==info["size"],"Drive payload size mismatch")
                attempts.append({"http":200,"bytes":count})
                return
        except requests.RequestException as e:
            attempts.append({"transport":type(e).__name__})
    raise ValueError("Drive ID readback did not succeed")
def main():
    report={"mode":"read_only","target":TARGET,"passed":False,"stage":"prior_proof","files":{}}
    try:
        prior=json.loads(Path("prior-proof/checkpoint-recovery-proof.json").read_text())
        need(prior["mode"]=="read_only" and prior["stage"]=="complete" and prior["latest"]==LATEST and prior["managed"]==6 and prior["github_redundancy_proven"] is True,"Prior proof not established")
        entries=prior["retained"]+prior["candidates"]
        expected={x["name"] for x in entries}
        need(len(expected)==6 and TARGET in expected and sum(x["drive"]["verified"] for x in entries)==5,"Unexpected prior recovery scope")
        need(next(x for x in entries if x["name"]==TARGET)["drive"]["verified"] is False,"Target is not the unverified copy")
        report["prior_proof_run"]="45733820182"
        report["stage"]="inventory"
        inv=inventory()
        pages=r.release_assets(REPO,RELEASE);assets=r.indexed(pages)
        need(select_asset(pages)==LATEST,"Newest checkpoint changed")
        need({n for n in inv if r.name_allowed(n)}==expected,"Managed checkpoint set changed")
        report["inventory_fingerprint_before"]=fingerprint(inv)
        stable_assets=stable_release_inventory(assets)
        report["release_fingerprint_before"]=fingerprint(stable_assets)
        # Previous report does not contain exact inventory IDs; use current start/end
        # comparison and match all six registered release identities, not an invented
        # historical fingerprint.
        report["prior_scope_matches"]=True
        with tempfile.TemporaryDirectory(prefix="titan-target-readonly-") as td:
            root=Path(td);registrations={}
            for name in sorted(expected):
                report["stage"]="registration:"+name
                n=name+".registered.json"
                need(n in inv and name+".retired.json" not in inv,"Missing active registration")
                p=root/n
                attempts=[]
                fetch_id(n,inv[n],p,attempts)
                rec=json.loads(p.read_bytes())
                need(rec["version"]==1 and rec["data"]["name"]==name and rec["manifest"]["name"]==name+".json","Invalid registration")
                for k in ("data","manifest"):
                    m=rec[k]
                    need(m["name"] in assets and r.identity(assets[m["name"]])==m and inv[m["name"]]["size"]==m["size"],"Registered identity differs")
                registrations[name]=rec
            report["registered"]=len(registrations)
            rec=registrations[TARGET]
            report["stage"]="github_target_restore"
            githubdb=github_pair(root/"github",rec)
            report["github_restored"]=True
            folder=root/"drive";folder.mkdir()
            for key in ("data","manifest"):
                m=rec[key];n=m["name"];p=folder/n
                report["stage"]="drive_id:"+key
                item={"metadata_attempts":[],"download_attempts":[],"verified":False}
                report["files"][key]=item
                fetch_id(n,inv[n],folder/("metadata-"+key),item["metadata_attempts"],media=False)
                fetch_id(n,inv[n],p,item["download_attempts"])
                need(matches(p,m),"Target Drive digest differs from registration")
                need(file_digest(p)==file_digest(root/"github"/n),"Target Drive bytes differ from GitHub")
                item["verified"]=True
            report["stage"]="restore_drive"
            db=folder/"restored.db"
            restore(folder/TARGET,db)
            need(file_digest(db)==file_digest(githubdb),"Restored databases differ")
            report["drive_restored"]=True
            report["stage"]="inventory_recheck"
            final=inventory();final_pages=r.release_assets(REPO,RELEASE);final_assets=r.indexed(final_pages)
            stable_final=stable_release_inventory(final_assets)
            report["drive_inventory_diff"]=inventory_diff(inv,final)
            report["release_identity_diff"]=inventory_diff(stable_assets,stable_final)
            report["inventory_fingerprint_after"]=fingerprint(final)
            report["release_fingerprint_after"]=fingerprint(stable_final)
            report["download_counter_changes"]=sum(assets[n].get("download_count")!=final_assets[n].get("download_count") for n in set(assets)&set(final_assets))
            need(select_asset(final_pages)==LATEST,"Selected checkpoint changed during targeted proof")
            need(final==inv,"Drive object inventory changed during targeted proof")
            need(stable_final==stable_assets,"GitHub release asset identity changed during targeted proof")
            report["inventory_unchanged_during_test"]=True
            report["stage"]="complete";report["passed"]=True
    except Exception as e:
        # Never print HTTP headers, URLs with credentials, raw provider responses or config.
        report["error"]=str(e)[:180] if isinstance(e,ValueError) and type(e) is ValueError else type(e).__name__
    finally:
        Path("targeted-checkpoint-report.json").write_text(json.dumps(report,indent=2))
        print("TARGETED_CHECKPOINT",json.dumps(report),flush=True)
    if not report["passed"]:raise SystemExit("Targeted proof incomplete; retain every checkpoint")
if __name__=="__main__":main()
