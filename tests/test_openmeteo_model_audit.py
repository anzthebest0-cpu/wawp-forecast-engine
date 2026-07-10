from datetime import datetime, timedelta

from src.db_manager import ForecastDB
from src.model_registry import freshness_status, model_metadata_dict


def _row(model: str, forecast_time: datetime, scraped_at: str) -> dict:
    return {
        "location": "Bandara_Sangia_Ni_Bandera",
        "model": model,
        "run_init_utc": "2026-07-06 00:00:00",
        "forecast_time": forecast_time.strftime("%Y-%m-%d %H:%M:%S"),
        "lead_hours": (forecast_time - datetime(2026, 7, 6, 8)).total_seconds() / 3600.0,
        "scraped_at": scraped_at,
        "temperature": 28.0,
        "dewpoint": 23.0,
        "humidity": 75.0,
        "pressure_msl": 1010.0,
        "rain": 0.0,
        "precipitation": 0.0,
        "showers": 0.0,
        "snowfall": 0.0,
        "wind_speed": 5.0,
        "wind_gust": 8.0,
        "wind_dir": 110.0,
        "cloud_cover": 30.0,
        "cloud_cover_low": 10.0,
        "cloud_cover_mid": 20.0,
        "cloud_cover_high": 5.0,
        "weather_code": 0,
        "visibility": 10000.0,
        "cape": 100.0,
        "lifted_index": 1.0,
        "convective_inhib": 0.0,
        "boundary_layer_h": 700.0,
        "precipitation_probability": 5.0,
    }


def test_openmeteo_model_run_audit_records_cadence(tmp_path):
    db = ForecastDB(str(tmp_path / "audit.sqlite"))
    scraped_at = "2026-07-06 00:05:00"
    start = datetime(2026, 7, 6, 8)
    rows = [_row("ECMWF_HRES", start + timedelta(hours=i), scraped_at) for i in range(4)]

    processed = db.ingest_openmeteo_rows(rows)
    assert processed == 4
    audit = db.conn.execute("""
        SELECT model, row_count, detected_interval_hours, provider_update_frequency_hours,
               expected_output_interval_hours, quality_status
        FROM openmeteo_model_runs
        WHERE model='ECMWF_HRES'
    """).fetchone()

    assert audit is not None
    assert audit[0] == "ECMWF_HRES"
    assert audit[1] == 4
    assert audit[2] == 1.0
    assert audit[3] == 6.0
    assert audit[4] == 1.0
    assert audit[5] == "partial"
    db.conn.close()


def test_freshness_status_uses_model_cadence():
    assert freshness_status(4.0, model_metadata_dict("ECMWF_HRES")["provider_update_frequency_hours"]) == "fresh"
    assert freshness_status(4.0, model_metadata_dict("GFS_GLOBAL")["provider_update_frequency_hours"]) == "stale"
