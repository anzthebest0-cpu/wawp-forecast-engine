import sqlite3

import pandas as pd

from src.rainfall_shadow_replay import (
    development_selected_ensemble_rows,
    load_historical_rain_pairs,
    rank_models,
    ranking_stability,
    score_frame,
)


def _audit_connection():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE openmeteo_forecasts ("
        "id INTEGER, location TEXT, model TEXT, run_init_utc TEXT, forecast_time TEXT, "
        "rain REAL, cloud_cover REAL, cloud_cover_low REAL, cloud_cover_mid REAL, "
        "cloud_cover_high REAL, weather_code INTEGER, lifted_index REAL)"
    )
    conn.execute(
        "CREATE TABLE awos_observations (location TEXT, obs_time TEXT, rain_1h REAL)"
    )
    return conn


def test_loader_quarantines_duplicates_negative_rain_and_hard_anomalies():
    conn = _audit_connection()
    location = "Bandara_Sangia_Ni_Bandera"
    observations = [(location, f"2026-01-01 0{hour}:00:00", 1.0) for hour in range(4)]
    conn.executemany("INSERT INTO awos_observations VALUES (?, ?, ?)", observations)
    rows = [
        (1, location, "ECMWF_HRES", "historical_forecast_api", "2026-01-01 00:00:00", 1.0, 10, 10, 10, 10, 0, -1),
        (2, location, "ECMWF_HRES", "historical_forecast_api", "2026-01-01 01:00:00", 1.0, 10, 10, 10, 10, 0, -1),
        (3, location, "ECMWF_HRES", "historical_forecast_api", "2026-01-01 01:00:00", 1.0, 10, 10, 10, 10, 0, -1),
        (4, location, "ECMWF_HRES", "historical_forecast_api", "2026-01-01 02:00:00", -0.1, 10, 10, 10, 10, 0, -1),
        (5, location, "ECMWF_HRES", "historical_forecast_api", "2026-01-01 03:00:00", 1.0, 140, 10, 10, 10, 0, -1),
    ]
    conn.executemany("INSERT INTO openmeteo_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    pairs, audit = load_historical_rain_pairs(conn, models=("ECMWF_HRES",))

    assert len(pairs) == 1
    assert audit["duplicate_model_valid_time_rows"] == 2
    assert audit["negative_forecast_rain_rows"] == 1
    assert audit["hard_physical_anomaly_rows"] == 1
    assert audit["quarantined_rows"] == 4


def test_score_frame_keeps_hour_episode_and_three_hour_units_distinct():
    frame = pd.DataFrame({
        "valid_time_utc": pd.date_range("2026-01-01 01:00:00", periods=3, freq="h"),
        "forecast_rain": [0.0, 0.0, 2.0],
        "observed_rain": [0.0, 2.0, 0.0],
    })

    rows = score_frame(frame, "holdout", "TEST", thresholds=(1.5,), native_cadence_hours=3)
    by_product = {row["product"]: row for row in rows}

    assert by_product["strict_hourly"]["hits"] == 0
    assert by_product["episode_pm1h"]["hits"] == 1
    assert by_product["complete_3h_same_numeric"]["hits"] == 1
    assert by_product["complete_3h_rate_equivalent"]["observed_events"] == 0
    assert by_product["cadence_aware_episode"]["cadence_tolerance_hours"] == 2


def test_rank_models_uses_csi_then_hss_then_far():
    base = {
        "split": "development_2023_2025",
        "hourly_threshold_mm": 1.5,
        "product": "strict_hourly",
        "POD": 0.5,
        "frequency_bias": 1.0,
        "observed_events": 10,
    }
    rows = [
        {**base, "model": "ECMWF_HRES", "CSI": 0.3, "HSS": 0.4, "FAR": 0.3},
        {**base, "model": "GFS_GLOBAL", "CSI": 0.4, "HSS": 0.2, "FAR": 0.2},
        {**base, "model": "ICON_SEAMLESS", "CSI": 0.4, "HSS": 0.3, "FAR": 0.4},
    ]

    ranked = rank_models(rows)

    assert [row["model"] for row in ranked] == ["ICON_SEAMLESS", "GFS_GLOBAL", "ECMWF_HRES"]


def test_ranking_stability_compares_the_same_model_without_reselection():
    rows = [
        {
            "split": split, "hourly_threshold_mm": 1.5, "product": "strict_hourly",
            "rank": rank, "model": "ECMWF_HRES", "CSI": csi, "HSS": 0.1,
            "POD": 0.2, "FAR": far, "frequency_bias": 1.0, "observed_events": 10,
        }
        for split, rank, csi, far in (
            ("development_2023_2025", 1, 0.3, 0.4),
            ("holdout_2026_h1", 4, 0.1, 0.8),
        )
    ]

    stability = ranking_stability(rows)

    assert stability[0]["development_rank"] == 1
    assert stability[0]["holdout_rank"] == 4
    assert stability[0]["rank_change_holdout_minus_development"] == 3


def test_development_selection_does_not_use_holdout_rank():
    models = ("ECMWF_HRES", "GFS_GLOBAL", "ICON_SEAMLESS", "GEM_GLOBAL")
    times = pd.date_range("2023-01-01", periods=36, freq="MS").append(
        pd.date_range("2026-01-01", periods=6, freq="MS")
    )
    pairs = pd.concat([
        pd.DataFrame({
            "model": model,
            "valid_time_utc": times,
            "forecast_rain": [0.0] * len(times),
            "observed_rain": [0.0] * len(times),
        })
        for model in models
    ], ignore_index=True)
    metric_rows = []
    for rank, model in enumerate(models, start=1):
        metric_rows.extend([
            {
                "split": "development_2023_2025", "model": model,
                "hourly_threshold_mm": 1.5, "product": "strict_hourly",
                "CSI": 1.0 / rank, "HSS": 0.0, "POD": 0.0, "FAR": 0.0,
                "frequency_bias": 0.0, "observed_events": 0,
            },
            {
                "split": "holdout_2026_h1", "model": model,
                "hourly_threshold_mm": 1.5, "product": "strict_hourly",
                "CSI": float(rank), "HSS": 0.0, "POD": 0.0, "FAR": 0.0,
                "frequency_bias": 0.0, "observed_events": 0,
            },
        ])

    selections, _ = development_selected_ensemble_rows(pairs, metric_rows)

    assert selections[0]["selected_models"] == "ECMWF_HRES,GFS_GLOBAL,ICON_SEAMLESS"
    assert selections[0]["selection_source"] == "development_2023_2025_only"
