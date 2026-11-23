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
from src.storage_incremental_archive import rclone_copy
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


def put_json(work, name, report):
    local = work / name
    local.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    rclone_copy(str(local), f'{PREFIX}/{name}')
    back = work / ('readback-' + name)
    rclone_copy(f'{PREFIX}/{name}', str(back))
    if file_digest(local) != file_digest(back):
        raise ValueError('Retention record readback mismatch')


def get_json(work, name):
    p = work/name
    rclone_copy(f'{PREFIX}/{name}', str(p))
    return json.loads(p.read_text())


def verify_pair(work, name, rec):
    p = work/name
    rclone_copy(f'{PREFIX}/{name}', str(p))
    rclone_copy(f'{PREFIX}/{name}.json', str(manifest_path(p)))
    for path,key in ((p,'data'),(manifest_path(p),'manifest')):
        meta = rec[key]
        if meta['name'] != path.name or meta['size'] != path.stat().st_size or meta['digest'] != 'sha256:'+file_digest(path):
            raise ValueError('Drive checkpoint differs from registered GitHub asset')
    return p


def retire(repo, release_id, checkpoint, latest_db, report_path):
    if not name_allowed(checkpoint.name):
        raise ValueError('Not a managed checkpoint')
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        pages = release_assets(repo, release_id)
        assets = indexed(pages)
        name = checkpoint.name
        if select_asset(pages) != name:
            raise ValueError('Current checkpoint is not the selected release')
        verify_asset(pages, checkpoint)
        verify_asset(pages, manifest_path(checkpoint))
        record = {'version':1,'data':identity(assets[name]),'manifest':identity(assets[name+'.json'])}
        with tempfile.TemporaryDirectory(dir=work) as check:
            latest = verify_pair(Path(check), name, record)
            if restore(latest, Path(check)/'latest.db')['database_sha256'] != file_digest(latest_db):
                raise ValueError('Latest recovered checkpoint differs from append candidate')
        put_json(work, name+'.registered.json', record)
        files = run('rclone','lsf',PREFIX,'--files-only').splitlines()
        if len(files) != len(set(files)):
            raise ValueError('Duplicate names in rolling Drive folder')
        records = {}
        for f in files:
            if f.endswith('.registered.json'):
                n = f[:-len('.registered.json')]
                if name_allowed(n) and n+'.retired.json' not in files:
                    rec = get_json(work, f)
                    if rec.get('version') != 1 or rec['data']['name'] != n or rec['manifest']['name'] != n+'.json':
                        raise ValueError('Unexpected registration metadata')
                    records[n] = rec
        report = {'policy':'retain three newest managed generations; all pre-policy assets pinned',
                  'latest':name,'managed':len(records),'retired':[],'skipped':[]}
        for old_name in eligible(records, name):
            # No source evidence, original checkpoint, or unregistered file
            # can reach this block. Preserve all three newer generations.
            rec = records[old_name]
            try:
                with tempfile.TemporaryDirectory(dir=work) as sub:
                    old_dir = Path(sub)
                    prior = verify_pair(old_dir, old_name, rec)
                    recovered = old_dir/'old.db'
                    restore(prior, recovered)
                    proof = append_superset(recovered, latest_db)
                current_pages = release_assets(repo, release_id)
                current = indexed(current_pages)
                if select_asset(current_pages) != name or any(identity(current[k]) != record[key] for k,key in ((name,'data'),(name+'.json','manifest'))):
                    raise ValueError('Latest changed during retention')
                for key in ('manifest','data'):
                    a = rec[key]
                    if a['name'] in current and identity(current[a['name']]) != a:
                        raise ValueError('Old release identity changed')
                # Delete only verified exact IDs; manifest first so an
                # interruption cannot select a half-retired prior pair.
                for key in ('manifest','data'):
                    a = rec[key]
                    if a['name'] in current:
                        run('gh','api','--method','DELETE',f"repos/{repo}/releases/assets/{a['id']}")
                final = indexed(release_assets(repo, release_id))
                if old_name in final or old_name+'.json' in final:
                    raise ValueError('Release retirement not confirmed')
                # Old manifest and registration/proof are retained; only the
                # redundant compressed data object is moved to Drive Trash.
                run('rclone','deletefile','--drive-use-trash=true',f'{PREFIX}/{old_name}')
                if old_name in run('rclone','lsf',PREFIX,'--files-only').splitlines():
                    raise ValueError('Drive trash move not confirmed')
                put_json(work, old_name+'.retired.json', {'version':1,'superseded_by':name,'retired':rec,'proof':proof})
                report['retired'].append(old_name)
            except (ValueError, KeyError, OSError, subprocess.CalledProcessError, sqlite3.Error) as exc:
                report['skipped'].append({'name':old_name,'reason':type(exc).__name__})
        report_path.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        if report['skipped']:
            raise ValueError('Some retirement candidates require review; no unverified assets removed')


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
