"""Prospective, research-only WAWP forecasts from explicit provider cycles.

Never write the rolling database, official products or operational weights.
Candidate methods are predeclared; every scored prediction must have been
issued before its valid time, and every fitting outcome precedes model init.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from src.archive_single_runs import Pacer, fetch_one, inspect_response, verify_bundle
from src.model_registry import PREVIEW_MODEL_REGISTRY

UTC=timezone.utc
CORE=('CMA_GRAPES_GLOBAL','ECMWF_HRES','ECMWF_IFS_9KM','GFS_GLOBAL','ICON_SEAMLESS','METEOFRANCE_ARPEGE_WORLD','UKMO_GLOBAL_10KM')
IFS={'ECMWF_HRES','ECMWF_IFS_9KM'}
PARAMS={'temperature_2m':('°C',-10,55),'dew_point_2m':('°C',-30,45),'pressure_msl':('hPa',850,1100),'wind_speed_10m':('kn',0,150),'wind_direction_10m':('°',0,360),'precipitation':('mm',0,300)}
GROUPS={'12-24h':(12,18,24),'30-48h':(30,36,42,48),'54-72h':(54,60,66,72),'78-96h':(78,84,90,96)}
CONFIG={'temperature_2m':('quantile_map','GEM_GLOBAL'),'pressure_msl':('median_bias','JMA_GSM'),'dew_point_2m':('mae_weighted','JMA_GSM'),'wind_speed_10m':('mean_bias',None),'wind_direction_10m':('mae_weighted',None)}
SOURCE_CONTRACT='wawp-shadow-source-v1'
FORECAST_CONTRACT='wawp-prospective-shadow-v1'


def digest(data):return hashlib.sha256(data).hexdigest()
def timestamp(t):return t.astimezone(UTC).isoformat()
def parse_time(s):
    t=datetime.fromisoformat(s)
    if t.tzinfo is None or t.utcoffset()!=timedelta(0):raise ValueError('explicit UTC required')
    return t.astimezone(UTC)


def choose_init(now):
    """Conservative eight-hour archive availability assumption, explicitly tested."""
    t=now.astimezone(UTC)-timedelta(hours=8)
    return t.replace(hour=(t.hour//12)*12,minute=0,second=0,microsecond=0)


def cycle_key(init):return init.strftime('%Y%m%d%HZ')


def scalar(v,var):
    lo,hi=PARAMS[var][1:]
    return float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and lo<=v<=hi else None


def capture_source(init,path,*,fetcher=fetch_one):
    """One explicit initialization; preserve original bytes, no substitute cycle."""
    pacer=Pacer(1.5);records=[]
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for model in PREVIEW_MODEL_REGISTRY:
            info,body=fetcher(model,init,pacer)
            if body is not None:
                if info.get('sha256')!=digest(body):raise ValueError('source checksum mismatch')
                info['member']=f'{model}.json';z.writestr(info['member'],body)
            records.append(info)
            print(model,info['status'],flush=True)
        data={'contract':SOURCE_CONTRACT,'initialization_utc':timestamp(init),'records':records}
        z.writestr('manifest.json',json.dumps(data,separators=(',',':')))
    return read_shadow_source(path)


def read_shadow_source(path):
    with zipfile.ZipFile(path) as z:
        data=json.loads(z.read('manifest.json'));init=parse_time(data['initialization_utc']);rows=data['records']
        if data['contract']!=SOURCE_CONTRACT or len(rows)!=len(PREVIEW_MODEL_REGISTRY) or {x['model'] for x in rows}!=set(PREVIEW_MODEL_REGISTRY):raise ValueError('invalid source cohort')
        result={};members={'manifest.json'}
        for r in rows:
            if parse_time(r['initialization_utc'])!=init:raise ValueError('source initialization mismatch')
            if r['status']!='accepted':continue
            member=r['member'];members.add(member);body=z.read(member)
            if digest(body)!=r['sha256'] or len(body)!=r['payload_bytes']:raise ValueError('source payload mismatch')
            detail=inspect_response(body,r['model'],init)
            if not all(r.get(k)==v for k,v in detail.items()):raise ValueError('source metadata mismatch')
            received=parse_time(r['retrieved_at_utc'])
            if received<init:raise ValueError('future initialization')
            result[(r['model'],init)]=(r['sha256'],json.loads(body),received)
        if set(z.namelist())!=members or len(z.namelist())!=len(members):raise ValueError('unexpected source members')
    return data,result


def load_training_archives(historical,shadow,init):
    """Bounded historical window. Duplicate records must match exact payload hashes."""
    start=init-timedelta(days=34);runs={};inventory={}
    def add(k,v):
        if not start<=k[1]<init:return
        if k in runs and runs[k][0]!=v[0]:raise ValueError(f'conflicting source payloads for {k}')
        runs[k]=v
    for f in sorted(Path(historical).glob('single-runs-*.zip')):
        m=verify_bundle(f);inventory[f.name]=digest(f.read_bytes())
        with zipfile.ZipFile(f) as z:
            for r in m['records']:
                if r['status']!='accepted':continue
                k=(r['model'],parse_time(r['initialization_utc']))
                add(k,(r['sha256'],json.loads(z.read(r['member'])),parse_time(r['retrieved_at_utc'])))
    for f in sorted(Path(shadow).glob('shadow-source-*.zip')):
        _,rows=read_shadow_source(f);inventory[f.name]=digest(f.read_bytes())
        for k,v in rows.items():add(k,v)
    return runs,inventory


def read_obs(folder):
    obs={};inventory={}
    for f in sorted(Path(folder).glob('000HLY.*.dat')):
        if not re.fullmatch(r'000HLY\.\d{6}\.dat',f.name):raise ValueError('unexpected hourly filename')
        raw=f.read_bytes();inventory[f.name]=digest(raw);lines=raw.decode('utf-8').splitlines()
        if not lines or 'WAWP' not in lines[0]:raise ValueError('not WAWP hourly data')
        for line in lines[4:]:
            a=line.split()
            if not a:continue
            if len(a)!=13 or a[0]!='000':raise ValueError('invalid hourly source row')
            t=datetime.strptime(a[1]+a[2],'%Y%m%d%H').replace(tzinfo=UTC)
            if t.strftime('%Y%m')!=f.name.split('.')[1] or t in obs:raise ValueError('duplicate or incorrect hourly period')
            def n(i,s=1):return None if '/' in a[i] else float(a[i])/s
            obs[t]=dict(zip(PARAMS,(n(5,10),n(6,10),n(4,10),n(9),n(8),n(12,10))))
    return obs,inventory


def obs_at(obs,t,var):
    if var=='precipitation':
        values=[scalar(obs.get(t-timedelta(hours=i),{}).get(var),var) for i in range(6)]
        return None if None in values else sum(values)
    if var=='wind_direction_10m' and not (obs.get(t,{}).get('wind_speed_10m') or 0)>0:return None
    return scalar(obs.get(t,{}).get(var),var)


def forecast_at(runs,model,init,lead,var):
    data=runs.get((model,init))
    if data is None:return None
    pay=data[1];a=pay['hourly'].get(var)
    if a is None or lead>=len(a) or pay['hourly_units'].get(var)!=PARAMS[var][0]:return None
    if var=='precipitation':
        values=[scalar(v,var) for v in a[lead-5:lead+1]]
        return None if len(values)!=6 or None in values else sum(values)
    if var=='wind_direction_10m':
        w=pay['hourly'].get('wind_speed_10m')
        if w is None or len(w)<=lead or not (w[lead] or 0)>0:return None
    return scalar(a[lead],var)


def family(members):
    w=np.array([.5 if m in IFS and IFS<=set(members) else 1. for m in members]);return w/w.sum()


def wrap(d):return (d+180)%360-180


def aggregate(x,w,circ=False):
    if not circ:return float(np.dot(x,w))
    z=np.exp(1j*np.deg2rad(x))@w
    return float(np.rad2deg(np.angle(z))%360) if abs(z)>1e-10 else None


def training_cases(runs,obs,members,var,group,init,days):
    lower=init-timedelta(days=days);rows=[]
    for r in sorted({t for _,t in runs if t.hour in (0,12) and t<init}):
        for lead in GROUPS[group]:
            t=r+timedelta(hours=lead)
            if not lower<=t<init:continue
            y=obs_at(obs,t,var);x=[forecast_at(runs,m,r,lead,var) for m in members]
            if y is not None and None not in x:rows.append((t,y,x))
    return rows


def eligible(rows,days):return len(rows)>=40 and len({r[0].date() for r in rows})>=math.ceil(.7*days)


def fit_predict(rows,x,members,var,method):
    y=np.array([r[1] for r in rows]);X=np.array([r[2] for r in rows]);x=np.array(x);base=family(members);circ=var=='wind_direction_10m'
    err=wrap(X-y[:,None]) if circ else X-y[:,None]
    if method=='mae_weighted':
        w=base/np.maximum(np.mean(abs(err),axis=0),1e-6);w/=w.sum();return aggregate(x,.5*base+.5*w,circ)
    if method=='mean_bias':z=x-np.mean(err,axis=0)
    elif method=='median_bias':z=x-np.median(err,axis=0)
    elif method=='quantile_map':
        q=np.linspace(.05,.95,19);yp=np.quantile(y,q);z=[]
        for i in range(len(x)):
            xp=np.quantile(X[:,i],q);u,ix=np.unique(xp,return_index=True);z.append(x[i]+np.interp(x[i],u,(yp-xp)[ix]))
        z=np.array(z)
    else:raise ValueError('unknown candidate method')
    if var=='wind_speed_10m':z=np.maximum(0,z)
    return aggregate(z,base)


def same_hour_persistence(obs,valid,init,var):
    t=valid-timedelta(hours=24)
    while t>init:t-=timedelta(hours=24)
    if init-t>timedelta(hours=48):return None
    v=obs_at(obs,t,var)
    return (float(v>=1) if var=='precipitation' else v) if v is not None else None


def build_forecast(init,issued,source,current,history,obs,obs_inventory,train_inventory):
    if issued<init or any(v[2]>issued for v in current.values()):raise ValueError('source not available at issuance')
    latest=max((t for t in obs if t<init),default=None);fresh=latest is not None and issued-latest<=timedelta(hours=48)
    fresh_by_var={var:any(t<init and issued-t<=timedelta(hours=48) and obs_at(obs,t,var) is not None for t in obs) for var in CONFIG}
    rows=[];withheld=collections.Counter();cache={}
    for group,leads in GROUPS.items():
        for lead in leads:
            valid=init+timedelta(hours=lead)
            if valid<=issued:continue
            for var in PARAMS:
                if var=='precipitation' and valid-timedelta(hours=6)<issued:continue
                core_values=[forecast_at(current,m,init,lead,var) for m in CORE]
                if None in core_values:withheld[f'{var}:core_missing']+=1;continue
                circ=var=='wind_direction_10m';rain=var=='precipitation';base=family(CORE)
                core_raw=aggregate(np.array(core_values)>=1,base) if rain else aggregate(core_values,base,circ)
                if core_raw is None:withheld[f'{var}:undefined_circular_mean']+=1;continue
                persist=same_hour_persistence(obs,valid,init,var)
                common={'parameter':var,'lead_bucket':group,'lead_hours':lead,'valid_utc':timestamp(valid),'receipt_lead_hours':(valid-issued).total_seconds()/3600,'raw_core':core_raw,'persistence':persist}
                # Baseline is always retained; each candidate has its own exact cohort baseline.
                rows.append({**common,'candidate':'raw_core','members':list(CORE),'method':'family_equal','window_days':None,'prediction':core_raw,'raw_same_cohort':core_raw,'training_n':0})
                if rain:continue # uncalibrated threshold exceedance only
                method,extra=CONFIG[var]
                variants=[CORE]
                if extra:variants.append(CORE+(extra,))
                for members in variants:
                    x=[forecast_at(current,m,init,lead,var) for m in members]
                    if None in x:withheld[f'{var}:{len(members)}_feed_missing']+=1;continue
                    raw=aggregate(x,family(members),circ)
                    if raw is None:continue
                    for days in (14,30):
                        k=(group,var,members,days)
                        if k not in cache:cache[k]=training_cases(history,obs,members,var,group,init,days) if fresh and fresh_by_var[var] else []
                        tr=cache[k]
                        if not eligible(tr,days):withheld[f'{var}:{len(members)}_{days}d_training_unavailable']+=1;continue
                        value=fit_predict(tr,x,members,var,method)
                        if value is None or scalar(value,var) is None:withheld[f'{var}:invalid_candidate']+=1;continue
                        name=f'{method}_{len(members)}_{extra if len(members)>len(CORE) else "core"}_{days}d'
                        rows.append({**common,'candidate':name,'members':list(members),'method':method,'window_days':days,'prediction':value,'raw_same_cohort':raw,'training_n':len(tr),'training_days':len({a[0].date() for a in tr}),'training_last_valid_utc':timestamp(max(a[0] for a in tr))})
    report={'contract':FORECAST_CONTRACT,'status':'research_only_not_operational','initialization_utc':timestamp(init),'issued_at_utc':timestamp(issued),'source_sha256':source,'source_models':sorted(m for m,_ in current),'latest_training_observation_utc':timestamp(latest) if latest else None,'awos_freshness_gate_passed':fresh,'observation_sha256':obs_inventory,'training_archive_sha256':train_inventory,'rows':rows,'withheld':dict(withheld)}
    validate_forecast(report)
    return report


def validate_forecast(data):
    if data['contract']!=FORECAST_CONTRACT or data['status']!='research_only_not_operational':raise ValueError('non-shadow product')
    init=parse_time(data['initialization_utc']);issued=parse_time(data['issued_at_utc']);keys=set()
    if issued<init or not re.fullmatch(r'[0-9a-f]{64}',data['source_sha256']):raise ValueError('invalid issuance/source digest')
    if not set(data['source_models'])<=set(PREVIEW_MODEL_REGISTRY):raise ValueError('unknown source model')
    for r in data['rows']:
        t=parse_time(r['valid_utc']);key=(r['parameter'],r['candidate'],t)
        if key in keys or t<=issued or t!=init+timedelta(hours=r['lead_hours']):raise ValueError('invalid forecast temporal identity')
        keys.add(key)
        var=r['parameter'];members=tuple(r['members'])
        if var not in PARAMS or r['lead_hours'] not in GROUPS[r['lead_bucket']] or not set(members)<=set(data['source_models']):raise ValueError('invalid forecast parameter, lead or members')
        if r['parameter']=='precipitation' and (r['candidate']!='raw_core' or t-timedelta(hours=6)<issued):raise ValueError('unsafe rain forecast')
        if not all(math.isfinite(r[k]) for k in ('prediction','raw_core','raw_same_cohort','receipt_lead_hours')):raise ValueError('invalid numeric forecast')
        if abs(r['receipt_lead_hours']-(t-issued).total_seconds()/3600)>1e-8:raise ValueError('actual receipt lead mismatch')
        if var=='precipitation':
            if any(not 0<=r[k]<=1 for k in ('prediction','raw_core','raw_same_cohort')):raise ValueError('invalid rain probability')
        elif scalar(r['prediction'],var) is None:raise ValueError('outside research validity range')
        if r['candidate']=='raw_core':
            if members!=CORE or r['method']!='family_equal' or r['window_days'] is not None or r['prediction']!=r['raw_core'] or r['prediction']!=r['raw_same_cohort']:raise ValueError('raw baseline inconsistent')
        else:
            method,extra=CONFIG[var];days=r['window_days'];allowed=[CORE]+([CORE+(extra,)] if extra else [])
            if members not in allowed or r['method']!=method or days not in (14,30) or r['training_n']<40 or r['training_days']<math.ceil(.7*days) or not data['awos_freshness_gate_passed'] or parse_time(r['training_last_valid_utc'])>=init:raise ValueError('unqualified or future training data')
    return data


def verify_forecasts(forecasts,obs,now,obs_inventory):
    """Score immutable prior predictions, never today's reforecast of past hours."""
    groups=collections.defaultdict(list);seen=set();pending=0
    for f in forecasts:
        data=validate_forecast(f);init=parse_time(data['initialization_utc']);issued=parse_time(data['issued_at_utc'])
        if init in seen:raise ValueError('duplicate issued initialization')
        seen.add(init)
        for r in data['rows']:
            valid=parse_time(r['valid_utc']);var=r['parameter']
            if valid>now:pending+=1;continue
            actual=obs_at(obs,valid,var)
            if actual is None:pending+=1;continue
            rain=var=='precipitation';circ=var=='wind_direction_10m';actual=float(actual>=1) if rain else actual
            def loss(x):return (x-actual)**2 if rain else abs(wrap(x-actual) if circ else x-actual)
            p=r.get('persistence');groups[(var,r['candidate'],r['lead_bucket'])].append((valid,loss(r['prediction']),loss(r['raw_same_cohort']),loss(r['raw_core']),loss(p) if p is not None else None,actual))
    scores=[]
    for (var,candidate,group),v in sorted(groups.items()):
        arr=np.array([a[1:4] for a in v]);pv=[a for a in v if a[4] is not None]
        scores.append({'parameter':var,'candidate':candidate,'lead_bucket':group,'metric':'Brier' if var=='precipitation' else 'MAE','n':len(v),'distinct_valid_times':len({a[0] for a in v}),'distinct_days':len({a[0].date() for a in v}),'event_pairs':sum(a[5]>=1 for a in v) if var=='precipitation' else None,'candidate_loss':float(arr[:,0].mean()),'paired_same_cohort_raw_loss':float(arr[:,1].mean()),'paired_core_raw_loss':float(arr[:,2].mean()),'persistence_n':len(pv),'paired_persistence_loss':float(np.mean([a[4] for a in pv])) if pv else None,'candidate_loss_on_persistence_pairs':float(np.mean([a[1] for a in pv])) if pv else None})
    return {'contract':'wawp-shadow-verification-v1','as_of_utc':timestamp(now),'initializations':len(seen),'pending_or_unobserved_rows':pending,'observation_sha256':obs_inventory,'scores':scores,'status':'research_only_no_automatic_promotion'}
