import json
from pathlib import Path

from src.db_manager import ForecastDB
from src.pipeline_publication_gate import REQUIRED_JSON, validate_publication_candidate


MODELS = tuple(f"MODEL_{index}" for index in range(6))


def _forecast(model: str, hour: int, *, scraped: str = "2026-07-01 00:05:00", rain: float = 0.0):
    return {
        "location": "Bandara_Sangia_Ni_Bandera",
        "model": model,
        "run_init_utc": "2026-07-01 00:00:00",
        "forecast_time": f"2026-07-01 {hour % 24:02d}:00:00",
        "lead_hours": float(hour),
        "scraped_at": scraped,
        "temperature": 27.0,
        "dewpoint": 23.0,
        "pressure_msl": 1010.0,
        "rain": rain,
        "precipitation": rain,
        "showers": 0.0,
        "snowfall": 0.0,
        "wind_speed": 4.0,
        "wind_gust": 7.0,
        "wind_dir": 100.0,
        "cloud_cover": 20.0,
        "cloud_cover_low": 10.0,
        "cloud_cover_mid": 10.0,
        "cloud_cover_high": 10.0,
        "weather_code": 0,
        "visibility": 10000.0,
        "lifted_index": -1.0,
    }


def _write_dashboard_payloads(data_dir: Path):
    data_dir.mkdir(parents=True)
    for name in REQUIRED_JSON:
        payload = {}
        if name == "pipeline_health.json":
            payload = {
                "dashboard_export_skipped": False,
                "dashboard_export_succeeded": True,
            }
        elif name == "db_health.json":
            payload = {"latest_data_pull_utc": "2026-07-01 00:05:00"}
        elif name == "tafor_intel.json":
            payload = {
                window: {"taf_text": "TAF WAWP 010000Z 0100/0200 00000KT 9999 FEW020="}
                for window in ("2300", "0500", "1100", "1700", "default")
            }
        (data_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def test_publication_gate_passes_clean_quorum(tmp_path):
    db = ForecastDB(str(tmp_path / "candidate.db"))
    try:
        db.ingest_openmeteo_rows([
            _forecast(model, hour) for model in MODELS for hour in range(24)
        ])
    finally:
        db.close()
    data_dir = tmp_path / "data"
    _write_dashboard_payloads(data_dir)

    report = validate_publication_candidate(
        tmp_path / "candidate.db", data_dir, models=MODELS
    )

    assert report["passed"] is True
    assert report["selection"]["selected_model_count"] == 6


def test_publication_gate_fails_without_required_json(tmp_path):
    db = ForecastDB(str(tmp_path / "candidate.db"))
    try:
        db.ingest_openmeteo_rows([
            _forecast(model, hour) for model in MODELS for hour in range(24)
        ])
    finally:
        db.close()
    data_dir = tmp_path / "data"
    _write_dashboard_payloads(data_dir)
    (data_dir / "tafor_intel.json").unlink()

    report = validate_publication_candidate(
        tmp_path / "candidate.db", data_dir, models=MODELS
    )

    assert report["passed"] is False
    assert any("tafor_intel.json" in failure for failure in report["hard_failures"])


def test_publication_gate_rejects_stale_dashboard_provenance(tmp_path):
    db = ForecastDB(str(tmp_path / "candidate.db"))
    try:
        db.ingest_openmeteo_rows([
            _forecast(model, hour) for model in MODELS for hour in range(24)
        ])
    finally:
        db.close()
    data_dir = tmp_path / "data"
    _write_dashboard_payloads(data_dir)
    (data_dir / "db_health.json").write_text(
        json.dumps({"latest_data_pull_utc": "2026-06-01 00:00:00"}),
        encoding="utf-8",
    )

    report = validate_publication_candidate(
        tmp_path / "candidate.db", data_dir, models=MODELS
    )

    assert report["passed"] is False
    assert "dashboard_database_provenance_mismatch" in report["hard_failures"]
