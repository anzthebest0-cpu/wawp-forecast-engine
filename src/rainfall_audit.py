"""Reproducible rainfall data-quality and metric-semantics audit for WAWP."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.meteorological_contract import (
    AWOS_HOURLY_RAIN_TIMESTAMP_LABEL,
    AWOS_MINUTE_RAIN_SEMANTICS,
    RAIN_EVENT_SHADOW_MM_H,
    RAIN_REPLAY_THRESHOLDS_MM_H,
    SEMANTICS_VERSION,
    interval_end_block_labels,
)
from src.forecast_time_provenance import TIME_CONTRACT_VERSION, valid_time_utc_sql


DEFAULT_LOCATION = "Bandara_Sangia_Ni_Bandera"
DEFAULT_THRESHOLD_MM_H = RAIN_EVENT_SHADOW_MM_H
WET_THRESHOLDS_MM_H = RAIN_REPLAY_THRESHOLDS_MM_H
VALID_WMO_WEATHER_CODES = (0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
                           71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99)


def _safe_round(value: Any, digits: int = 4):
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def build_observation_ledger(
    conn: sqlite3.Connection,
    location: str = DEFAULT_LOCATION,
    threshold: float = DEFAULT_THRESHOLD_MM_H,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_sql_query(
        """
        SELECT obs_time, rain_1h
        FROM awos_observations
        WHERE location = ?
        ORDER BY obs_time
        """,
        conn,
        params=(location,),
    )
    frame["obs_time_utc"] = pd.to_datetime(frame["obs_time"], errors="coerce")
    frame["rain_1h_mm"] = pd.to_numeric(frame["rain_1h"], errors="coerce")
    frame = frame.dropna(subset=["obs_time_utc"]).sort_values("obs_time_utc").reset_index(drop=True)
    frame["obs_time_wita"] = frame["obs_time_utc"] + pd.Timedelta(hours=8)
    frame["hour_gap"] = frame["obs_time_utc"].diff().dt.total_seconds().div(3600.0)
    frame["is_complete_next_hour"] = frame["hour_gap"].fillna(1.0).eq(1.0)
    frame["strict_wet"] = frame["rain_1h_mm"].ge(threshold)

    # This reproduces the current production transformation exactly. It is a
    # centered rolling maximum over available samples, not a 3-hour amount.
    frame["legacy_3h_expanded_wet"] = (
        frame["rain_1h_mm"].rolling(3, min_periods=1, center=True).max().ge(threshold)
    )

    episode_start = frame["strict_wet"] & (
        ~frame["strict_wet"].shift(fill_value=False) | ~frame["is_complete_next_hour"]
    )
    frame["episode_id"] = episode_start.cumsum().where(frame["strict_wet"])

    frame["block_3h_end_utc"] = interval_end_block_labels(
        pd.DatetimeIndex(frame["obs_time_utc"]), 3
    )
    grouped = frame.groupby("block_3h_end_utc", dropna=False)["rain_1h_mm"]
    block_count = grouped.count()
    block_sum = grouped.sum(min_count=1)
    block_complete = block_count.eq(3)
    block_lookup = pd.DataFrame({
        "block_3h_sample_count": block_count,
        "block_3h_sum_mm": block_sum,
        "block_3h_complete": block_complete,
    })
    frame = frame.join(block_lookup, on="block_3h_end_utc")

    valid_times = frame["obs_time_utc"].drop_duplicates()
    expected_rows = 0
    if not valid_times.empty:
        expected_rows = len(pd.date_range(valid_times.min(), valid_times.max(), freq="h"))

    complete_blocks = block_lookup[block_lookup["block_3h_complete"]]
    summary = {
        "grain": "one row per stored hourly AWOS timestamp",
        "semantics_version": SEMANTICS_VERSION,
        "rain_timestamp_label": AWOS_HOURLY_RAIN_TIMESTAMP_LABEL,
        "location": location,
        "first_obs_utc": valid_times.min().isoformat() if not valid_times.empty else None,
        "last_obs_utc": valid_times.max().isoformat() if not valid_times.empty else None,
        "row_count": int(len(frame)),
        "distinct_timestamp_count": int(frame["obs_time_utc"].nunique()),
        "duplicate_timestamp_count": int(frame["obs_time_utc"].duplicated().sum()),
        "expected_hour_count": int(expected_rows),
        "missing_hour_count": int(max(0, expected_rows - frame["obs_time_utc"].nunique())),
        "null_rain_count": int(frame["rain_1h_mm"].isna().sum()),
        "negative_rain_count": int(frame["rain_1h_mm"].lt(0).sum()),
        "rain_total_mm_if_hourly_increment": _safe_round(frame["rain_1h_mm"].sum(min_count=1), 2),
        "maximum_hourly_value_mm": _safe_round(frame["rain_1h_mm"].max(), 2),
        "wet_sample_counts": {
            str(value): int(frame["rain_1h_mm"].ge(value).sum()) for value in WET_THRESHOLDS_MM_H
        },
        "metric_comparison_at_threshold": {
            "threshold_mm_per_hour": threshold,
            "strict_hourly_wet_samples": int(frame["strict_wet"].sum()),
            "unique_contiguous_episodes": int(frame["episode_id"].nunique()),
            "legacy_centered_rolling_peak_samples": int(frame["legacy_3h_expanded_wet"].sum()),
            "legacy_inflation_factor_vs_hourly": _safe_round(
                frame["legacy_3h_expanded_wet"].sum() / max(1, frame["strict_wet"].sum())
            ),
            "complete_utc_aligned_3h_end_labeled_bins": int(len(complete_blocks)),
            "wet_complete_3h_bins_at_same_numeric_threshold": int(
                complete_blocks["block_3h_sum_mm"].ge(threshold).sum()
            ),
            "three_hour_threshold_status": "diagnostic only; mm/3h threshold requires meteorological approval",
        },
        "monthly_metric_comparison": [],
    }
    for month, group in frame.groupby(frame["obs_time_utc"].dt.to_period("M")):
        summary["monthly_metric_comparison"].append({
            "month": str(month),
            "stored_hours": int(len(group)),
            "nonnull_rain_hours": int(group["rain_1h_mm"].notna().sum()),
            "strict_hourly_wet_samples": int(group["strict_wet"].sum()),
            "unique_contiguous_episodes": int(group["episode_id"].nunique()),
            "legacy_centered_rolling_peak_samples": int(group["legacy_3h_expanded_wet"].sum()),
        })
    ledger_columns = [
        "obs_time_utc", "obs_time_wita", "rain_1h_mm", "hour_gap", "strict_wet",
        "episode_id", "legacy_3h_expanded_wet", "block_3h_end_utc",
        "block_3h_sample_count", "block_3h_sum_mm", "block_3h_complete",
    ]
    return frame[ledger_columns], summary


def profile_minute_rain(conn: sqlite3.Connection, location: str = DEFAULT_LOCATION) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*), COUNT(rain_1min),
               SUM(CASE WHEN rain_1min > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN rain_1min < 0 THEN 1 ELSE 0 END),
               MIN(obs_time), MAX(obs_time), MIN(rain_1min), MAX(rain_1min), SUM(rain_1min)
        FROM awos_observations_1min
        WHERE location = ?
        """,
        (location,),
    ).fetchone()
    return {
        "row_count": int(row[0] or 0),
        "nonnull_rain_count": int(row[1] or 0),
        "positive_rain_sample_count": int(row[2] or 0),
        "negative_rain_sample_count": int(row[3] or 0),
        "first_obs": row[4],
        "last_obs": row[5],
        "minimum_raw_value": _safe_round(row[6]),
        "maximum_raw_value": _safe_round(row[7]),
        "raw_sum": _safe_round(row[8], 2),
        "interpretation_status": AWOS_MINUTE_RAIN_SEMANTICS,
        "safe_aggregation": "do not sum minute values; use hourly RA36 for rainfall accumulation",
    }


def profile_minute_hourly_reconciliation(
    conn: sqlite3.Connection,
    location: str = DEFAULT_LOCATION,
) -> dict[str, Any]:
    row = conn.execute(
        """
        WITH paired AS (
            SELECT h.rain_1h,
                   (SELECT m.rain_1min
                    FROM awos_observations_1min m
                    WHERE m.location=h.location AND m.obs_time=h.obs_time
                    LIMIT 1) AS boundary_value,
                   (SELECT COUNT(m.rain_1min)
                    FROM awos_observations_1min m
                    WHERE m.location=h.location
                      AND m.obs_time>datetime(h.obs_time, '-1 hour')
                      AND m.obs_time<=h.obs_time) AS minute_values,
                   (SELECT SUM(m.rain_1min)
                    FROM awos_observations_1min m
                    WHERE m.location=h.location
                      AND m.obs_time>datetime(h.obs_time, '-1 hour')
                      AND m.obs_time<=h.obs_time) AS preceding_hour_sum,
                   (SELECT MAX(m.rain_1min)
                    FROM awos_observations_1min m
                    WHERE m.location=h.location
                      AND m.obs_time>datetime(h.obs_time, '-1 hour')
                      AND m.obs_time<=h.obs_time) AS preceding_hour_max
            FROM awos_observations h
            WHERE h.location=? AND h.rain_1h IS NOT NULL
        )
        SELECT COUNT(*),
               SUM(CASE WHEN ABS(rain_1h-boundary_value) <= 0.001 THEN 1 ELSE 0 END),
               SUM(CASE WHEN ABS(rain_1h-preceding_hour_sum) <= 0.001 THEN 1 ELSE 0 END),
               SUM(CASE WHEN ABS(rain_1h-preceding_hour_max) <= 0.001 THEN 1 ELSE 0 END),
               SUM(CASE WHEN rain_1h > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN rain_1h > 0 AND ABS(rain_1h-boundary_value) <= 0.001 THEN 1 ELSE 0 END),
               SUM(CASE WHEN rain_1h > 0 AND ABS(rain_1h-preceding_hour_sum) <= 0.001 THEN 1 ELSE 0 END),
               SUM(CASE WHEN rain_1h > 0 AND ABS(rain_1h-preceding_hour_max) <= 0.001 THEN 1 ELSE 0 END),
               SUM(CASE WHEN rain_1h > 0 THEN preceding_hour_sum ELSE 0 END),
               SUM(CASE WHEN rain_1h > 0 THEN rain_1h ELSE 0 END)
        FROM paired WHERE minute_values > 0
        """,
        (location,),
    ).fetchone()
    paired = int(row[0] or 0)
    wet = int(row[4] or 0)
    return {
        "paired_hour_count": paired,
        "all_hour_boundary_value_matches_hourly_count": int(row[1] or 0),
        "all_hour_preceding_minute_sum_matches_hourly_count": int(row[2] or 0),
        "all_hour_preceding_minute_max_matches_hourly_count": int(row[3] or 0),
        "wet_hour_count": wet,
        "wet_hour_boundary_value_matches_hourly_count": int(row[5] or 0),
        "wet_hour_preceding_minute_sum_matches_hourly_count": int(row[6] or 0),
        "wet_hour_preceding_minute_max_matches_hourly_count": int(row[7] or 0),
        "wet_hour_preceding_minute_sum_raw_total": _safe_round(row[8], 2),
        "wet_hour_hourly_total": _safe_round(row[9], 2),
        "boundary_match_rate": _safe_round((row[1] or 0) / max(1, paired)),
        "wet_hour_boundary_match_rate": _safe_round((row[5] or 0) / max(1, wet)),
        "finding": (
            "minute RA is a rolling one-hour accumulation snapshot: the value at the hour boundary "
            "matches hourly RA36; summing minute values is invalid"
        ),
    }


def profile_forecast_rain(conn: sqlite3.Connection, location: str = DEFAULT_LOCATION) -> dict[str, Any]:
    scope = conn.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT model), COUNT(DISTINCT run_init_utc),
               MIN(forecast_time), MAX(forecast_time),
               SUM(CASE WHEN rain < 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN precipitation IS NOT NULL AND ABS(rain - precipitation) > 0.001 THEN 1 ELSE 0 END),
               SUM(CASE WHEN precipitation IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN lifted_index IS NOT NULL AND ABS(lifted_index) > 30 THEN 1 ELSE 0 END),
               SUM(CASE WHEN cloud_cover < 0 OR cloud_cover > 100
                              OR cloud_cover_low < 0 OR cloud_cover_low > 100
                              OR cloud_cover_mid < 0 OR cloud_cover_mid > 100
                              OR cloud_cover_high < 0 OR cloud_cover_high > 100 THEN 1 ELSE 0 END)
        FROM openmeteo_forecasts WHERE location = ?
        """,
        (location,),
    ).fetchone()
    model_rows = conn.execute(
        """
        SELECT model, COUNT(*), COUNT(DISTINCT run_init_utc),
               SUM(CASE WHEN rain >= 1.5 THEN 1 ELSE 0 END),
               SUM(CASE WHEN precipitation IS NULL THEN 1 ELSE 0 END)
        FROM openmeteo_forecasts
        WHERE location = ?
        GROUP BY model ORDER BY model
        """,
        (location,),
    ).fetchall()
    return {
        "grain": "model run plus valid time",
        "row_count": int(scope[0] or 0),
        "model_count": int(scope[1] or 0),
        "run_label_count": int(scope[2] or 0),
        "first_forecast_time": scope[3],
        "last_forecast_time": scope[4],
        "negative_rain_count": int(scope[5] or 0),
        "rain_vs_precipitation_mismatch_count_when_precipitation_present": int(scope[6] or 0),
        "precipitation_null_count": int(scope[7] or 0),
        "impossible_lifted_index_count": int(scope[8] or 0),
        "cloud_cover_out_of_range_count": int(scope[9] or 0),
        "rain_column_semantics": (
            "current live scraper stores total precipitation in rain; historical loaders must be checked separately"
        ),
        "models": [
            {
                "model": row[0],
                "rows": int(row[1]),
                "run_labels": int(row[2]),
                "rain_ge_1_5_rows": int(row[3] or 0),
                "precipitation_null_rows": int(row[4] or 0),
            }
            for row in model_rows
        ],
    }


def build_forecast_anomaly_ledger(
    conn: sqlite3.Connection,
    location: str = DEFAULT_LOCATION,
) -> pd.DataFrame:
    code_placeholders = ",".join("?" for _ in VALID_WMO_WEATHER_CODES)
    query = f"""
        SELECT id, model, run_init_utc, scraped_at, forecast_time,
               rain, precipitation, showers, snowfall,
               cloud_cover, cloud_cover_low, cloud_cover_mid, cloud_cover_high,
               weather_code, visibility, cape, lifted_index, convective_inhib,
               boundary_layer_h
        FROM openmeteo_forecasts
        WHERE location = ? AND (
            rain < 0 OR precipitation < 0 OR showers < 0 OR snowfall < 0
            OR cloud_cover < 0 OR cloud_cover > 100
            OR cloud_cover_low < 0 OR cloud_cover_low > 100
            OR cloud_cover_mid < 0 OR cloud_cover_mid > 100
            OR cloud_cover_high < 0 OR cloud_cover_high > 100
            OR (weather_code IS NOT NULL AND weather_code NOT IN ({code_placeholders}))
            OR (lifted_index IS NOT NULL AND ABS(lifted_index) > 30)
        )
        ORDER BY scraped_at, model, forecast_time
    """
    return pd.read_sql_query(query, conn, params=(location, *VALID_WMO_WEATHER_CODES))


def profile_time_alignment(conn: sqlite3.Connection, location: str = DEFAULT_LOCATION) -> dict[str, Any]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(openmeteo_forecasts)")}
    valid_utc = valid_time_utc_sql("f", has_explicit_column="valid_time_utc" in columns)
    max_obs = conn.execute(
        "SELECT MAX(obs_time) FROM awos_observations WHERE location = ?",
        (location,),
    ).fetchone()[0]
    historical_exact = conn.execute(
        """
        SELECT COUNT(*) FROM openmeteo_forecasts f
        JOIN awos_observations o ON o.location=f.location AND o.obs_time=f.forecast_time
        WHERE f.location=? AND f.run_init_utc='historical_forecast_api'
        """,
        (location,),
    ).fetchone()[0]
    operational_exact = conn.execute(
        """
        SELECT COUNT(*) FROM openmeteo_forecasts f
        JOIN awos_observations o ON o.location=f.location AND o.obs_time=f.forecast_time
        WHERE f.location=? AND f.run_init_utc<>'historical_forecast_api'
        """,
        (location,),
    ).fetchone()[0]
    operational_shifted = conn.execute(
        f"""
        SELECT COUNT(*) FROM openmeteo_forecasts f
        JOIN awos_observations o
          ON o.location=f.location AND o.obs_time={valid_utc}
        WHERE f.location=? AND f.run_init_utc<>'historical_forecast_api'
        """,
        (location,),
    ).fetchone()[0]
    return {
        "pairing_contract_version": TIME_CONTRACT_VERSION,
        "latest_observation_utc": max_obs,
        "historical_exact_text_join_rows": int(historical_exact or 0),
        "operational_exact_text_join_rows": int(operational_exact or 0),
        "operational_utc_normalized_join_rows": int(operational_shifted or 0),
        "risk": (
            "forecast_time remains WITA for operational display compatibility; verification uses "
            "the explicit or derived valid_time_utc field against AWOS UTC"
        ),
        "status": "UTC pairing implemented; residuals remain observe-only pending live evidence",
    }


def run_audit(db_path: Path, output_dir: Path, location: str = DEFAULT_LOCATION) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with _read_only_connection(db_path) as conn:
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        ledger, observations = build_observation_ledger(conn, location=location)
        forecast_anomalies = build_forecast_anomaly_ledger(conn, location=location)
        payload = {
            "metadata": {
                "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "database": str(db_path.resolve()),
                "database_size_bytes": db_path.stat().st_size,
                "database_sha256": _sha256(db_path),
                "sqlite_quick_check": integrity,
                "audit_mode": "read_only",
            },
            "observations_hourly": observations,
            "observations_minute": profile_minute_rain(conn, location=location),
            "minute_hourly_reconciliation": profile_minute_hourly_reconciliation(conn, location=location),
            "forecasts": profile_forecast_rain(conn, location=location),
            "forecast_hard_anomalies": {
                "row_count": int(len(forecast_anomalies)),
                "affected_models": sorted(forecast_anomalies["model"].dropna().unique().tolist()),
                "affected_run_count": int(
                    forecast_anomalies[["model", "run_init_utc"]].drop_duplicates().shape[0]
                ),
                "first_scrape": forecast_anomalies["scraped_at"].min() if not forecast_anomalies.empty else None,
                "last_scrape": forecast_anomalies["scraped_at"].max() if not forecast_anomalies.empty else None,
                "status": "quarantine from training and replay until source mapping is reconstructed",
            },
            "time_alignment": profile_time_alignment(conn, location=location),
        }

    ledger.to_csv(output_dir / "rainfall_observation_ledger.csv", index=False)
    forecast_anomalies.to_csv(output_dir / "forecast_hard_anomalies.csv", index=False)
    (output_dir / "rainfall_audit_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("wawp_forecasts.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("audit/rainfall/evidence"))
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    args = parser.parse_args()
    payload = run_audit(args.database, args.output_dir, location=args.location)
    comparison = payload["observations_hourly"]["metric_comparison_at_threshold"]
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
