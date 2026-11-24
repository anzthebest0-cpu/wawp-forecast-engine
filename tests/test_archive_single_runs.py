import hashlib
import json
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from src import archive_single_runs as a


def fake_payload(run):
    times=[(run+timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in range(24)]
    return json.dumps({'latitude':-4.338158,'longitude':121.524047,'elevation':10,'utc_offset_seconds':0,
        'hourly':{'time':times,'temperature_2m':[27]*24,'wind_speed_10m':[3]*24,'precipitation':[None]+[0]*23},
        'hourly_units':{'temperature_2m':'°C','wind_speed_10m':'kn','precipitation':'mm'}},ensure_ascii=False).encode()


def test_plan_uses_all_9_models_only_after_gap_and_two_ifs_before():
    rows=list(a.run_plan(date(2026,8,19),date(2026,11,23),date(2026,10,1)))
    assert len(rows)==2180 and len(set(rows))==2180
    assert {m for m,r in rows if r.date()==date(2026,8,19)}==set(a.IFS_MODELS)
    assert len({m for m,r in rows if r.date()==date(2026,10,1)})==9
    assert {r.hour for m,r in rows if m=='GEM_GLOBAL'}=={0,12}


def test_inspects_true_axis_and_preserves_missing_precipitation():
    run=datetime(2026,10,31,6,tzinfo=timezone.utc)
    body=fake_payload(run)
    detail=a.inspect_response(body,a.IFS_9KM_MODEL,run)
    assert detail['nonnull_counts']['precipitation']==23
    assert detail['last_populated_lead_hours']['precipitation']==23
    with pytest.raises(ValueError):
        a.inspect_response(body,a.IFS_9KM_MODEL,run+timedelta(hours=6))
    p=json.loads(body);p['hourly_units']['wind_speed_10m']='km/h'
    with pytest.raises(ValueError):
        a.inspect_response(json.dumps(p).encode(),a.IFS_9KM_MODEL,run)


def test_resume_verifies_exact_bytes_and_does_not_redownload(tmp_path,monkeypatch):
    calls=[]
    def fake(model,run,pacer):
        calls.append((model,run))
        b=fake_payload(run)
        info={'model':model,'provider_model_id':a.PREVIEW_MODEL_REGISTRY[model].openmeteo_id,
              'initialization_utc':run.isoformat(),'request_url':a.request_url(model,run),'status':'accepted',
              'retrieved_at_utc':'2026-11-24T00:00:00+00:00','payload_bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),
              **a.inspect_response(b,model,run)}
        return info,b
    monkeypatch.setattr(a,'fetch_one',fake)
    d=date(2026,8,19); g=date(2026,10,1); first=tmp_path/'first.zip'; second=tmp_path/'second.zip'
    a.recover_batch(d,d,g,first)
    assert len(calls)==8
    a.recover_batch(d,d,g,second,resume=[first])
    assert len(calls)==8
    assert a.verify_bundle(first)==a.verify_bundle(second)
    with zipfile.ZipFile(first) as x, zipfile.ZipFile(second) as y:
        assert all(x.read(n)==y.read(n) for n in x.namelist())
