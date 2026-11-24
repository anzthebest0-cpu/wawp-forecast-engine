"""Recover the approved WAWP interval, with confirmed independent Drive batches."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

from src.archive_single_runs import recover_batch, run_plan, verify_bundle
from src.storage_snapshot import file_digest

START, END, GAP = date(2026,8,19), date(2026,11,23), date(2026,10,1)


def rclone(*args):
    subprocess.run(['rclone',*args],check=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True)
    p.add_argument('--remote',required=True)
    p.add_argument('--execution-id',required=True)
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    work=args.work; prior=work/'prior'; output=work/'new'; readback=work/'readback'
    for d in (prior,output,readback):d.mkdir(parents=True,exist_ok=True)
    remote=args.remote.rstrip('/')
    rclone('mkdir',remote)
    rclone('copy',remote,str(prior),'--include','single-runs-*.zip','--transfers','1')
    archives=sorted(prior.glob('single-runs-*.zip'))
    accepted={}; outcomes={}

    def register(path):
        manifest=verify_bundle(path)
        for rec in manifest['records']:
            key=(rec['model'],rec['initialization_utc'])
            if rec['status']=='accepted':
                if key in accepted and accepted[key]!=rec['sha256']:
                    raise ValueError(f'Conflicting prior archive evidence for {key}')
                accepted[key]=rec['sha256']
            outcomes[key]=rec['status']

    for f in archives: register(f)
    expected={(m,r.isoformat()) for m,r in run_plan(START,END,GAP)}
    report={'contract':'wawp-archive-recovery-report-v1','expected_runs':len(expected),'batches':[], 'status':'running'}
    started=time.monotonic()

    def save(status):
        report.update(status=status,accepted_runs=len(expected&accepted.keys()),missing_runs=len(expected-accepted.keys()))
        report['outcomes_unaccepted']={}
        for key in expected-accepted.keys():
            s=outcomes.get(key,'not_attempted')
            report['outcomes_unaccepted'][s]=report['outcomes_unaccepted'].get(s,0)+1
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n')

    # Two paced workers, well below the pilot concurrency limit. Stop on 429.
    # Small batches bound time between independent Drive readbacks.
    d=START
    while d<=END:
        if time.monotonic()-started>270*60:
            save('paused_runner_time_budget')
            print('Runner time budget reached; resume the workflow. Accepted batches remain verified.',flush=True)
            return
        end=min(d+timedelta(days=6 if d<GAP else 2),END)
        if d<GAP<=end:end=GAP-timedelta(days=1)
        keys={(m,r.isoformat()) for m,r in run_plan(d,end,GAP)}
        if keys<=accepted.keys():
            print(f'Already verified: {d} through {end}',flush=True)
            d=end+timedelta(days=1);continue
        name=f'single-runs-{d:%Y%m%d}-{end:%Y%m%d}-{args.execution_id}.zip'
        path=output/name
        manifest=recover_batch(d,end,GAP,path,workers=2,interval=1.5,resume=archives)
        target=f'{remote}/{name}'
        rclone('copyto',str(path),target,'--immutable','--transfers','1')
        copied=readback/name
        rclone('copyto',target,str(copied),'--transfers','1')
        if file_digest(path)!=file_digest(copied):raise ValueError('Drive archive readback differs')
        register(copied);archives.append(copied)
        report['batches'].append({'start':str(d),'end':str(end),'file':name,'bytes':path.stat().st_size,'sha256':file_digest(path),'drive_readback_verified':True,'accepted':sum(r['status']=='accepted' for r in manifest['records'])})
        save('running')
        if any(r['status'] in {'rate_limited','not_attempted_rate_limit'} for r in manifest['records']):
            save('paused_provider_rate_limit')
            print('Provider rate limit: stopped after saving verified partial archive; resume manually after provider cooldown.',flush=True)
            return
        print(f'Overall {len(expected&accepted.keys())}/{len(expected)} accepted',flush=True)
        d=end+timedelta(days=1)
    save('complete' if expected<=accepted.keys() else 'complete_with_provider_gaps')
    print(json.dumps({k:v for k,v in report.items() if k!='batches'}),flush=True)


if __name__=='__main__':main()
