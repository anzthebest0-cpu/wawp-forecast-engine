import hashlib
import json
import sqlite3
import urllib.error
from contextlib import closing
from datetime import datetime, timezone, timedelta

import pytest

from src.db_manager import ForecastDB
from src.model_acquisition import (CORE, FIELDS, RateLimited, RequestRejected, collect, ingest_immutable,
                                   parse_response, request_bytes, retry_after, iso)
from scripts.collect_model_preview import archive_responses
from scripts.verify_preview_append import verify

MODEL = "ECMWF_HRES"
NOW = datetime(2026, 11, 23, 16, 5, tzinfo=timezone.utc)


def payload(*, temp=28, time_count=30, unit="°C", precip=None):
    times = [(NOW.replace(minute=0) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(time_count)]
    return json.dumps({"latitude": -4.34, "longitude": 121.52, "utc_offset_seconds": 0,
                       "hourly_units": {"temperature_2m": unit, "precipitation": "mm", "wind_speed_10m": "kn"},
                       "hourly": {"time": times, "temperature_2m": [temp] * time_count,
                                  "precipitation": [precip] * time_count, "wind_speed_10m": [4] * time_count}}).encode()


def response(body=None, *, now=NOW):
    body = body or payload()
    rows, detail = parse_response(body, MODEL, now, tuple(CORE))
    meta = {"model": MODEL, "retrieved_at_utc": iso(now), "payload_sha256": hashlib.sha256(body).hexdigest(),
            "row_count": len(rows), "requested_fields": list(CORE), **detail}
    return meta, body, rows


def test_time_axis_missing_rain_and_units():
    rows, detail = parse_response(payload(), MODEL, NOW, tuple(CORE))
    assert len(rows) == 29
    assert rows[0]["valid_time_utc"] == "2026-11-23 17:00:00"
    assert rows[0]["forecast_time"] == "2026-11-24 01:00:00"
    assert rows[0]["rain"] is None and rows[0]["temperature"] == 28
    rows, detail = parse_response(payload(unit="°F", precip=0), MODEL, NOW, tuple(CORE))
    assert rows[0]["temperature"] is None and rows[0]["rain"] == 0
    assert "temperature_2m" in detail["unknown_or_unavailable_fields"]
    broken = json.loads(payload()); broken["hourly"]["time"][1] = broken["hourly"]["time"][0]
    with pytest.raises(ValueError, match="non-contiguous"):
        parse_response(json.dumps(broken).encode(), MODEL, NOW, tuple(CORE))


def test_bounded_retry_and_rate_limit():
    calls, sleeps = [], []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, _): return b'{}'
    def opener(request, timeout):
        calls.append(timeout)
        if len(calls) == 1: raise urllib.error.HTTPError(request.full_url, 429, 'slow', {'Retry-After': '7'}, None)
        return Response()
    assert request_bytes('https://example.invalid', deadline=100, opener=opener, monotonic=lambda: 0,
                         sleeper=sleeps.append, clock=lambda: NOW)[0] == b'{}'
    assert sleeps == [7] and len(calls) == 2
    calls.clear(); sleeps.clear()
    with pytest.raises(RateLimited):
        request_bytes('https://example.invalid', deadline=5, opener=opener, monotonic=lambda: 0,
                      sleeper=sleeps.append, clock=lambda: NOW)
    assert len(calls) == 1 and not sleeps
    assert retry_after('Tue, 24 Nov 2026 16:05:00 GMT', NOW) == 86400


def test_schema_fallback_and_global_rate_limit():
    calls = []
    def requester(url, **kwargs):
        calls.append(url)
        if len(calls) == 1: raise RequestRejected('400')
        if len(calls) == 2: return payload(), NOW
        raise RateLimited('429')
    responses, outcomes = collect(models=(MODEL, 'GFS_GLOBAL', 'ICON_SEAMLESS'), requester=requester)
    assert len(responses) == 1 and len(calls) == 3
    assert outcomes[0]['request_profile'] == 'core'
    assert outcomes[1]['reason'] == 'provider_rate_limit' and outcomes[2]['status'] == 'not_attempted'


def test_archive_and_immutable_same_hour_collections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / 'rclone.conf'; cfg.write_text('[wawp]\ntype = local\n')
    monkeypatch.setenv('RCLONE_CONFIG', str(cfg))
    path = tmp_path / 'db.sqlite'; db = ForecastDB(str(path)); db.close()
    first = response(); second = response(payload(temp=29), now=NOW+timedelta(seconds=1))
    restored, uri, digest = archive_responses([first], [], tmp_path/'work', 'wawp:validation/fresh-acquisition/test')
    assert restored == [first] and (tmp_path/'validation/fresh-acquisition/test'/uri.split('/')[-1]).exists()
    with closing(sqlite3.connect(path)) as conn:
        assert ingest_immutable(conn, restored, evidence_uri=uri, evidence_sha256=digest) == 29
        assert ingest_immutable(conn, restored, evidence_uri=uri, evidence_sha256=digest) == 0
        assert ingest_immutable(conn, [second], evidence_uri='test:verified', evidence_sha256='test-sha') == 29
        before = conn.execute('SELECT COUNT(*) FROM openmeteo_forecasts').fetchone()[0]
        with pytest.raises(ValueError, match='Different payload'):
            ingest_immutable(conn, [response(payload(temp=30))], evidence_uri='test', evidence_sha256='test')
        assert conn.execute('SELECT COUNT(*) FROM openmeteo_forecasts').fetchone()[0] == before == 58
        assert conn.execute('SELECT COUNT(*) FROM openmeteo_collection_receipts').fetchone()[0] == 2


def test_append_verifier_rejects_changed_original_row(tmp_path):
    import shutil
    baseline = tmp_path/'baseline.db'; db = ForecastDB(str(baseline)); db.close()
    with closing(sqlite3.connect(baseline)) as conn:
        ingest_immutable(conn, [response()], evidence_uri='test', evidence_sha256='test')
    candidate = tmp_path/'candidate.db'; shutil.copyfile(baseline, candidate)
    with closing(sqlite3.connect(candidate)) as conn:
        ingest_immutable(conn, [response(now=NOW+timedelta(seconds=1))], evidence_uri='test', evidence_sha256='test')
    assert verify(baseline, candidate, 29)['status'] == 'passed'
    with closing(sqlite3.connect(candidate)) as conn:
        conn.execute('UPDATE openmeteo_forecasts SET temperature=99 WHERE id=1'); conn.commit()
    with pytest.raises(ValueError, match='Original records changed'):
        verify(baseline, candidate, 29)
