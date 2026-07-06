"""Observe-only operational lead residual summaries.

This module builds a compact state artifact from live multi-init forecast pairs.
It does not apply corrections. The output is intended for dashboard provenance
and future promotion gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import numpy as np
import pandas as pd


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


def query_operational_pairs(conn, parameter: str, start_date: str, end_date: str, models: list[str]) -> pd.DataFrame:
    f_col, o_col = PARAMETER_COLUMNS[parameter]
    placeholders = ",".join("?" for _ in models)
    query = f"""
        SELECT
            f.forecast_time AS Datetime,
            f.model AS Model,
            f.run_init_utc AS Run_Init_UTC,
            f.lead_hours AS Lead_Hour,
            f.{f_col} AS forecast,
            o.{o_col} AS obs
        FROM openmeteo_forecasts f
        INNER JOIN awos_observations o
            ON f.location = o.location
            AND f.forecast_time = o.obs_time
        WHERE f.forecast_time >= ? AND f.forecast_time <= ?
          AND f.run_init_utc <> 'historical_forecast_api'
          AND f.lead_hours >= 0
          AND f.model IN ({placeholders})
    """
    return pd.read_sql_query(query, conn, params=(start_date, end_date, *models))


def build_operational_residual_state(conn, start_date: str, end_date: str, models: list[str]) -> dict:
    parameters = {}
    detail_rows = []
    for parameter in PARAMETERS:
        pairs = query_operational_pairs(conn, parameter, start_date, end_date, models)
        result = summarize_parameter_pairs(pairs, parameter)
        parameters[parameter] = result["summary"]
        detail_rows.extend(result["rows"])

    ready_rows = sum(1 for row in detail_rows if row["promotion_status"] == "ready_observe_only")
    pending_rows = sum(1 for row in detail_rows if row["promotion_status"] == "pending")
    disabled_rows = sum(1 for row in detail_rows if row["promotion_status"] == "disabled_observe_only")
    total_pairs = sum(int(summary.get("total_pairs") or 0) for summary in parameters.values())

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "start_date": start_date,
            "end_date": end_date,
            "mode": "observe_only",
            "enabled_rows": 0,
            "total_pairs": total_pairs,
            "ready_rows": ready_rows,
            "pending_rows": pending_rows,
            "disabled_rows": disabled_rows,
            "lead_buckets": [name for name, _, _ in LEAD_BUCKETS],
            "note": "Operational residuals are measured only; no live forecast correction is applied in this phase.",
        },
        "parameters": parameters,
        "rows": detail_rows,
    }
