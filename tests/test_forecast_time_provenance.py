import os
import sqlite3

import pandas as pd

import src.scrape_openmeteo as scrape_openmeteo
from src.advanced_ensemble_weighter import AdvancedEnsembleWeighter
from src.build_qm_training_pairs import build_training_pairs
from src.db_manager import ForecastDB
from src.forecast_time_provenance import (
    TIME_CONTRACT_VERSION,
    ensure_time_provenance_schema,
    normalize_row_time_fields,
)
from src.operational_residuals import operational_pairing_audit, query_operational_pairs
from src.quantile_mapper import _ensure_qm_schema, fit_multiparam_qm_to_db


def _operational_row(local_time: str, *, cloud_cover: float = 20.0) -> dict:
    return {
        "location": "Bandara_Sangia_Ni_Bandera",
        "model": "ECMWF_HRES",
        "run_init_utc": "2026-07-01 00:00:00",
        "forecast_time": local_time,
        "lead_hours": 0.0,
        "scraped_at": "2026-07-01 00:05:00",
        "temperature": 27.0,
        "rain": 0.0,
        "precipitation": 0.0,
        "showers": 0.0,
        "snowfall": 0.0,
        "cloud_cover": cloud_cover,
        "cloud_cover_low": 10.0,
        "cloud_cover_mid": 10.0,
        "cloud_cover_high": 10.0,
        "weather_code": 0,
        "visibility": 9999.0,
        "lifted_index": -1.0,
    }


def _set_cycle(row: dict, cycle: str, scraped_at: str) -> dict:
    result = dict(row)
    result["run_init_utc"] = cycle
    result["scraped_at"] = scraped_at
    return result


def test_normalize_row_time_fields_distinguishes_operational_wita_and_historical_utc():
    operational = normalize_row_time_fields(_operational_row("2026-07-01 08:00:00"))
    historical = normalize_row_time_fields({
        "run_init_utc": "historical_forecast_api",
        "forecast_time": "2026-07-01 00:00:00",
    })

    assert operational["valid_time_utc"] == "2026-07-01 00:00:00"
    assert operational["forecast_time_basis"] == "forecast_api_wita"
    assert historical["valid_time_utc"] == "2026-07-01 00:00:00"
    assert historical["forecast_time_basis"] == "historical_api_utc"


def test_scraper_emits_wita_display_and_utc_verification_time():
    original_get_json = scrape_openmeteo._get_json
    scrape_openmeteo._get_json = lambda _url: {
        "hourly": {
            "time": ["2026-07-01T08:00"],
            "temperature_2m": [27.0],
            "precipitation": [0.0],
        }
    }
    try:
        dashboard_rows, rows = scrape_openmeteo.fetch_model("ECMWF_HRES", "ecmwf_ifs025", 1)
    finally:
        scrape_openmeteo._get_json = original_get_json

    assert rows[0]["forecast_time"] == "2026-07-01 08:00:00"
    assert rows[0]["valid_time_utc"] == "2026-07-01 00:00:00"
    assert rows[0]["forecast_time_basis"] == "forecast_api_wita"
    assert dashboard_rows[0]["Datetime"] == "2026-07-01 08:00:00"
    assert dashboard_rows[0]["Valid_Time_UTC"] == "2026-07-01 00:00:00"


def test_schema_backfills_only_operational_rows():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE openmeteo_forecasts "
        "(run_init_utc TEXT, forecast_time TEXT)"
    )
    conn.executemany(
        "INSERT INTO openmeteo_forecasts VALUES (?, ?)",
        [
            ("2026-07-01 00:00:00", "2026-07-01 08:00:00"),
            ("historical_forecast_api", "2026-07-01 00:00:00"),
        ],
    )

    status = ensure_time_provenance_schema(conn)
    rows = conn.execute(
        "SELECT run_init_utc, valid_time_utc, forecast_time_basis "
        "FROM openmeteo_forecasts ORDER BY run_init_utc"
    ).fetchall()

    assert status["contract_version"] == TIME_CONTRACT_VERSION
    assert status["operational_rows_backfilled"] == 1
    assert "operational UTC" in status["index_policy"]
    assert rows[0][1:] == ("2026-07-01 00:00:00", "forecast_api_wita")
    assert rows[1][1:] == (None, None)
    index_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_om_valid_utc'"
    ).fetchone()[0]
    assert "WHERE valid_time_utc IS NOT NULL" in index_sql


def test_pairing_audit_supports_pre_migration_database_schema():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE openmeteo_forecasts (
            location TEXT, model TEXT, run_init_utc TEXT, forecast_time TEXT,
            scraped_at TEXT, lead_hours REAL, rain REAL, precipitation REAL, showers REAL,
            snowfall REAL, cloud_cover REAL, cloud_cover_low REAL,
            cloud_cover_mid REAL, cloud_cover_high REAL, visibility REAL,
            weather_code INTEGER, lifted_index REAL
        )
        """
    )
    conn.execute(
        "CREATE TABLE awos_observations (location TEXT, obs_time TEXT)"
    )
    conn.execute(
        """
        INSERT INTO openmeteo_forecasts VALUES (
            'Bandara_Sangia_Ni_Bandera', 'ECMWF_HRES', '2026-07-01 00:00:00',
            '2026-07-01 08:00:00', '2026-07-01 00:05:00',
            0, 0, 0, 0, 0, 20, 10, 10, 10, 9999, 0, -1
        )
        """
    )
    conn.execute(
        "INSERT INTO awos_observations VALUES "
        "('Bandara_Sangia_Ni_Bandera', '2026-07-01 00:00:00')"
    )

    audit = operational_pairing_audit(
        conn,
        "2026-07-01 00:00:00",
        "2026-07-01 01:00:00",
        ["ECMWF_HRES"],
    )

    assert audit["physically_aligned_pair_rows"] == 1
    assert audit["legacy_naive_text_pair_rows"] == 0
    assert audit["explicit_valid_time_utc_rows"] == 0


def test_operational_pairs_use_utc_and_quarantine_hard_anomalies(tmp_path):
    db = ForecastDB(str(tmp_path / "pairs.sqlite"))
    try:
        db.ingest_openmeteo_rows([
            _operational_row("2026-07-01 08:00:00"),
            _set_cycle(
                _operational_row("2026-07-01 09:00:00", cloud_cover=140.0),
                "2026-07-01 01:00:00",
                "2026-07-01 01:05:00",
            ),
        ])
        db.conn.executemany(
            """
            INSERT INTO awos_observations (
                location, obs_time, temperature, rain_1h
            ) VALUES (?, ?, ?, ?)
            """,
            [
                ("Bandara_Sangia_Ni_Bandera", "2026-07-01 00:00:00", 28.0, 0.0),
                ("Bandara_Sangia_Ni_Bandera", "2026-07-01 01:00:00", 29.0, 0.0),
            ],
        )
        db.conn.commit()

        pairs = query_operational_pairs(
            db.conn,
            "Temperature",
            "2026-07-01 00:00:00",
            "2026-07-01 02:00:00",
            ["ECMWF_HRES"],
        )
        audit = operational_pairing_audit(
            db.conn,
            "2026-07-01 00:00:00",
            "2026-07-01 02:00:00",
            ["ECMWF_HRES"],
        )
        build_training_pairs(db)
        operational_training = db.conn.execute(
            """
            SELECT valid_time, COUNT(*) FROM qm_training_pairs
            WHERE source_type='operational_multiinit'
            GROUP BY valid_time
            """
        ).fetchall()

        assert len(pairs) == 1
        assert pairs.iloc[0]["Datetime"] == "2026-07-01 00:00:00"
        assert pairs.iloc[0]["Local_Datetime"] == "2026-07-01 08:00:00"
        assert audit["physically_aligned_pair_rows"] == 1
        assert audit["legacy_naive_text_pair_rows"] == 0
        assert audit["hard_anomaly_rows_excluded"] == 1
        assert audit["whole_cycle_quarantine"] is True
        assert operational_training == [("2026-07-01 00:00:00", 1)]
    finally:
        db.close()


def test_one_hard_anomaly_quarantines_the_entire_operational_cycle(tmp_path):
    db = ForecastDB(str(tmp_path / "whole-cycle.sqlite"))
    try:
        db.ingest_openmeteo_rows([
            _operational_row("2026-07-01 08:00:00"),
            _operational_row("2026-07-01 09:00:00", cloud_cover=140.0),
        ])
        db.conn.executemany(
            """
            INSERT INTO awos_observations (location, obs_time, temperature, rain_1h)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("Bandara_Sangia_Ni_Bandera", "2026-07-01 00:00:00", 28.0, 0.0),
                ("Bandara_Sangia_Ni_Bandera", "2026-07-01 01:00:00", 29.0, 0.0),
            ],
        )
        db.conn.commit()

        pairs = query_operational_pairs(
            db.conn,
            "Temperature",
            "2026-07-01 00:00:00",
            "2026-07-01 02:00:00",
            ["ECMWF_HRES"],
        )
        operational_count = build_training_pairs(db)

        assert pairs.empty
        assert operational_count == 0
    finally:
        db.close()


def test_legacy_timing_weights_are_contained_by_default():
    previous = os.environ.pop("WAWP_LEGACY_TIMING_WEIGHTING_MODE", None)
    try:
        weighter = AdvancedEnsembleWeighter(models=["ECMWF_HRES", "GFS_GLOBAL"])
        base = {"ECMWF_HRES": 0.7, "GFS_GLOBAL": 0.3}
        frame = pd.DataFrame({
            "WITA_Target": pd.date_range("2026-07-01", periods=6, freq="h"),
            "Model": ["ECMWF_HRES"] * 6,
            "Rain": [2.0] * 6,
            "OBS_Rain": [2.0] * 6,
        })

        result = weighter.calculate_timing_weights(frame, base_w=base)

        assert result == base
        assert weighter.timing_weights["status"] == "deprecated_observe_only"
        assert weighter.timing_weights["applied"] is False
    finally:
        if previous is not None:
            os.environ["WAWP_LEGACY_TIMING_WEIGHTING_MODE"] = previous


def test_historical_training_regime_uses_wita_not_utc(tmp_path):
    db = ForecastDB(str(tmp_path / "historical-regime.sqlite"))
    try:
        row = _operational_row("2026-07-01 00:00:00")
        row["run_init_utc"] = "historical_forecast_api"
        db.ingest_openmeteo_rows([row])
        db.conn.execute(
            """
            INSERT INTO awos_observations (
                location, obs_time, temperature, rain_1h
            ) VALUES (?, ?, ?, ?)
            """,
            ("Bandara_Sangia_Ni_Bandera", "2026-07-01 00:00:00", 28.0, 0.0),
        )
        db.conn.commit()

        build_training_pairs(db)
        regime = db.conn.execute(
            "SELECT regime FROM qm_training_pairs WHERE source_type='continuous_historical'"
        ).fetchone()[0]

        assert regime == "morning_06_11"
    finally:
        db.close()


def test_pre_contract_operational_qm_is_deprecated_but_current_contract_survives():
    conn = sqlite3.connect(":memory:")
    _ensure_qm_schema(conn)
    conn.execute(
        """
        CREATE TABLE qm_training_pairs (
            model TEXT,
            lead_bucket TEXT,
            lead_bucket_gust TEXT,
            source_type TEXT,
            correction_layer TEXT,
            valid_time TEXT,
            fcst_temperature REAL, obs_temperature REAL,
            fcst_dewpoint REAL, obs_dewpoint REAL,
            fcst_pressure REAL, obs_pressure REAL,
            fcst_wind_speed REAL, obs_wind_speed REAL,
            fcst_wind_gust REAL, obs_wind_gust REAL,
            fcst_wind_dir REAL, obs_wind_dir REAL,
            fcst_rain REAL, obs_rain REAL
        )
        """
    )
    base_values = (
        "temperature", "L1_0_6h", "[20, 30]", "[20, 30]", 100,
        "2026-07-01T00:00:00+00:00", "operational_multiinit", "operational_residual",
    )
    conn.execute(
        """
        INSERT INTO qm_cdfs (
            model, parameter, lead_bucket, fcst_quantiles, obs_quantiles,
            n_samples, trained_at, enabled, deprecated, metadata,
            source_type, correction_layer
        ) VALUES ('ECMWF_HRES', ?, ?, ?, ?, ?, ?, 1, 0, '{}', ?, ?)
        """,
        base_values,
    )
    conn.execute(
        """
        INSERT INTO qm_cdfs (
            model, parameter, lead_bucket, fcst_quantiles, obs_quantiles,
            n_samples, trained_at, enabled, deprecated, metadata,
            source_type, correction_layer
        ) VALUES ('GFS_GLOBAL', ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
        """,
        (*base_values[:6], '{"pairing_contract_version":"openmeteo-valid-time-utc-v1"}', *base_values[6:]),
    )

    fit_multiparam_qm_to_db(conn, log_fn=lambda _message: None)
    rows = dict(
        conn.execute(
            "SELECT model, enabled || ':' || deprecated FROM qm_cdfs ORDER BY model"
        ).fetchall()
    )

    assert rows["ECMWF_HRES"] == "0:1"
    assert rows["GFS_GLOBAL"] == "1:0"
