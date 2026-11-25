"""Bounded Drive-backed prospective experiment; no production DB or Pages writes."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path

from src import shadow_research as s

UTC=timezone.utc


def command(*args,capture=False):
    return subprocess.run(['rclone',*args],check=True,text=True,capture_output=capture).stdout if capture else subprocess.run(['rclone',*args],check=True)


def listing(remote):
    names=command('lsf',remote,'--files-only',capture=True).splitlines()
    if len(names)!=len(set(names)):raise ValueError(f'Duplicate names in {remote}; resolve before reading')
    return names


def fetch(remote,name,local):
    if Path(name).name!=name:raise ValueError('unsafe archive name')
    command('copyto',f'{remote}/{name}',str(local/name),'--transfers','1')
    return local/name


def publish(path,remote,readback):
    command('copyto',str(path),f'{remote}/{path.name}','--immutable','--transfers','1')
    copy=fetch(remote,path.name,readback)
    if s.digest(copy.read_bytes())!=s.digest(path.read_bytes()):raise ValueError('Drive readback mismatch')
    return copy


def json_file(path,data):
    if path.exists():raise FileExistsError(path)
    path.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')
    return path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--execution-id',required=True);p.add_argument('--remote',default='wawp:shadow-research');args=p.parse_args()
    if not re.fullmatch(r'\d+-\d+',args.execution_id):p.error('invalid execution ID')
    work=args.work;work.mkdir(parents=True,exist_ok=True)
    dirs={name:work/name for name in ('obs','historic','source','forecast','new','readback')}
    for d in dirs.values():d.mkdir()
    remote=args.remote.rstrip('/');src=f'{remote}/source';fc=f'{remote}/forecasts';ver=f'{remote}/verification';awos='wawp:awos-inbox';hist='wawp:historical-single-runs'
    for d in (src,fc,ver):command('mkdir',d)
    now=datetime.now(UTC);init=s.choose_init(now);cycle=s.cycle_key(init)
    # Original hourly files on Drive are the authoritative observation snapshot.
    for name in listing(awos):
        if re.fullmatch(r'000HLY\.\d{6}\.dat',name):fetch(awos,name,dirs['obs'])
    obs,obs_inventory=s.read_obs(dirs['obs'])
    forecasts=[];existing=None
    for name in listing(fc):
        m=re.fullmatch(r'shadow-forecast-(\d{10}Z)-([0-9a-f]{64})\.json',name)
        if not m:continue
        f=fetch(fc,name,dirs['forecast']);body=f.read_bytes()
        if s.digest(body)!=m.group(2):raise ValueError('immutable forecast checksum mismatch')
        data=s.validate_forecast(json.loads(body));assert s.cycle_key(s.parse_time(data['initialization_utc']))==m.group(1)
        forecasts.append(data)
        if m.group(1)==cycle:existing=data
    source_files=listing(src);new=False
    if existing is None:
        # Prefer the exact first captured source for this cycle on retry.
        source_name=f'shadow-source-{cycle}.zip'
        if source_name in source_files:source_path=fetch(src,source_name,dirs['source'])
        else:
            source_path=dirs['new']/source_name;s.capture_source(init,source_path);publish(source_path,src,dirs['readback'])
        manifest,current=s.read_shadow_source(source_path);source_hash=s.digest(source_path.read_bytes())
        start=(init-timedelta(days=34)).date()
        for name in listing(hist):
            match=re.fullmatch(r'single-runs-(\d{8})-(\d{8})-.+\.zip',name)
            if not match:continue
            first,last=(datetime.strptime(x,'%Y%m%d').date() for x in match.groups())
            if last>=start and first<=init.date():fetch(hist,name,dirs['historic'])
        for name in source_files:
            match=re.fullmatch(r'shadow-source-(\d{10}Z)\.zip',name)
            if not match or name==source_name:continue
            t=datetime.strptime(match.group(1),'%Y%m%d%HZ').replace(tzinfo=UTC)
            if start<=t.date() and t<init:fetch(src,name,dirs['source'])
        history,inventory=s.load_training_archives(dirs['historic'],dirs['source'],init)
        issued=datetime.now(UTC)
        if any(v[2]>issued for v in history.values()):raise ValueError('future source receipt in training snapshot')
        data=s.build_forecast(init,issued,source_hash,current,history,obs,obs_inventory,inventory)
        # Issue at serialization time, not before potentially slow fitting.
        issued=datetime.now(UTC);data['issued_at_utc']=s.timestamp(issued)
        data['rows']=[r for r in data['rows'] if s.parse_time(r['valid_utc'])>issued and (r['parameter']!='precipitation' or s.parse_time(r['valid_utc'])-timedelta(hours=6)>=issued)]
        for r in data['rows']:r['receipt_lead_hours']=(s.parse_time(r['valid_utc'])-issued).total_seconds()/3600
        s.attach_integrated(data)
        s.validate_forecast(data)
        body=(json.dumps(data,indent=2,allow_nan=False)+'\n').encode();name=f'shadow-forecast-{cycle}-{s.digest(body)}.json';path=dirs['new']/name
        path.write_bytes(body);publish(path,fc,dirs['readback']);forecasts.append(data);existing=data;new=True
    # Confirm the exact stored source for the current cycle, even on resume.
    if existing is None:raise ValueError('no immutable shadow forecast')
    source_name=f'shadow-source-{cycle}.zip';path=dirs['source']/source_name
    if not path.exists():path=fetch(src,source_name,dirs['source'])
    if s.digest(path.read_bytes())!=existing['source_sha256']:raise ValueError('stored forecast source mismatch')
    # Evaluation reads only previously issued immutable rows and observed hours.
    verification=s.verify_forecasts(forecasts,obs,datetime.now(UTC),obs_inventory)
    vpath=json_file(dirs['new']/f'shadow-verification-{args.execution_id}.json',verification);publish(vpath,ver,dirs['readback'])
    # Public action artifact contains counts only, not station data or forecasts.
    status={'status':'research_only_no_promotion','initialization_utc':existing['initialization_utc'],'new_forecast':new,'source_models':len(existing['source_models']),'raw_rows':sum(r['candidate']=='raw_core' for r in existing['rows']),'candidate_rows':sum(r['candidate']!='raw_core' for r in existing['rows']),'awos_freshness_gate_passed':existing['awos_freshness_gate_passed'],'latest_training_observation_utc':existing['latest_training_observation_utc'],'pending_or_unobserved_rows':verification['pending_or_unobserved_rows'],'scored_groups':len(verification['scores']),'withheld':existing['withheld']}
    status['integrated']={k:v for k,v in verification['integrated'].items() if k not in ('scores','joint_scores')}
    status['integrated']['current_cycle_vectors']=len(existing.get('integrated',{}).get('vectors',[]))
    status['integrated']['current_cycle_all_fits_active']=sum(all(f['active'] for f in v['fits'].values()) for v in existing.get('integrated',{}).get('vectors',[]))
    args.report.parent.mkdir(parents=True,exist_ok=True);json_file(args.report,status);print(json.dumps(status),flush=True)


if __name__=='__main__':main()
