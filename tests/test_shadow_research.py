from datetime import datetime,timedelta,timezone
import json
import numpy as np
import pytest

from src import shadow_research as s

UTC=timezone.utc
INIT=datetime(2026,11,24,0,tzinfo=UTC)


def payload(init,t=29.):
    h={'time':[(init+timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in range(97)]}
    for var,value in [('temperature_2m',t),('dew_point_2m',23.),('pressure_msl',1014.),('wind_speed_10m',5.),('wind_direction_10m',350.),('precipitation',0.)]:h[var]=[value]*97
    return {'latitude':-4.338158,'longitude':121.524047,'elevation':10,'utc_offset_seconds':0,'hourly':h,'hourly_units':{k:v[0] for k,v in s.PARAMS.items()}}


def obs_at(t):return dict(zip(s.PARAMS,(27.,22.,1013.,3.,10.,0.)))


def fixture():
    obs={INIT-timedelta(hours=i):obs_at(INIT-timedelta(hours=i)) for i in range(1,24*35)}
    hist={}
    for day in range(1,35):
        for hour in (0,12):
            t=INIT.replace(hour=hour)-timedelta(days=day)
            for m in s.PREVIEW_MODEL_REGISTRY:hist[(m,t)]=('a'*64,payload(t),t+timedelta(hours=8))
    current={(m,INIT):('a'*64,payload(INIT),INIT+timedelta(hours=8)) for m in s.PREVIEW_MODEL_REGISTRY}
    return obs,hist,current


def test_cycle_selection_actual_receipt_cutoff():
    assert s.choose_init(INIT+timedelta(hours=8,minutes=20))==INIT
    assert s.choose_init(INIT+timedelta(hours=7,minutes=59))==INIT-timedelta(hours=12)
    assert s.choose_init(INIT+timedelta(hours=20,minutes=20))==INIT+timedelta(hours=12)


def test_prospective_candidates_no_leakage_and_no_backdated_rain():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    # A future observation must not be used in a training fit.
    obs[INIT+timedelta(hours=12)]={**obs_at(INIT),'temperature_2m':50.}
    r=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{});assert r['awos_freshness_gate_passed']
    temp=[x for x in r['rows'] if x['parameter']=='temperature_2m' and x['method']=='quantile_map'];assert temp and all(abs(x['prediction']-27)<1e-8 for x in temp)
    assert all(s.parse_time(x['training_last_valid_utc'])<INIT for x in temp)
    assert all(s.parse_time(x['valid_utc'])>issued for x in r['rows'])
    rain=[x for x in r['rows'] if x['parameter']=='precipitation'];assert rain and all(x['candidate']=='raw_core' and x['lead_hours']>=18 for x in rain)
    assert not any('taf' in x for x in r['rows'])
    bad=json.loads(json.dumps(r));bad['rows'][0]['valid_utc']=s.timestamp(issued)
    with pytest.raises(ValueError):s.validate_forecast(bad)


def test_missing_core_or_stale_awos_never_invents_calibration():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    stale={t:y for t,y in obs.items() if t<INIT-timedelta(hours=49)}
    r=s.build_forecast(INIT,issued,'a'*64,current,hist,stale,{},{});assert not r['awos_freshness_gate_passed']
    assert r['rows'] and all(x['candidate']=='raw_core' for x in r['rows'])
    current.pop((s.CORE[0],INIT));r=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{});assert r['rows']==[] and r['withheld']


def test_verify_only_issued_rows_and_matched_baselines():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    r=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{});check=INIT+timedelta(hours=12)
    assert not s.verify_forecasts([r],obs,issued,{})['scores']
    obs[check]=obs_at(check)
    v=s.verify_forecasts([r],obs,check,{})
    t=next(x for x in v['scores'] if x['parameter']=='temperature_2m' and x['candidate']=='quantile_map_7_core_14d')
    assert t['n']==1 and t['candidate_loss']==pytest.approx(0) and t['paired_core_raw_loss']==pytest.approx(2)
    with pytest.raises(ValueError):s.verify_forecasts([r,r],obs,check,{})


def test_source_capture_preserves_exact_bytes(tmp_path):
    def fake(model,init,pacer):
        info={'model':model,'initialization_utc':s.timestamp(init),'status':'accepted','retrieved_at_utc':s.timestamp(init+timedelta(hours=8))}
        if model=='GEM_GLOBAL':return {**info,'status':'provider_unavailable_or_rejected'},None
        body=json.dumps(payload(init)).encode();info.update(s.inspect_response(body,model,init),sha256=s.digest(body),payload_bytes=len(body));return info,body
    m,rows=s.capture_source(INIT,tmp_path/'test.zip',fetcher=fake)
    assert len(m['records'])==9 and len(rows)==8
    assert ('GEM_GLOBAL',INIT) not in rows


def test_diurnal_bias_additive_with_time_of_day_shrinkage():
    rows=[]
    for d in range(1,15):
        rows.extend([(INIT-timedelta(days=d),1010.,[1012.]*7),(INIT-timedelta(days=d)+timedelta(hours=6),1010.,[1010.]*7)])
    assert s.fit_predict(rows,[1014.]*7,s.CORE,'pressure_msl','diurnal_bias',INIT)==pytest.approx(1012.5)
    assert s.fit_predict(rows,[1014.]*7,s.CORE,'pressure_msl','diurnal_bias',INIT+timedelta(hours=6))==pytest.approx(1013.5)
    assert s.fit_predict(rows,[1014.]*7,s.CORE,'pressure_msl','diurnal_bias',INIT+timedelta(hours=3))==pytest.approx(1013.)
    obs,hist,current=fixture();r=s.build_forecast(INIT,INIT+timedelta(hours=8,minutes=20),'a'*64,current,hist,obs,{},{});diurnal=[x for x in r['rows'] if x['method']=='diurnal_bias' and x['parameter'] in s.DIURNAL_WINDOWS]
    assert diurnal and {x['parameter'] for x in diurnal}==set(s.DIURNAL_WINDOWS)
    assert all(x['window_days']==s.DIURNAL_WINDOWS[x['parameter']][x['lead_bucket']] for x in diurnal)
    assert any(x['method']=='median_bias' for x in r['rows']) and any(x['method']=='mae_weighted' for x in r['rows'])
    bad=json.loads(json.dumps(r));next(x for x in bad['rows'] if x['method']=='diurnal_bias')['window_days']=45
    with pytest.raises(ValueError):s.validate_forecast(bad)


def test_hourly_diurnal_wind_matches_replay_and_keeps_paired_raw():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    r=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{});w=[x for x in r['rows'] if x['candidate']==s.WIND_HOURLY_CANDIDATE]
    assert len(w)==88 and {x['lead_hours'] for x in w}==set(range(9,97))
    assert all(x['prediction']==pytest.approx(3.) and x['raw_core']==pytest.approx(5.) and x['window_days']==14 for x in w)
    assert all(s.parse_time(x['training_last_valid_utc'])<INIT for x in w)
    assert all(x['lead_hours'] in s.WIND_HOURLY_GROUPS[x['lead_bucket']] for x in w)
    bad=json.loads(json.dumps(r));next(x for x in bad['rows'] if x['candidate']==s.WIND_HOURLY_CANDIDATE)['window_days']=7
    with pytest.raises(ValueError):s.validate_forecast(bad)


def test_nighttime_spread_is_derived_and_never_visibility():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    r=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{});x=[a for a in r['rows'] if a['candidate']==s.SPREAD_CANDIDATE]
    assert x and all(a['parameter']==s.SPREAD and 0<=a['prediction']<=85 for a in x)
    assert all(a['prediction']==pytest.approx(5.) and a['raw_core']==pytest.approx(6.) for a in x)
    assert all((s.parse_time(a['valid_utc']).hour+8)%24 in (*range(19,24),*range(0,8)) for a in x)
    assert all(s.parse_time(a['training_last_valid_utc'])<INIT for a in x)
    assert all('visibility' not in a['parameter'] for a in r['rows'])
    bad=json.loads(json.dumps(r));next(a for a in bad['rows'] if a['candidate']==s.SPREAD_CANDIDATE)['parameter']='wind_speed_10m'
    with pytest.raises(ValueError):s.validate_forecast(bad)
    bad=json.loads(json.dumps(r));next(x for x in bad['rows'] if x['candidate']==s.WIND_HOURLY_CANDIDATE)['method']='mean_bias'
    with pytest.raises(ValueError):s.validate_forecast(bad)
