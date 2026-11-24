import json
from contextlib import closing
from datetime import datetime, timezone, timedelta

from src.db_manager import ForecastDB, LOCATION_NAME
from src.model_preview import export_preview, WINDOWS
from src.model_preview_gate import validate
from src import scrape_openmeteo, backfill_historical_forecast_api


NOW = datetime(2026, 9, 15, 17, 16, 35, tzinfo=timezone.utc)
MODELS = ("ECMWF_HRES", "GFS_GLOBAL")


def _rows(model, *, pull=None, precipitation=None, rain=0.0, direction=100.0):
    pull = pull or NOW - timedelta(minutes=15)
    for i in range(30):
        valid = NOW.replace(minute=0, second=0) + timedelta(hours=i)
        yield dict(location=LOCATION_NAME, model=model, run_init_utc=pull.strftime("%Y-%m-%d %H:00:00"),
                   scraped_at=pull.strftime("%Y-%m-%d %H:%M:%S"),
                   forecast_time=(valid + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
                   valid_time_utc=valid.strftime("%Y-%m-%d %H:%M:%S"), forecast_time_basis="forecast_api_wita",
                   lead_hours=i, wind_dir=direction, wind_speed=4.0, temperature=28.0, dewpoint=24.0,
                   rain=rain, precipitation=precipitation, visibility=None, cloud_cover_low=None)


def _database(tmp_path):
    path = tmp_path / "source.db"
    db = ForecastDB(str(path))
    try:
        db.ingest_openmeteo_rows(list(_rows(MODELS[0])) + list(_rows(MODELS[1], pull=NOW - timedelta(days=1), precipitation=0.0)))
    finally:
        db.close()
    return path


def test_fresh_preview_preserves_missing_and_withholds_all_drafts(tmp_path):
    source = _database(tmp_path)
    out = tmp_path / "preview"
    import sqlite3
    with closing(sqlite3.connect(source)) as conn:
        manifest = export_preview(conn, out, now=NOW, models=MODELS)
    assert list(manifest["models"]) == [MODELS[0]]
    assert manifest["row_count"] == 29
    preview = json.loads((out / "taf_guidance.json").read_text())
    assert all(x["Rain"] is None and x["Visibility"] is None for x in preview["data"])
    assert all(x["Field_Model_Count"]["Rain"] == 0 for x in preview["data"])
    taf = json.loads((out / "tafor_intel.json").read_text())
    assert set(taf) == set(WINDOWS) and all(x["taf_text"] is None for x in taf.values())
    assert validate(source, out, models=MODELS, now=NOW)["passed"]
    assert not validate(source, out, models=MODELS, now=NOW + timedelta(hours=7))["passed"]
    taf["default"]["taf_text"] = "TAF WAWP INVALID="
    (out / "tafor_intel.json").write_text(json.dumps(taf))
    assert "unsafe_taf_payload" in validate(source, out, models=MODELS, now=NOW)["issues"]


def test_expired_archive_yields_no_current_data(tmp_path):
    source = _database(tmp_path)
    out = tmp_path / "expired"
    tomorrow = NOW + timedelta(days=1)
    import sqlite3
    with closing(sqlite3.connect(source)) as conn:
        manifest = export_preview(conn, out, now=tomorrow, models=MODELS)
    assert manifest["status"] == "unavailable"
    assert json.loads((out / "taf_guidance.json").read_text())["data"] == []
    assert validate(source, out, models=MODELS, now=tomorrow)["passed"]


def test_tampered_model_payload_fails_exact_gate(tmp_path):
    source = _database(tmp_path)
    out = tmp_path / "tamper"
    import sqlite3
    with closing(sqlite3.connect(source)) as conn:
        export_preview(conn, out, now=NOW, models=MODELS)
    guidance = json.loads((out / "taf_guidance.json").read_text())
    guidance["data"][0]["Rain"] = 0.0
    (out / "taf_guidance.json").write_text(json.dumps(guidance))
    assert "numeric_product_mismatch" in validate(source, out, models=MODELS, now=NOW)["issues"]


def test_provider_missing_precipitation_not_fabricated(monkeypatch):
    payload = {"hourly": {"time": ["2026-09-15T00:00", "2026-09-15T01:00", "2026-09-15T02:00"],
               "precipitation": [None, 0.0, 2.3], "rain": [None, 0.0, 2.0], "showers": [None, 0.0, 0.3]}}
    monkeypatch.setattr(scrape_openmeteo, "_get_json", lambda _: payload)
    dashboard, native = scrape_openmeteo.fetch_model(MODELS[0], "ecmwf_ifs025")
    historical = backfill_historical_forecast_api._rows_from_payload(MODELS[0], payload)
    assert [r["Rain"] for r in dashboard] == [None, 0.0, 2.3]
    assert [r["rain"] for r in native] == [None, 0.0, 2.3]
    assert [r["rain"] for r in historical] == [None, 0.0, 2.3]
    assert native[0]["showers"] is None and historical[0]["snowfall"] is None


def test_ecmwf_feed_metadata_no_longer_claims_native_hres():
    from src.model_registry import model_metadata_dict
    from src.rainfall_shadow_replay import NATIVE_CADENCE_HOURS
    model = model_metadata_dict("ECMWF_HRES")
    assert model["openmeteo_id"] == "ecmwf_ifs025"
    assert "0.25°" in model["provider"] and model["native_temporal_resolution"].startswith("3h")
    assert NATIVE_CADENCE_HOURS["ECMWF_HRES"] == 3


def test_ifs_9km_is_independent_and_does_not_double_weight_ecmwf(tmp_path):
    import sqlite3
    from src.model_registry import MODEL_REGISTRY, PREVIEW_MODEL_REGISTRY, IFS_9KM_MODEL
    from src.model_acquisition import request_url
    assert IFS_9KM_MODEL not in MODEL_REGISTRY
    assert PREVIEW_MODEL_REGISTRY[IFS_9KM_MODEL].openmeteo_id == "ecmwf_ifs"
    assert PREVIEW_MODEL_REGISTRY["ECMWF_HRES"].openmeteo_id == "ecmwf_ifs025"
    assert "models=ecmwf_ifs&" in request_url(IFS_9KM_MODEL, ["temperature_2m"])
    path = tmp_path / "independent.db"
    db = ForecastDB(str(path))
    try:
        db.ingest_openmeteo_rows(list(_rows("ECMWF_HRES", precipitation=1.0)) +
                                [dict(r, temperature=40.0, precipitation=20.0) for r in _rows(IFS_9KM_MODEL)])
    finally:
        db.close()
    out = tmp_path / "preview"
    models = ("ECMWF_HRES", IFS_9KM_MODEL)
    with closing(sqlite3.connect(path)) as conn:
        manifest = export_preview(conn, out, now=NOW, models=models)
    assert set(manifest["models"]) == set(models)
    guidance = json.loads((out / "taf_guidance.json").read_text())
    individual = json.loads((out / "individual_models.json").read_text())
    assert all(r["Temperature"] == 28.0 and r["Rain"] == 1.0 and r["Model_Count"] == 1 for r in guidance["data"])
    assert set(individual["Temperature"][IFS_9KM_MODEL].values()) == {40.0}
    assert validate(path, out, models=models, now=NOW)["passed"]
