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


def test_west_sector_adaptive_frequency_and_blend():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    assert s.west_sector(0,None)==0 and s.west_sector(3,None) is None and s.west_sector(None,270) is None
    assert s.west_sector(2,225)==1 and s.west_sector(2,315)==1 and s.west_sector(2,316)==0
    for o in obs.values():o['wind_direction_10m']=270
    f=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{});rows=[r for r in f['rows'] if r['parameter']==s.WEST_SECTOR]
    assert rows and len(rows)==176 and all(r['training_n']==30 and r['raw_core']==pytest.approx(0.) for r in rows)
    assert all(r['prediction']==pytest.approx(1. if r['method']=='local_frequency' else .75) for r in rows)
    assert all(s.parse_time(r['training_last_valid_utc'])<INIT for r in rows)
    bad=json.loads(json.dumps(f));next(r for r in bad['rows'] if r['candidate']=='blend25_model75_recent30_sector')['prediction']=.6
    with pytest.raises(ValueError):s.validate_forecast(bad)
    target=INIT+timedelta(hours=9);obs[target]={**obs_at(target),'wind_direction_10m':270}
    v=s.verify_forecasts([f],obs,target,{})
    z=next(r for r in v['scores'] if r['parameter']==s.WEST_SECTOR and r['candidate']=='blend25_model75_recent30_sector');assert z['metric']=='Brier' and z['candidate_loss']==pytest.approx(.0625) and z['paired_core_raw_loss']==pytest.approx(1.)
    bad=json.loads(json.dumps(f));next(x for x in bad['rows'] if x['candidate']==s.WIND_HOURLY_CANDIDATE)['method']='mean_bias'
    with pytest.raises(ValueError):s.validate_forecast(bad)


def test_integrated_vector_uses_exact_same_core_and_forecast_only_guard():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    # Simulate a 6h majority wet forecast ending at +18h and dry at +24h.
    for m in s.PREVIEW_MODEL_REGISTRY:
        current[(m,INIT)][1]['hourly']['precipitation'][13:19]=[.2]*6
    f=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{})
    vec=f['integrated']['vectors'];assert len(vec)==14
    wet=next(v for v in vec if v['lead_hours']==18)
    dry=next(v for v in vec if v['lead_hours']==24)
    assert wet['forecast_wet_guard'] and wet['raw']['precipitation']==pytest.approx(1)
    assert wet['guarded']==wet['raw'] and dry['guarded']==dry['corrected']
    assert all(v['fits']['wind_speed_10m']['candidate']==s.WIND_HOURLY_CANDIDATE for v in vec)
    assert wet['fits']['pressure_msl']['window_days']==21 and wet['corrected']['pressure_msl']==pytest.approx(1013)
    assert wet['fits']['dew_point_2m']['window_days']==14 and wet['corrected']['dew_point_2m']==pytest.approx(22)
    assert wet['corrected']['wind_speed_10m']==pytest.approx(3)
    assert set(wet['raw'])==set(s.PARAMS) and wet['raw']['temperature_2m']==wet['corrected']['temperature_2m']
    assert all(s.parse_time(v['valid_utc'])-timedelta(hours=6)>=issued for v in vec)
    # An observed future wet label cannot change a prospective guard.
    obs[INIT+timedelta(hours=24)]={**obs_at(INIT),'precipitation':20.}
    f2=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{});assert f2['integrated']==f['integrated']
    bad=json.loads(json.dumps(f));bad['integrated']['vectors'][0]['guarded']['pressure_msl']+=1
    with pytest.raises(ValueError,match='integrated vector'):s.validate_forecast(bad)


def test_integrated_fallback_coherence_legacy_and_final_issue_filter():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    # Raw dewpoint can be <= T while a fitted positive correction exceeds T.
    for y in obs.values():y['dew_point_2m']=28.;y['temperature_2m']=30.
    for m in s.PREVIEW_MODEL_REGISTRY:
        current[(m,INIT)][1]['hourly']['temperature_2m']=[25.]*97
    f=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{})
    assert f['integrated']['vectors'] and all(v['dewpoint_capped'] and v['corrected']['dew_point_2m']==pytest.approx(25.) for v in f['integrated']['vectors'])
    legacy=json.loads(json.dumps(f));legacy.pop('integrated')
    assert s.validate_forecast(legacy)==legacy
    stale={t:y for t,y in obs.items() if t<INIT-timedelta(hours=49)}
    z=s.build_forecast(INIT,issued,'a'*64,current,hist,stale,{},{});assert z['integrated']['vectors']
    assert all(v['raw']==v['corrected']==v['guarded'] and not any(x['active'] for x in v['fits'].values()) for v in z['integrated']['vectors'])
    # Rebuild only from surviving final-issuance rows after a slow fit.
    final=INIT+timedelta(hours=19)
    f['issued_at_utc']=s.timestamp(final)
    f['rows']=[r for r in f['rows'] if s.parse_time(r['valid_utc'])>final and (r['parameter']!='precipitation' or s.parse_time(r['valid_utc'])-timedelta(hours=6)>=final)]
    for r in f['rows']:r['receipt_lead_hours']=(s.parse_time(r['valid_utc'])-final).total_seconds()/3600
    s.attach_integrated(f)
    assert all(v['lead_hours']>=30 for v in f['integrated']['vectors'])
    s.validate_forecast(f)


def test_integrated_verification_common_wet_dry_no_future_observation():
    obs,hist,current=fixture();issued=INIT+timedelta(hours=8,minutes=20)
    for m in s.PREVIEW_MODEL_REGISTRY:current[(m,INIT)][1]['hourly']['precipitation'][13:19]=[.2]*6
    f=s.build_forecast(INIT,issued,'a'*64,current,hist,obs,{},{});v=f['integrated']['vectors']
    for h in range(13,25):obs[INIT+timedelta(hours=h)]={**obs_at(INIT),'precipitation':.3 if h<=18 else 0.}
    before=s.verify_forecasts([f],obs,issued,{})['integrated']
    assert before['verified_vectors']==0 and before['pending_or_unobserved_vectors']==len(v)
    result=s.verify_forecasts([f],obs,INIT+timedelta(hours=24),{})['integrated']
    assert result['verified_vectors']==2 and result['distinct_wet_valid_times']==1
    assert result['all_three_fits_active_vectors']==2 and not result['review_minimum_sample_met']
    q=next(x for x in result['scores'] if x['parameter']=='pressure_msl' and x['lead_bucket']=='all' and x['observed_regime']=='wet')
    assert q['loss']['raw']==pytest.approx(1) and q['loss']['corrected']==pytest.approx(0) and q['loss']['guarded']==pytest.approx(1)
    j=next(x for x in result['joint_scores'] if x['lead_bucket']=='all' and x['observed_regime']=='all')
    assert j['guard_hits']==1 and j['guard_misses']==0 and j['guard_false_alarms']==0
    # Removing one observation constituent excludes the entire joint vector.
    obs[INIT+timedelta(hours=24)]['dew_point_2m']=None
    assert s.verify_forecasts([f],obs,INIT+timedelta(hours=24),{})['integrated']['verified_vectors']==1
