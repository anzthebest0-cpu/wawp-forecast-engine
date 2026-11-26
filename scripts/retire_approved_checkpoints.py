"""One-time approved retirement of three explicitly named redundant managed checkpoints.
Stop on any discrepancy. No permanent Drive deletion, no original/pinned backup changes."""
import json,re,subprocess,tempfile,time
from pathlib import Path
from urllib.parse import quote
import requests
from src import checkpoint_retention as r
from src.storage_snapshot import restore,select_asset,file_digest
from rehearse_checkpoint_independent_proof import github_pair,matches,need
from rehearse_targeted_checkpoint_readback import inventory,fingerprint,stable_release_inventory,inventory_diff,fetch_id,token,reason,REPO,RELEASE,LATEST
KEEP=("wawp_forecasts-45581150608-1.db.zst","wawp_forecasts-45561446523-2.db.zst","wawp_forecasts-45503228434-1.db.zst")
RETIRE=("wawp_forecasts-45373414819-1.db.zst","wawp_forecasts-45294080650-1.db.zst","wawp_forecasts-45283393336-1.db.zst")
IDS={
 RETIRE[0]:(786706963,786707242),
 RETIRE[1]:(785042681,785042868),
 RETIRE[2]:(784853993,784854454),
 KEEP[0]:(790838950,790839240),
 KEEP[1]:(790423633,790424101),
 KEEP[2]:(789156327,789156550),
}
EXPECTED=set(KEEP+RETIRE)

def check_scope(inv,assets,records):
    need({n for n in inv if r.name_allowed(n)}==EXPECTED,"Managed Drive scope changed")
    need(set(records)==EXPECTED,"Registration scope changed")
    for n in EXPECTED:
        rec=records[n]
        need(n not in r.PINNED and r.name_allowed(n),"Pinned or invalid managed name")
        need(rec["version"]==1 and rec["data"]["name"]==n and rec["manifest"]["name"]==n+".json","Invalid registration")
        need((rec["data"]["id"],rec["manifest"]["id"])==IDS[n],"Approved release IDs differ")
        for k in ("data","manifest"):
            m=rec[k]
            need(m["name"] in assets and r.identity(assets[m["name"]])==m and inv[m["name"]]["size"]==m["size"],"Registered identity differs")
        need(n+".retired.json" not in inv,"Already retired or partial previous run")
    need(tuple(r.eligible(records,LATEST))==RETIRE,"Retirement candidates differ from approved scope")

def read_inventory():
    pages=r.release_assets(REPO,RELEASE)
    need(select_asset(pages)==LATEST,"Selected latest checkpoint changed")
    return inventory(),stable_release_inventory(r.indexed(pages)),r.indexed(pages)

def assert_inventory(expected_drive,expected_release):
    d,g,_=read_inventory()
    need(d==expected_drive,"Drive inventory changed")
    need(g==expected_release,"GitHub release identity changed")

def drive_pair(folder,rec,inv):
    folder.mkdir()
    for k in ("data","manifest"):
        m=rec[k];n=m["name"];p=folder/n
        fetch_id(n,inv[n],folder/("metadata-"+k),[],media=False)
        fetch_id(n,inv[n],p,[])
        need(matches(p,m),"Drive registered checksum mismatch")
    db=folder/"restored.db"
    restore(folder/rec["data"]["name"],db)
    return db

def trash_exact(name,info):
    # Restrict the only destructive Drive request to the exact allowlisted payload ID.
    need(name in RETIRE,"Refuse to trash non-approved payload")
    fid=info["id"]
    need(isinstance(fid,str) and re.fullmatch(r"[A-Za-z0-9_-]+",fid),"Invalid Drive ID")
    fetch_id(name,info,Path(tempfile.gettempdir())/("unused-metadata-"+name),[],media=False)
    url="https://www.googleapis.com/drive/v3/files/"+quote(fid,safe="")
    headers={"Authorization":"Bearer "+token(),"Accept-Encoding":"identity"}
    params={"supportsAllDrives":"true","fields":"id,name,size,trashed"}
    # A failed or uncertain PATCH stops the workflow; do not blindly repeat mutation.
    with requests.patch(url,params=params,headers=headers,json={"trashed":True},timeout=(20,60),allow_redirects=False) as resp:
        if resp.status_code!=200:raise ValueError("Drive trash request "+str(reason(resp)))
        data=resp.json()
        need(data.get("id")==fid and data.get("name")==name and int(data.get("size",-1))==info["size"] and data.get("trashed") is True,"Trash response identity mismatch")
    with requests.get(url,params=params,headers=headers,timeout=(20,60),allow_redirects=False) as resp:
        need(resp.status_code==200,"Drive trash state readback unavailable")
        data=resp.json()
        need(data.get("id")==fid and data.get("name")==name and data.get("trashed") is True,"Trash state not confirmed")

def write_receipt(work,name,record,proof):
    n=name+".retired.json";p=work/n
    payload={"version":1,"superseded_by":LATEST,"retired":record,"proof":proof,"approval_scope":"three older managed checkpoints only"}
    p.write_text(json.dumps(payload,sort_keys=True,indent=2)+"\n")
    # Use the existing immutable receipt upload and verify readback by exact object ID.
    subprocess.run(["rclone","copyto","--immutable","--checksum","--retries","2","--low-level-retries","3",str(p),r.PREFIX+"/"+n],check=True,timeout=240)
    inv=inventory();need(n in inv and inv[n]["size"]==p.stat().st_size,"Receipt inventory mismatch")
    back=work/("readback-"+n);fetch_id(n,inv[n],back,[])
    need(file_digest(back)==file_digest(p),"Retirement receipt readback differs")
    return inv[n]

def persist(report):
    Path("controlled-retirement-report.json").write_text(json.dumps(report,indent=2))
    print("CONTROLLED_RETIREMENT",json.dumps({**report,"proofs":list(report.get("proofs",{}))}),flush=True)

def main():
    report={"mode":"approved_one_time_retirement","passed":False,"stage":"prior_evidence","retired":[],"actions":[],"proofs":{},"retained":list(KEEP)}
    try:
        prior=json.loads(Path("prior-proof/checkpoint-recovery-proof.json").read_text())
        target=json.loads(Path("target-proof/targeted-checkpoint-report.json").read_text())
        need(prior["github_redundancy_proven"] is True and prior["stage"]=="complete" and prior["latest"]==LATEST and {x["name"] for x in prior["candidates"]}==set(RETIRE),"Prior redundancy evidence mismatch")
        need(all(x["redundancy_proven"] and x["drive"]["verified"] for x in prior["candidates"]),"Prior candidate evidence incomplete")
        need(target["passed"] is True and target["stage"]=="complete" and target["inventory_unchanged_during_test"] is True and target["drive_restored"] is True,"Targeted evidence incomplete")
        report["stage"]="fresh_inventory"
        inv,stable,assets=read_inventory()
        need(fingerprint(inv)==target["inventory_fingerprint_after"] and fingerprint(stable)==target["release_fingerprint_after"],"Inventory differs from approved proof; no changes made")
        with tempfile.TemporaryDirectory(prefix="titan-approved-retirement-") as td:
            root=Path(td);records={}
            for n in sorted(EXPECTED):
                report["stage"]="registration:"+n
                name=n+".registered.json";p=root/name
                need(name in inv,"Missing registered checkpoint")
                fetch_id(name,inv[name],p,[])
                records[n]=json.loads(p.read_bytes())
            check_scope(inv,assets,records)
            # Re-establish fresh independent recovery evidence for every retained and retiring pair.
            latestdb=None
            for n in KEEP+RETIRE:
                report["stage"]="fresh_restore:"+n
                p=root/n;p.mkdir()
                db=github_pair(p/"github",records[n])
                drive=drive_pair(p/"drive",records[n],inv)
                need(file_digest(db)==file_digest(drive),"Independent restored databases differ")
                if n==LATEST:latestdb=db
                if n in RETIRE:
                    need(latestdb is not None,"Newest checkpoint not verified")
                    report["proofs"][n]=r.append_superset(db,latestdb)
                # Remove only disposable local restored copies to bound runner disk.
                if n!=LATEST:
                    db.unlink();drive.unlink()
                else:drive.unlink()
                report.setdefault("freshly_verified",[]).append(n)
                persist(report)
            need(set(report["freshly_verified"])==EXPECTED and set(report["proofs"])==set(RETIRE),"Fresh proof incomplete")
            assert_inventory(inv,stable)
            report["stage"]="preflight_passed";persist(report)
            # All six pairs have passed and no backend has changed. Only exact approved IDs may be retired.
            expected_drive=dict(inv);expected_release=dict(stable)
            for n in RETIRE:
                rec=records[n]
                for key in ("manifest","data"):
                    report["stage"]="github_remove:"+n+":"+key;persist(report)
                    assert_inventory(expected_drive,expected_release)
                    m=rec[key];need(m["id"]==IDS[n][0 if key=="data" else 1] and expected_release[m["name"]]==m,"Release asset changed")
                    subprocess.run(["gh","api","--method","DELETE",f"repos/{REPO}/releases/assets/{m['id']}"],check=True,timeout=60)
                    expected_release.pop(m["name"])
                    assert_inventory(expected_drive,expected_release)
                    report["actions"].append({"action":"github_asset_removed","name":m["name"],"asset_id":m["id"]});persist(report)
                report["stage"]="drive_trash:"+n;persist(report)
                assert_inventory(expected_drive,expected_release)
                trash_exact(n,expected_drive[n])
                expected_drive.pop(n)
                assert_inventory(expected_drive,expected_release)
                report["actions"].append({"action":"drive_payload_trashed","name":n});persist(report)
                report["stage"]="receipt:"+n;persist(report)
                expected_drive[n+".retired.json"]=write_receipt(root,n,rec,report["proofs"][n])
                assert_inventory(expected_drive,expected_release)
                report["retired"].append(n);persist(report)
            report["stage"]="final_verification"
            assert_inventory(expected_drive,expected_release)
            need({n for n in expected_drive if r.name_allowed(n)}==set(KEEP),"Final active managed count not three")
            report["managed_payloads_after"]=3
            report["active_payload_bytes_retired"]=sum(records[n]["data"]["size"] for n in RETIRE)
            report["final_inventory_fingerprint"]=fingerprint(expected_drive)
            report["final_release_fingerprint"]=fingerprint(expected_release)
            report["passed"]=True;report["stage"]="complete"
    except Exception as e:
        report["error"]=str(e)[:180] if type(e) is ValueError else type(e).__name__
    finally:persist(report)
    if not report["passed"]:raise SystemExit("Retirement incomplete; stop and inspect exact recorded stage before retrying")
if __name__=="__main__":main()
