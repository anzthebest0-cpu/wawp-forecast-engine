from src.db_manager import ForecastDB
from src.forecast_selection import (
    SELECTION_CONTRACT_VERSION,
    select_latest_clean_collections,
)


def _row(model: str, cycle: str, scraped: str, valid_wita: str, *, cloud_cover: float = 20.0) -> dict:
    return {
        "location": "Bandara_Sangia_Ni_Bandera",
        "model": model,
        "run_init_utc": cycle,
        "forecast_time": valid_wita,
        "lead_hours": 1.0,
        "scraped_at": scraped,
        "temperature": 27.0,
        "dewpoint": 23.0,
        "pressure_msl": 1010.0,
        "rain": 0.0,
        "precipitation": 0.0,
        "showers": 0.0,
        "snowfall": 0.0,
        "wind_speed": 4.0,
        "wind_gust": 7.0,
        "wind_dir": 100.0,
        "cloud_cover": cloud_cover,
        "cloud_cover_low": 10.0,
        "cloud_cover_mid": 10.0,
        "cloud_cover_high": 10.0,
        "weather_code": 0,
        "visibility": 10000.0,
        "lifted_index": -1.0,
    }


def test_asof_selector_prevents_future_collection_leakage(tmp_path):
    db = ForecastDB(str(tmp_path / "selection.sqlite"))
    try:
        db.ingest_openmeteo_rows([
            _row("ECMWF_HRES", "2026-07-01 00:00:00", "2026-07-01 00:05:00", "2026-07-01 09:00:00"),
            _row("ECMWF_HRES", "2026-07-01 06:00:00", "2026-07-01 06:05:00", "2026-07-01 09:00:00"),
        ])

        rows, audit = select_latest_clean_collections(
            db.conn,
            ["ECMWF_HRES"],
            as_of_utc="2026-07-01 05:00:00",
            minimum_cycle_rows=1,
        )

        assert set(rows["scraped_at"]) == {"2026-07-01 00:05:00"}
        assert audit["selection_contract_version"] == SELECTION_CONTRACT_VERSION
        assert audit["selected_model_count"] == 1
    finally:
        db.close()


def test_asof_selector_rejects_entire_latest_anomalous_cycle(tmp_path):
    db = ForecastDB(str(tmp_path / "quarantine.sqlite"))
    try:
        db.ingest_openmeteo_rows([
            _row("ECMWF_HRES", "2026-07-01 00:00:00", "2026-07-01 00:05:00", "2026-07-01 09:00:00"),
            _row("ECMWF_HRES", "2026-07-01 06:00:00", "2026-07-01 06:05:00", "2026-07-01 09:00:00"),
            _row(
                "ECMWF_HRES",
                "2026-07-01 06:00:00",
                "2026-07-01 06:05:00",
                "2026-07-01 10:00:00",
                cloud_cover=140.0,
            ),
        ])

        rows, audit = select_latest_clean_collections(
            db.conn, ["ECMWF_HRES"], minimum_cycle_rows=1
        )

        assert set(rows["scraped_at"]) == {"2026-07-01 00:05:00"}
        model = audit["models"][0]
        assert model["rejected_hard_anomaly_cycle_count"] == 1
        assert model["selection_changed_from_latest"] is True
    finally:
        db.close()


def test_asof_selector_is_deterministic_when_scrape_times_tie(tmp_path):
    db = ForecastDB(str(tmp_path / "tie.sqlite"))
    try:
        db.ingest_openmeteo_rows([
            _row("GFS_GLOBAL", "2026-07-01 00:00:00", "2026-07-01 06:05:00", "2026-07-01 09:00:00"),
            _row("GFS_GLOBAL", "2026-07-01 06:00:00", "2026-07-01 06:05:00", "2026-07-01 10:00:00"),
        ])

        rows, audit = select_latest_clean_collections(
            db.conn, ["GFS_GLOBAL"], minimum_cycle_rows=1
        )

        assert set(rows["run_init_utc"]) == {"2026-07-01 06:00:00"}
        assert audit["models"][0]["selected_collection_cycle_utc"] == "2026-07-01 06:00:00"
    finally:
        db.close()


def test_asof_selector_rejects_incomplete_clean_cycle(tmp_path):
    db = ForecastDB(str(tmp_path / "incomplete.sqlite"))
    try:
        db.ingest_openmeteo_rows([
            _row("ECMWF_HRES", "2026-07-01 00:00:00", "2026-07-01 00:05:00", "2026-07-01 09:00:00"),
            _row("ECMWF_HRES", "2026-07-01 06:00:00", "2026-07-01 06:05:00", "2026-07-01 10:00:00"),
        ])

        rows, audit = select_latest_clean_collections(
            db.conn, ["ECMWF_HRES"], minimum_cycle_rows=2
        )

        assert rows.empty
        assert audit["models"][0]["rejected_incomplete_cycle_count"] == 2
        assert audit["models"][0]["status"] == "no_clean_cycle"
    finally:
        db.close()
