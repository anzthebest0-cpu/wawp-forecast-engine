"""Bound only verified, append-only model-refresh checkpoint generations.

Older original backups and source/ledger evidence are never retirement targets.
Only checkpoints from the dedicated rolling prefix with registration records
can be retired. Drive payloads go to Trash; manifest and proof remain.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scripts.verify_preview_append import verify
from src.rolling_checkpoint_transport import RollingTransport, need
import requests
from src.storage_snapshot import file_digest, manifest_path, restore, select_asset, verify_asset

PREFIX = "wawp:checkpoints/rolling"
KEEP = 3
PATTERN = re.compile(r"wawp_forecasts-\d+-\d+\.db\.zst")
PINNED = {"wawp_forecasts-45206519366-1.db.zst", "wawp_forecasts-45279365274-1.db.zst"}


def run(*args):
    return subprocess.check_output(args, text=True)


def release_assets(repo, release_id):
    # Older gh versions do not have --slurp. Parse the concatenated
    # paginated JSON arrays without assuming one page or guessing asset IDs.
    raw = run("gh", "api", "--paginate", f"repos/{repo}/releases/{release_id}/assets?per_page=100")
    decoder = json.JSONDecoder()
    pages = []
    while raw.strip():
        page, end = decoder.raw_decode(raw.lstrip())
        if not isinstance(page, list):
            raise ValueError('Expected release asset page')
        pages.append(page)
        raw = raw.lstrip()[end:]
    return pages


def indexed(pages):
    result = {}
    for page in pages:
        for asset in page:
            name = asset['name']
            if name in result:
                raise ValueError("Duplicate release asset name")
            result[name] = asset
    return result


def identity(asset):
    return {k: asset[k] for k in ('id', 'name', 'size', 'digest', 'state', 'created_at')}


def name_allowed(name):
    return bool(PATTERN.fullmatch(name)) and name not in PINNED


def eligible(records, latest, *, keep=KEEP):
    if latest not in records:
        raise ValueError("Current checkpoint is not registered")
    ordered = sorted(records, key=lambda n: (records[n]['data']['created_at'], records[n]['data']['id']), reverse=True)
    if ordered[0] != latest:
        raise ValueError("Selected latest differs from newest managed generation")
    return ordered[keep:]


def append_superset(old, new):
    with closing(sqlite3.connect(old)) as a, closing(sqlite3.connect(new)) as b:
        diff = b.execute('SELECT COUNT(*) FROM openmeteo_forecasts').fetchone()[0] - a.execute('SELECT COUNT(*) FROM openmeteo_forecasts').fetchone()[0]
        if diff < 0:
            raise ValueError('Native record count decreased')
        old_seq = dict(a.execute('SELECT name,MAX(seq) FROM sqlite_sequence GROUP BY name'))
        new_seq = dict(b.execute('SELECT name,MAX(seq) FROM sqlite_sequence GROUP BY name'))
        if any(new_seq.get(k, -1) < v for k,v in old_seq.items()):
            raise ValueError('SQLite sequence went backwards')
    return verify(old, new, diff)


def stable(pages):
    return {n:identity(a) for n,a in indexed(pages).items()}

def assert_state(repo,release_id,transport,latest,expected_drive,expected_release):
    pages=release_assets(repo,release_id)
    need(select_asset(pages)==latest,"Selected checkpoint changed")
    need(stable(pages)==expected_release,"Release identity changed")
    need(transport.inventory()==expected_drive,"Rolling object inventory changed")

def verify_pair(work,name,rec,transport,inv):
    for key in ("data","manifest"):
        m=rec[key];n=m["name"]
        need(n in inv and inv[n]["size"]==m["size"],"Drive inventory mismatch")
        p=work/n;transport.read(n,inv[n],p)
        need("sha256:"+file_digest(p)==m["digest"],"Drive checkpoint differs from registered GitHub asset")
    return work/name

def registration(name,rec):
    need(name_allowed(name) and rec["version"]==1 and rec["data"]["name"]==name and rec["manifest"]["name"]==name+".json","Invalid managed registration")
    for k in ("data","manifest"):
        m=rec[k];need(identity(m)==m and m["state"]=="uploaded" and re.fullmatch(r"sha256:[0-9a-f]{64}",m["digest"]),"Invalid registered release identity")

def receipt(intent):
    return {"version":1,"superseded_by":intent["latest"]["data"]["name"],"retired":intent["record"],"proof":intent["proof"]}

def retire(repo,release_id,checkpoint,latest_db,report_path):
    name=checkpoint.name
    need(name_allowed(name),"Not a managed checkpoint")
    report={"policy":"retain three newest verified managed generations","latest":name,"retired":[],"skipped":[],"stage":"preflight"}
    try:
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp);t=RollingTransport();pages=release_assets(repo,release_id);assets=stable(pages)
            need(select_asset(pages)==name,"Current checkpoint is not the selected release")
            verify_asset(pages,checkpoint);verify_asset(pages,manifest_path(checkpoint))
            rec={"version":1,"data":assets[name],"manifest":assets[name+".json"]}
            registration(name,rec)
            inv=t.inventory()
            with tempfile.TemporaryDirectory(dir=work) as sub:
                p=Path(sub);a=verify_pair(p,name,rec,t,inv)
                latest=restore(a,p/"latest.db")
                need(latest["database_sha256"]==file_digest(latest_db),"Latest Drive database differs from verified release")
            # Existing registration is read/compared, not replaced.
            reg=name+".registered.json"
            inv[reg]=t.put_json(reg,rec,work)
            assert_state(repo,release_id,t,name,inv,assets)
            records={};pending={}
            for f in sorted(inv):
                if not f.endswith(".registered.json"):continue
                n=f.removesuffix(".registered.json")
                if not name_allowed(n):continue
                if n+".retired.json" in inv:
                    need(n not in inv and n not in assets and n+".json" not in assets,"Retired record has active payload")
                    continue
                old=t.read_json(f,inv[f],work);registration(n,old);records[n]=old
                journal=n+".retiring.json"
                if journal in inv:pending[n]=t.read_json(journal,inv[journal],work)
            need(all(n in records for n in inv if name_allowed(n)),"Unregistered active managed checkpoint")
            need(all(f.removesuffix(".retiring.json") in records or f.removesuffix(".retiring.json")+".retired.json" in inv for f in inv if f.endswith(".retiring.json")),"Orphan retirement journal")
            candidates=eligible(records,name);keep=set(records)-set(candidates)
            need(set(pending)<=set(candidates),"Pending retirement is not eligible")
            report["managed"]=len(records)
            # Verify retained rollback copies before any retirement. Latest was checked above.
            if candidates:
                for n in sorted(keep-{name}):
                    old=records[n]
                    need(all(assets.get(old[k]["name"])==old[k] for k in ("data","manifest")),"Retained release identity mismatch")
                    with tempfile.TemporaryDirectory(dir=work) as sub:
                        p=Path(sub);restore(verify_pair(p,n,old,t,inv),p/"rollback.db")
            for n in candidates:
                report["stage"]="candidate:"+n;old=records[n];journal=n+".retiring.json"
                assert_state(repo,release_id,t,name,inv,assets)
                for k in ("data","manifest"):
                    m=old[k]
                    if m["name"] in assets:need(assets[m["name"]]==m,"Old release identity changed")
                    elif n not in pending:raise ValueError("Missing old release asset without journal")
                if n in pending:
                    intent=pending[n]
                    need(intent["version"]==1 and intent["record"]==old and intent["latest"]==rec and intent["latest_database_sha256"]==file_digest(latest_db),"Pending journal does not match verified newest checkpoint")
                    need(intent["proof"]["status"]=="passed","Pending proof invalid")
                    need(inv.get(n+".json")==intent["drive_manifest"],"Pending Drive manifest changed")
                    if n in inv:need(inv[n]==intent["drive_data"],"Pending Drive payload changed")
                    else:
                        need(n not in assets and n+".json" not in assets,"Trashed payload still has release assets")
                        t.metadata(n,intent["drive_data"],trashed=True)
                else:
                    need(n in inv,"Missing active payload without journal")
                    with tempfile.TemporaryDirectory(dir=work) as sub:
                        p=Path(sub);db=p/"old.db";restore(verify_pair(p,n,old,t,inv),db)
                        proof=append_superset(db,latest_db)
                    intent={"version":1,"record":old,"latest":rec,"latest_database_sha256":file_digest(latest_db),"drive_data":inv[n],"drive_manifest":inv[n+".json"],"proof":proof}
                    assert_state(repo,release_id,t,name,inv,assets)
                    inv[journal]=t.put_json(journal,intent,work)
                    assert_state(repo,release_id,t,name,inv,assets)
                # Verified journal is committed before the first mutation. A restart
                # may resume only with the same verified newest database and exact IDs.
                for k in ("manifest","data"):
                    m=old[k]
                    if m["name"] not in assets:continue
                    assert_state(repo,release_id,t,name,inv,assets)
                    run("gh","api","--method","DELETE",f"repos/{repo}/releases/assets/{m['id']}")
                    assets.pop(m["name"]);assert_state(repo,release_id,t,name,inv,assets)
                if n in inv:
                    assert_state(repo,release_id,t,name,inv,assets)
                    t.trash(n,intent["drive_data"]);inv.pop(n)
                    assert_state(repo,release_id,t,name,inv,assets)
                else:t.metadata(n,intent["drive_data"],trashed=True)
                inv[n+".retired.json"]=t.put_json(n+".retired.json",receipt(intent),work)
                assert_state(repo,release_id,t,name,inv,assets)
                report["retired"].append(n)
            report["active_managed"]=sum(name_allowed(n) for n in inv)
            report["stage"]="complete"
    except (ValueError,KeyError,OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired,sqlite3.Error,requests.RequestException) as exc:
        report["skipped"].append({"stage":report["stage"],"reason":type(exc).__name__})
        raise ValueError("Checkpoint retirement requires review; pending work must reconcile before acquisition") from exc
    finally:
        report_path.write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps(report,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True)
    p.add_argument('--release-id',required=True,type=int)
    p.add_argument('--checkpoint',required=True,type=Path)
    p.add_argument('--database',required=True,type=Path)
    p.add_argument('--report',required=True,type=Path)
    a=p.parse_args()
    if a.repo != 'anzthebest0-cpu/wawp-forecast-engine':
        p.error('Unexpected repository')
    retire(a.repo,a.release_id,a.checkpoint,a.database,a.report)


if __name__=='__main__':
    main()
