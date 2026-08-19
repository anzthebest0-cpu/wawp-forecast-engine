"""Observe-only operational lead residual summaries.

This module builds a compact state artifact from live multi-init forecast pairs.
It does not apply corrections. The output is intended for dashboard provenance
and future promotion gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from time import perf_counter
from typing import Iterable

import numpy as np
import pandas as pd

from src.forecast_time_provenance import (
    HISTORICAL_RUN_LABEL,
    TIME_CONTRACT_VERSION,
    hard_valid_forecast_sql,
    valid_time_utc_sql,
)


log = logging.getLogger(__name__)


PARAMETERS = [
    "Temperature",
    "Dewpoint",
    "Pressure",
    "Rainfall",
    "Wind Speed",
    "Wind Dir.",
    "Wind Gust",
]

LEAD_BUCKETS = [
    ("L1_0_6h", 0.0, 6.0),
    ("L2_6_12h", 6.0, 12.0),
    ("L3_12_24h", 12.0, 24.0),
    ("L4_24_48h", 24.0, 48.0),
    ("L5_48plus", 48.0, float("inf")),
]

PARAMETER_COLUMNS = {
    "Temperature": ("temperature", "temperature"),
    "Dewpoint": ("dewpoint", "dewpoint"),
    "Pressure": ("pressure_msl", "pressure"),
    "Rainfall": ("rain", "rain_1h"),
    "Wind Speed": ("wind_speed", "wind_speed"),
    "Wind Gust": ("wind_gust", "wind_gust_max"),
    "Wind Dir.": ("wind_dir", "wind_dir"),
}

PAIR_COLUMN_ALIASES = {
    parameter: (
        f"forecast_{forecast_column}",
        f"obs_{observation_column}",
    )
    for parameter, (forecast_column, observation_column) in PARAMETER_COLUMNS.items()
}

PROMOTION_THRESHOLDS = {
    "Temperature": {"pairs": 100, "events": 0},
    "Dewpoint": {"pairs": 100, "events": 0},
    "Pressure": {"pairs": 100, "events": 0},
    "Wind Speed": {"pairs": 150, "events": 0},
    "Wind Dir.": {"pairs": 100, "events": 0},
    "Wind Gust": {"pairs": 100, "events": 20},
    "Rainfall": {"pairs": 300, "events": 20},
}


def lead_bucket(lead_hours: float | int | None) -> str | None:
    try:
        lead = float(lead_hours)
    except (TypeError, ValueError):
        return None
    if lead < 0:
        return None
    for name, lo, hi in LEAD_BUCKETS:
        if lo <= lead < hi:
            return name
    return "L5_48plus"


def circular_diff_deg(obs: pd.Series, forecast: pd.Series) -> pd.Series:
    return ((obs.astype(float) - forecast.astype(float) + 180.0) % 360.0) - 180.0


def circular_mean_deg(values: Iterable[float]) -> float:
    vals = np.array([float(v) for v in values if pd.notna(v)], dtype=float)
    if len(vals) == 0:
        return 0.0
    radians = np.deg2rad(vals)
    angle = np.rad2deg(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean()))
    return float(((angle + 180.0) % 360.0) - 180.0)


def _safe_round(value, digits: int = 4):
    try:
        if value is None or pd.isna(value) or np.isinf(value):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _split_fit_validation(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("Datetime")
    if len(df) >= 30:
        cut = max(1, int(len(df) * 0.7))
        return df.iloc[:cut], df.iloc[cut:]
    return df, df


def _binary_scores(obs_event: pd.Series, fc_event: pd.Series) -> dict:
    obs = obs_event.astype(bool)
    fc = fc_event.astype(bool)
    hits = int((obs & fc).sum())
    misses = int((obs & ~fc).sum())
    false_alarms = int((~obs & fc).sum())
    correct_negatives = int((~obs & ~fc).sum())
    total = hits + misses + false_alarms + correct_negatives
    pod = hits / (hits + misses) if (hits + misses) else 0.0
    far = false_alarms / (hits + false_alarms) if (hits + false_alarms) else 1.0
    csi = hits / (hits + misses + false_alarms) if (hits + misses + false_alarms) else 0.0
    expected = (
        ((hits + misses) * (hits + false_alarms))
        + ((correct_negatives + misses) * (correct_negatives + false_alarms))
    ) / total if total else 0.0
    denom = total - expected
    hss = (hits + correct_negatives - expected) / denom if denom > 0 else 0.0
    return {
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_negatives": correct_negatives,
        "POD": _safe_round(pod),
        "FAR": _safe_round(far),
        "CSI": _safe_round(csi),
        "HSS": _safe_round(hss),
    }


def _status(parameter: str, sample_count: int, event_count: int, skill_score: float | None) -> tuple[str, str]:
    thresholds = PROMOTION_THRESHOLDS.get(parameter, {"pairs": 100, "events": 0})
    min_pairs = int(thresholds["pairs"])
    min_events = int(thresholds.get("events") or 0)
    if sample_count < min_pairs:
        return "pending", f"needs {min_pairs} pairs"
    if min_events and event_count < min_events:
        return "pending", f"needs {min_events} observed events"
    if skill_score is None:
        return "pending", "validation unavailable"
    if skill_score > 0:
        return "ready_observe_only", "validation improves baseline; correction not enabled yet"
    return "disabled_observe_only", "validation does not improve baseline"


def summarize_parameter_pairs(df: pd.DataFrame, parameter: str) -> dict:
    if df.empty:
        return {
            "parameter": parameter,
            "rows": [],
            "summary": {
                "total_pairs": 0,
                "ready_rows": 0,
                "pending_rows": 0,
                "disabled_rows": 0,
                "best_skill_score": None,
                "status": "pending",
            },
        }

    work = df.copy()
    work["Datetime"] = pd.to_datetime(work["Datetime"], errors="coerce")
    work["Lead_Hour"] = pd.to_numeric(work["Lead_Hour"], errors="coerce")
    work["forecast"] = pd.to_numeric(work["forecast"], errors="coerce")
    work["obs"] = pd.to_numeric(work["obs"], errors="coerce")
    work = work.dropna(subset=["Datetime", "Model", "Lead_Hour", "forecast", "obs"])
    work["lead_bucket"] = work["Lead_Hour"].apply(lead_bucket)
    work = work.dropna(subset=["lead_bucket"])

    rows = []
    for (model, bucket), group in work.groupby(["Model", "lead_bucket"], sort=True):
        fit, validation = _split_fit_validation(group)
        sample_count = int(len(group))
        valid_count = int(len(validation))
        event_count = 0
        rain_scores = None

        if parameter == "Wind Dir.":
            fit_residual = circular_diff_deg(fit["obs"], fit["forecast"])
            correction = circular_mean_deg(fit_residual)
            before_err = circular_diff_deg(validation["obs"], validation["forecast"]).abs()
            after_fc = (validation["forecast"].astype(float) + correction) % 360.0
            after_err = circular_diff_deg(validation["obs"], after_fc).abs()
            mean_error = circular_mean_deg(circular_diff_deg(group["obs"], group["forecast"]))
            median_error = correction
        elif parameter == "Rainfall":
            fc_event = group["forecast"].astype(float) >= 1.0
            obs_event = group["obs"].astype(float) >= 0.1
            event_count = int(obs_event.sum())
            rain_scores = _binary_scores(obs_event, fc_event)
            before_err = (validation["forecast"].astype(float) - validation["obs"].astype(float)).abs()
            after_err = before_err
            mean_error = float((group["obs"].astype(float) - group["forecast"].astype(float)).mean())
            median_error = None
        else:
            residual = fit["obs"].astype(float) - fit["forecast"].astype(float)
            correction = float(residual.median()) if len(residual) else 0.0
            if parameter in {"Wind Speed", "Wind Gust"}:
                after_fc = (validation["forecast"].astype(float) + correction).clip(lower=0.0)
            else:
                after_fc = validation["forecast"].astype(float) + correction
            before_err = (validation["forecast"].astype(float) - validation["obs"].astype(float)).abs()
            after_err = (after_fc - validation["obs"].astype(float)).abs()
            mean_error = float((group["obs"].astype(float) - group["forecast"].astype(float)).mean())
            median_error = correction
            if parameter == "Wind Gust":
                event_count = int((group["obs"].astype(float) >= 15.0).sum())

        mae_before = float(before_err.mean()) if len(before_err) else None
        mae_after = float(after_err.mean()) if len(after_err) else None
        if mae_before and mae_before > 0 and mae_after is not None:
            skill_score = 1.0 - (mae_after / mae_before)
        else:
            skill_score = None
        if parameter == "Rainfall" and rain_scores is not None:
            skill_score = rain_scores.get("HSS")
        promotion_status, reason = _status(parameter, sample_count, event_count, skill_score)

        payload = {
            "model": str(model),
            "parameter": parameter,
            "lead_bucket": str(bucket),
            "sample_count": sample_count,
            "validation_count": valid_count,
            "event_count": event_count,
            "mean_error": _safe_round(mean_error),
            "median_error": _safe_round(median_error),
            "mae_before": _safe_round(mae_before),
            "mae_after_if_median_correction_used": _safe_round(mae_after),
            "skill_score": _safe_round(skill_score),
            "enabled": False,
            "promotion_status": promotion_status,
            "reason": reason,
        }
        if rain_scores is not None:
            payload["rainfall_occurrence"] = rain_scores
            payload["median_error"] = None
            payload["mae_after_if_median_correction_used"] = None
            payload["skill_score"] = _safe_round(skill_score)
            payload["reason"] = (
                "occurrence tracked observe-only; amount residual deferred"
                if promotion_status != "pending"
                else reason + "; amount residual deferred"
            )
        rows.append(payload)

    ready = sum(1 for row in rows if row["promotion_status"] == "ready_observe_only")
    pending = sum(1 for row in rows if row["promotion_status"] == "pending")
    disabled = sum(1 for row in rows if row["promotion_status"] == "disabled_observe_only")
    best_skill = max(
        (float(row["skill_score"]) for row in rows if row.get("skill_score") is not None),
        default=None,
    )
    return {
        "parameter": parameter,
        "rows": rows,
        "summary": {
            "total_pairs": int(len(work)),
            "ready_rows": ready,
            "pending_rows": pending,
            "disabled_rows": disabled,
            "best_skill_score": _safe_round(best_skill),
            "status": "ready_observe_only" if ready else ("disabled_observe_only" if disabled and not pending else "pending"),
        },
    }


def _operational_valid_time_sql(conn, alias: str = "f") -> tuple[str, bool]:
    """Use the indexed UTC column after migration, with legacy fallback support."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(openmeteo_forecasts)")}
    has_explicit_utc = "valid_time_utc" in columns
    if has_explicit_utc:
        # ensure_time_provenance_schema() backfills every operational row. Using
        # the column directly lets SQLite use idx_om_valid_utc; wrapping it in
        # COALESCE/CASE forced a full forecast-archive scan on every parameter.
        return f"{alias}.valid_time_utc", True
    return valid_time_utc_sql(alias, has_explicit_column=False), False


def _clean_cycle_ctes(valid_utc: str, placeholders: str) -> str:
    """Materialize cycle eligibility once instead of once per forecast row."""
    candidate_valid = hard_valid_forecast_sql("q")
    return f"""
        WITH candidate_cycles AS MATERIALIZED (
            SELECT DISTINCT f.location, f.model, f.run_init_utc, f.scraped_at
            FROM openmeteo_forecasts f
            WHERE {valid_utc} >= ? AND {valid_utc} <= ?
              AND f.run_init_utc <> '{HISTORICAL_RUN_LABEL}'
              AND f.lead_hours >= 0
              AND f.model IN ({placeholders})
        ),
        clean_cycles AS MATERIALIZED (
            SELECT c.location, c.model, c.run_init_utc, c.scraped_at
            FROM candidate_cycles c
            WHERE NOT EXISTS (
                SELECT 1
                FROM openmeteo_forecasts q
                WHERE q.location = c.location
                  AND q.model = c.model
                  AND q.run_init_utc = c.run_init_utc
                  AND q.scraped_at = c.scraped_at
                  AND NOT ({candidate_valid})
            )
        )
    """


def query_operational_pair_matrix(
    conn,
    start_date: str,
    end_date: str,
    models: list[str],
) -> pd.DataFrame:
    """Fetch all residual parameters with one UTC-aligned, cycle-clean join."""
    placeholders = ",".join("?" for _ in models)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(openmeteo_forecasts)")}
    valid_utc, _has_explicit_utc = _operational_valid_time_sql(conn, "f")
    time_basis = (
        "COALESCE(f.forecast_time_basis, 'legacy_derived')"
        if "forecast_time_basis" in columns
        else "'legacy_derived'"
    )
    hard_valid = hard_valid_forecast_sql("f")
    parameter_columns = ",\n            ".join(
        f"f.{forecast_column} AS {PAIR_COLUMN_ALIASES[parameter][0]}, "
        f"o.{observation_column} AS {PAIR_COLUMN_ALIASES[parameter][1]}"
        for parameter, (forecast_column, observation_column) in PARAMETER_COLUMNS.items()
    )
    query = _clean_cycle_ctes(valid_utc, placeholders) + f"""
        SELECT
            {valid_utc} AS Datetime,
            f.forecast_time AS Local_Datetime,
            f.model AS Model,
            f.run_init_utc AS Collection_Cycle_UTC,
            f.run_init_utc AS Run_Init_UTC,
            f.scraped_at AS Collected_At_UTC,
            {time_basis} AS Time_Basis,
            f.lead_hours AS Lead_Hour,
            {parameter_columns}
        FROM openmeteo_forecasts f
        INNER JOIN clean_cycles c
            ON c.location = f.location
            AND c.model = f.model
            AND c.run_init_utc = f.run_init_utc
            AND c.scraped_at = f.scraped_at
        INNER JOIN awos_observations o
            ON f.location = o.location
            AND {valid_utc} = o.obs_time
        WHERE {valid_utc} >= ? AND {valid_utc} <= ?
          AND f.run_init_utc <> 'historical_forecast_api'
          AND f.lead_hours >= 0
          AND f.model IN ({placeholders})
          AND {hard_valid}
        ORDER BY f.model, f.run_init_utc, {valid_utc}
    """
    params = (start_date, end_date, *models, start_date, end_date, *models)
    return pd.read_sql_query(query, conn, params=params)


def _query_operational_pair_count(
    conn,
    start_date: str,
    end_date: str,
    models: list[str],
) -> int:
    """Count aligned clean-cycle pairs without requiring parameter columns."""
    placeholders = ",".join("?" for _ in models)
    valid_utc, _has_explicit_utc = _operational_valid_time_sql(conn, "f")
    hard_valid = hard_valid_forecast_sql("f")
    query = _clean_cycle_ctes(valid_utc, placeholders) + f"""
        SELECT COUNT(*)
        FROM openmeteo_forecasts f
        INNER JOIN clean_cycles c
            ON c.location = f.location
            AND c.model = f.model
            AND c.run_init_utc = f.run_init_utc
            AND c.scraped_at = f.scraped_at
        INNER JOIN awos_observations o
            ON o.location = f.location
            AND o.obs_time = {valid_utc}
        WHERE {valid_utc} >= ? AND {valid_utc} <= ?
          AND f.run_init_utc <> '{HISTORICAL_RUN_LABEL}'
          AND f.lead_hours >= 0
          AND f.model IN ({placeholders})
          AND {hard_valid}
    """
    params = (start_date, end_date, *models, start_date, end_date, *models)
    return int(conn.execute(query, params).fetchone()[0] or 0)


def _parameter_pairs_from_matrix(matrix: pd.DataFrame, parameter: str) -> pd.DataFrame:
    forecast_column, observation_column = PAIR_COLUMN_ALIASES[parameter]
    base_columns = [
        "Datetime", "Local_Datetime", "Model", "Collection_Cycle_UTC",
        "Run_Init_UTC", "Collected_At_UTC", "Time_Basis", "Lead_Hour",
    ]
    if matrix.empty:
        return pd.DataFrame(columns=[*base_columns, "forecast", "obs"])
    pairs = matrix[[*base_columns, forecast_column, observation_column]].rename(
        columns={forecast_column: "forecast", observation_column: "obs"}
    )
    if parameter == "Wind Gust":
        pairs = pairs[pd.to_numeric(pairs["obs"], errors="coerce") > 0]
    return pairs.reset_index(drop=True)


def query_operational_pairs(conn, parameter: str, start_date: str, end_date: str, models: list[str]) -> pd.DataFrame:
    matrix = query_operational_pair_matrix(conn, start_date, end_date, models)
    return _parameter_pairs_from_matrix(matrix, parameter)


def operational_pairing_audit(
    conn,
    start_date: str,
    end_date: str,
    models: list[str],
    *,
    physically_aligned_pair_rows: int | None = None,
) -> dict:
    placeholders = ",".join("?" for _ in models)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(openmeteo_forecasts)")}
    has_explicit_utc = "valid_time_utc" in columns
    valid_utc, _ = _operational_valid_time_sql(conn, "f")
    hard_valid = hard_valid_forecast_sql("f")
    explicit_utc_count = (
        "SUM(CASE WHEN f.valid_time_utc IS NOT NULL THEN 1 ELSE 0 END)"
        if has_explicit_utc
        else "0"
    )
    params = (start_date, end_date, *models)
    scope = conn.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT f.model), COUNT(DISTINCT f.run_init_utc),
               MIN({valid_utc}), MAX({valid_utc}),
               {explicit_utc_count},
               SUM(CASE WHEN {hard_valid} THEN 0 ELSE 1 END)
        FROM openmeteo_forecasts f
        WHERE {valid_utc} >= ? AND {valid_utc} <= ?
          AND f.run_init_utc <> 'historical_forecast_api'
          AND f.lead_hours >= 0
          AND f.model IN ({placeholders})
    """, params).fetchone()
    if physically_aligned_pair_rows is None:
        corrected_pairs = _query_operational_pair_count(conn, start_date, end_date, models)
    else:
        corrected_pairs = int(physically_aligned_pair_rows)
    legacy_pairs = conn.execute(f"""
        SELECT COUNT(*)
        FROM openmeteo_forecasts f
        INNER JOIN awos_observations o
          ON o.location=f.location AND o.obs_time=f.forecast_time
        WHERE {valid_utc} >= ? AND {valid_utc} <= ?
          AND f.run_init_utc <> 'historical_forecast_api'
          AND f.lead_hours >= 0
          AND f.model IN ({placeholders})
    """, params).fetchone()[0]
    return {
        "pairing_contract_version": TIME_CONTRACT_VERSION,
        "forecast_time_compatibility_basis": "WITA for operational Forecast API rows",
        "verification_join": "valid_time_utc equals AWOS obs_time UTC",
        "run_provenance": "run_init_utc is a collection-cycle proxy, not a confirmed provider initialization",
        "scope_forecast_rows": int(scope[0] or 0),
        "scope_model_count": int(scope[1] or 0),
        "scope_collection_cycle_count": int(scope[2] or 0),
        "first_valid_time_utc": scope[3],
        "last_valid_time_utc": scope[4],
        "explicit_valid_time_utc_rows": int(scope[5] or 0),
        "hard_anomaly_rows_excluded": int(scope[6] or 0),
        "whole_cycle_quarantine": True,
        "physically_aligned_pair_rows": int(corrected_pairs or 0),
        "legacy_naive_text_pair_rows": int(legacy_pairs or 0),
        "promotion_eligible": False,
    }


def build_operational_residual_state(
    conn,
    start_date: str,
    end_date: str,
    models: list[str],
    *,
    verification_frozen: bool = False,
) -> dict:
    started = perf_counter()
    log.info("Building operational residual pair matrix...")
    matrix = query_operational_pair_matrix(conn, start_date, end_date, models)
    matrix_seconds = perf_counter() - started
    log.info(
        "Operational residual pair matrix ready: rows=%d elapsed=%.2fs",
        len(matrix),
        matrix_seconds,
    )
    audit_started = perf_counter()
    pairing_audit = operational_pairing_audit(
        conn,
        start_date,
        end_date,
        models,
        physically_aligned_pair_rows=len(matrix),
    )
    log.info("Operational residual pairing audit ready: elapsed=%.2fs", perf_counter() - audit_started)
    parameters = {}
    detail_rows = []
    for parameter in PARAMETERS:
        pairs = _parameter_pairs_from_matrix(matrix, parameter)
        result = summarize_parameter_pairs(pairs, parameter)
        parameters[parameter] = result["summary"]
        detail_rows.extend(result["rows"])
        log.info(
            "Operational residual summary %s: pairs=%d status=%s",
            parameter,
            result["summary"]["total_pairs"],
            result["summary"]["status"],
        )

    ready_rows = sum(1 for row in detail_rows if row["promotion_status"] == "ready_observe_only")
    pending_rows = sum(1 for row in detail_rows if row["promotion_status"] == "pending")
    disabled_rows = sum(1 for row in detail_rows if row["promotion_status"] == "disabled_observe_only")
    total_pairs = sum(int(summary.get("total_pairs") or 0) for summary in parameters.values())

    state = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "start_date": start_date,
            "end_date": end_date,
            "mode": "observe_only",
            "pairing_contract_version": TIME_CONTRACT_VERSION,
            "verification_frozen": bool(verification_frozen),
            "promotion_eligible": False,
            "enabled_rows": 0,
            "total_pairs": total_pairs,
            "ready_rows": ready_rows,
            "pending_rows": pending_rows,
            "disabled_rows": disabled_rows,
            "lead_buckets": [name for name, _, _ in LEAD_BUCKETS],
            "note": (
                "Operational residuals are measured with UTC-normalized valid times only; "
                "no live forecast correction is applied in this phase."
            ),
        },
        "pairing_audit": pairing_audit,
        "parameters": parameters,
        "rows": detail_rows,
    }
    log.info("Operational residual state complete: elapsed=%.2fs", perf_counter() - started)
    return state
