"""Leakage-safe rainfall replay for the continuous historical forecast stream.

This module is audit-only. It never updates live weights, QM state, consensus,
or TAF guidance. The Open-Meteo Historical Forecast API rows are treated as a
continuous, non-lead-aware stream and are evaluated against UTC hourly AWOS
rainfall using the versioned meteorological contract.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.event_window_verification import event_window_metrics_for_model
from src.meteorological_contract import (
    RAIN_REPLAY_THRESHOLDS_MM_H,
    SEMANTICS_VERSION,
)
from src.rainfall_audit import DEFAULT_LOCATION, VALID_WMO_WEATHER_CODES


ACTIVE_MODELS = (
    "ECMWF_HRES",
    "GFS_GLOBAL",
    "ICON_SEAMLESS",
    "GEM_GLOBAL",
    "CMA_GRAPES_GLOBAL",
    "JMA_GSM",
    "METEOFRANCE_ARPEGE_WORLD",
    "UKMO_GLOBAL_10KM",
)

# The Historical Forecast API is a stitched stream. These values describe the
# provider guidance behind its hourly output grid, not a recoverable run lead.
NATIVE_CADENCE_HOURS = {
    "ECMWF_HRES": 1,
    "GFS_GLOBAL": 1,
    "ICON_SEAMLESS": 1,
    "GEM_GLOBAL": 3,
    "CMA_GRAPES_GLOBAL": 3,
    "JMA_GSM": 6,
    "METEOFRANCE_ARPEGE_WORLD": 1,
    "UKMO_GLOBAL_10KM": 1,
}

SOURCE_TYPE = "continuous_historical"
LEAD_AWARE = False
DEVELOPMENT_START = pd.Timestamp("2023-01-01 00:00:00")
DEVELOPMENT_END = pd.Timestamp("2026-01-01 00:00:00")
HOLDOUT_START = DEVELOPMENT_END
HOLDOUT_END = pd.Timestamp("2026-07-01 00:00:00")
PRIMARY_THRESHOLD_MM_H = 1.5
BOOTSTRAP_REPLICATES = 500
BOOTSTRAP_SEED = 20260802


@dataclass(frozen=True)
class ReplaySplit:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp


SPLITS = (
    ReplaySplit("development_2023_2025", DEVELOPMENT_START, DEVELOPMENT_END),
    ReplaySplit("holdout_2026_h1", HOLDOUT_START, HOLDOUT_END),
)


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_ratio(numerator: int | float, denominator: int | float) -> float | None:
    return float(numerator / denominator) if denominator else None


def _round(value: Any, digits: int = 5) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_historical_rain_pairs(
    conn: sqlite3.Connection,
    location: str = DEFAULT_LOCATION,
    models: Iterable[str] = ACTIVE_MODELS,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load exact UTC historical pairs and quarantine unsafe forecast rows."""
    selected_models = tuple(models)
    model_marks = ",".join("?" for _ in selected_models)
    code_marks = ",".join("?" for _ in VALID_WMO_WEATHER_CODES)
    query = f"""
        SELECT f.id, f.model, f.forecast_time, f.rain AS forecast_rain,
               o.rain_1h AS observed_rain,
               f.cloud_cover, f.cloud_cover_low, f.cloud_cover_mid,
               f.cloud_cover_high, f.weather_code, f.lifted_index
        FROM openmeteo_forecasts f
        INNER JOIN awos_observations o
          ON o.location = f.location AND o.obs_time = f.forecast_time
        WHERE f.location = ?
          AND f.run_init_utc = 'historical_forecast_api'
          AND f.model IN ({model_marks})
        ORDER BY f.model, f.forecast_time, f.id
    """
    raw = pd.read_sql_query(
        query,
        conn,
        params=(location, *selected_models),
    )
    raw["valid_time_utc"] = pd.to_datetime(raw["forecast_time"], errors="coerce")
    raw["forecast_rain"] = pd.to_numeric(raw["forecast_rain"], errors="coerce")
    raw["observed_rain"] = pd.to_numeric(raw["observed_rain"], errors="coerce")

    duplicate_key = raw.duplicated(["model", "forecast_time"], keep=False)
    invalid_time = raw["valid_time_utc"].isna()
    invalid_amount = (
        raw["forecast_rain"].isna()
        | raw["observed_rain"].isna()
        | raw["forecast_rain"].lt(0)
        | raw["observed_rain"].lt(0)
    )
    invalid_cloud = pd.Series(False, index=raw.index)
    for column in ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high"):
        values = pd.to_numeric(raw[column], errors="coerce")
        invalid_cloud |= values.notna() & ~values.between(0, 100)
    weather = pd.to_numeric(raw["weather_code"], errors="coerce")
    invalid_weather = weather.notna() & ~weather.isin(VALID_WMO_WEATHER_CODES)
    lifted_index = pd.to_numeric(raw["lifted_index"], errors="coerce")
    invalid_lifted_index = lifted_index.notna() & lifted_index.abs().gt(30)
    hard_anomaly = invalid_cloud | invalid_weather | invalid_lifted_index

    quarantine = duplicate_key | invalid_time | invalid_amount | hard_anomaly
    pairs = raw.loc[
        ~quarantine,
        ["id", "model", "valid_time_utc", "forecast_rain", "observed_rain"],
    ].copy()
    pairs = pairs.sort_values(["model", "valid_time_utc"]).reset_index(drop=True)
    pairs["native_cadence_hours"] = pairs["model"].map(NATIVE_CADENCE_HOURS)
    pairs["native_cadence_class"] = pairs["native_cadence_hours"].map(
        lambda value: f"native_{int(value)}h" if pd.notna(value) else "unknown"
    )

    audit = {
        "raw_exact_join_rows": int(len(raw)),
        "usable_pair_rows": int(len(pairs)),
        "quarantined_rows": int(quarantine.sum()),
        "duplicate_model_valid_time_rows": int(duplicate_key.sum()),
        "invalid_or_missing_time_rows": int(invalid_time.sum()),
        "invalid_or_missing_amount_rows": int(invalid_amount.sum()),
        "hard_physical_anomaly_rows": int(hard_anomaly.sum()),
        "negative_forecast_rain_rows": int(raw["forecast_rain"].lt(0).sum()),
        "negative_observed_rain_rows": int(raw["observed_rain"].lt(0).sum()),
        "quarantine_policy": "exclude every ambiguous duplicate and every row with missing, negative, or hard-invalid forecast fields",
    }
    return pairs, audit


def _metric_row(
    split: str,
    model: str,
    threshold: float,
    product: str,
    metric: dict[str, Any],
    native_cadence_hours: int | None,
) -> dict[str, Any]:
    observed_events = int(metric.get("observed_events") or 0)
    forecast_events = int(metric.get("forecast_events") or 0)
    return {
        "split": split,
        "model": model,
        "native_cadence_hours": native_cadence_hours,
        "product": product,
        "hourly_threshold_mm": float(threshold),
        "metric_threshold": metric.get("threshold"),
        "threshold_unit": metric.get("threshold_unit"),
        "verification_unit": metric.get("verification_unit"),
        "sample_size": int(metric.get("sample_size") or 0),
        "observed_events": observed_events,
        "forecast_events": forecast_events,
        "hits": int(metric.get("hits") or 0),
        "misses": int(metric.get("misses") or 0),
        "false_alarms": int(metric.get("false_alarms") or 0),
        "correct_negatives": metric.get("correct_negatives"),
        "POD": metric.get("POD"),
        "FAR": metric.get("FAR"),
        "CSI": metric.get("CSI"),
        "HSS": metric.get("HSS"),
        "frequency_bias": _round(_safe_ratio(forecast_events, observed_events)),
        "mean_abs_timing_error_h": metric.get("mean_abs_timing_error_h"),
        "mean_signed_timing_error_h": metric.get("mean_signed_timing_error_h"),
        "amount_MAE": metric.get("amount_MAE", metric.get("peak_MAE")),
        "amount_Bias": metric.get("amount_Bias", metric.get("peak_Bias")),
        "source_type": SOURCE_TYPE,
        "lead_aware": LEAD_AWARE,
        "status": "shadow_only",
    }


def score_frame(
    frame: pd.DataFrame,
    split: str,
    model: str,
    thresholds: Iterable[float] = RAIN_REPLAY_THRESHOLDS_MM_H,
    native_cadence_hours: int | None = None,
) -> list[dict[str, Any]]:
    """Score all approved shadow products for one paired rainfall series."""
    if frame.empty:
        return []
    source = pd.DataFrame({
        "Datetime": pd.to_datetime(frame["valid_time_utc"], errors="coerce"),
        "forecast": pd.to_numeric(frame["forecast_rain"], errors="coerce"),
        "obs": pd.to_numeric(frame["observed_rain"], errors="coerce"),
    }).dropna(subset=["Datetime", "forecast", "obs"])
    rows: list[dict[str, Any]] = []
    cadence_window = min(2, max(1, int(np.ceil((native_cadence_hours or 1) / 2))))
    for threshold in thresholds:
        same_numeric = event_window_metrics_for_model(
            source,
            threshold=float(threshold),
            windows=(0, 1, 2),
            block_hours=3,
            block_mode="sum",
            block_threshold=float(threshold),
            timestamp_label="interval_end",
        )
        rate_equivalent = event_window_metrics_for_model(
            source,
            threshold=float(threshold),
            windows=(0,),
            block_hours=3,
            block_mode="sum",
            block_threshold=float(threshold) * 3.0,
            timestamp_label="interval_end",
        )
        products = {
            "strict_hourly": same_numeric["pm0h"],
            "episode_pm1h": same_numeric["pm1h"],
            "episode_pm2h": same_numeric["pm2h"],
            "cadence_aware_episode": same_numeric[f"pm{cadence_window}h"],
            "complete_3h_same_numeric": same_numeric["3h_block"],
            "complete_3h_rate_equivalent": rate_equivalent["3h_block"],
        }
        for product, metric in products.items():
            row = _metric_row(split, model, float(threshold), product, metric, native_cadence_hours)
            if product == "cadence_aware_episode":
                row["cadence_tolerance_hours"] = cadence_window
            else:
                row["cadence_tolerance_hours"] = None
            rows.append(row)
    return rows


def _amount_metrics(frame: pd.DataFrame, split: str, model: str) -> dict[str, Any]:
    forecast = pd.to_numeric(frame["forecast_rain"], errors="coerce")
    observed = pd.to_numeric(frame["observed_rain"], errors="coerce")
    error = forecast - observed
    wet_wet = forecast.ge(0.1) & observed.ge(0.1)
    observed_wet = observed.ge(0.1)
    forecast_dry_obs_wet = forecast.lt(0.1) & observed_wet
    return {
        "split": split,
        "model": model,
        "paired_hours": int(error.notna().sum()),
        "all_hour_MAE_mm": _round(error.abs().mean()),
        "all_hour_bias_mm": _round(error.mean()),
        "observed_wet_hours_ge_0_1": int(observed_wet.sum()),
        "wet_wet_hours_ge_0_1": int(wet_wet.sum()),
        "missed_wet_hours_ge_0_1": int(forecast_dry_obs_wet.sum()),
        "wet_wet_MAE_mm": _round(error[wet_wet].abs().mean()),
        "wet_wet_bias_mm": _round(error[wet_wet].mean()),
        "source_type": SOURCE_TYPE,
        "lead_aware": LEAD_AWARE,
    }


def _split_frame(pairs: pd.DataFrame, split: ReplaySplit) -> pd.DataFrame:
    return pairs[
        pairs["valid_time_utc"].ge(split.start)
        & pairs["valid_time_utc"].lt(split.end)
    ].copy()


def _metric_sort_key(row: dict[str, Any]) -> tuple[float, float, float, str]:
    csi = float(row["CSI"]) if row.get("CSI") is not None else -1.0
    hss = float(row["HSS"]) if row.get("HSS") is not None else -1.0
    far = float(row["FAR"]) if row.get("FAR") is not None else 1.0
    return (-csi, -hss, far, str(row["model"]))


def rank_models(metric_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank models independently within split, threshold, and product."""
    groups: dict[tuple[str, float, str], list[dict[str, Any]]] = {}
    for row in metric_rows:
        if row["model"] not in ACTIVE_MODELS:
            continue
        key = (row["split"], float(row["hourly_threshold_mm"]), row["product"])
        groups.setdefault(key, []).append(row)
    ranked: list[dict[str, Any]] = []
    for (split, threshold, product), rows in sorted(groups.items()):
        for rank, row in enumerate(sorted(rows, key=_metric_sort_key), start=1):
            ranked.append({
                "split": split,
                "hourly_threshold_mm": threshold,
                "product": product,
                "rank": rank,
                "model": row["model"],
                "CSI": row["CSI"],
                "HSS": row["HSS"],
                "POD": row["POD"],
                "FAR": row["FAR"],
                "frequency_bias": row["frequency_bias"],
                "observed_events": row["observed_events"],
            })
    return ranked


def ranking_stability(rankings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare development ranks with untouched holdout ranks."""
    development = {
        (float(row["hourly_threshold_mm"]), row["product"], row["model"]): row
        for row in rankings
        if row["split"] == "development_2023_2025"
    }
    holdout = {
        (float(row["hourly_threshold_mm"]), row["product"], row["model"]): row
        for row in rankings
        if row["split"] == "holdout_2026_h1"
    }
    rows: list[dict[str, Any]] = []
    for key in sorted(set(development).intersection(holdout)):
        dev = development[key]
        test = holdout[key]
        rows.append({
            "hourly_threshold_mm": key[0],
            "product": key[1],
            "model": key[2],
            "development_rank": int(dev["rank"]),
            "holdout_rank": int(test["rank"]),
            "rank_change_holdout_minus_development": int(test["rank"] - dev["rank"]),
            "development_CSI": dev["CSI"],
            "holdout_CSI": test["CSI"],
            "development_FAR": dev["FAR"],
            "holdout_FAR": test["FAR"],
            "development_top3": int(dev["rank"]) <= 3,
            "holdout_top3": int(test["rank"]) <= 3,
        })
    return rows


def _consensus_frame(frame: pd.DataFrame, models: Iterable[str]) -> pd.DataFrame:
    selected = tuple(models)
    subset = frame[frame["model"].isin(selected)].copy()
    forecast = subset.pivot(index="valid_time_utc", columns="model", values="forecast_rain")
    forecast = forecast.reindex(columns=selected)
    minimum_models = max(1, min(2, len(selected)))
    median = forecast.median(axis=1, skipna=True).where(forecast.notna().sum(axis=1).ge(minimum_models))
    observed = subset.groupby("valid_time_utc")["observed_rain"].first()
    combined = pd.concat([median.rename("forecast_rain"), observed.rename("observed_rain")], axis=1)
    return combined.dropna().reset_index()


def development_selected_ensemble_rows(
    pairs: pd.DataFrame,
    metric_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Freeze top-three development choices and evaluate only on holdout."""
    rankings = rank_models(metric_rows)
    development = [row for row in rankings if row["split"] == "development_2023_2025"]
    holdout = _split_frame(pairs, SPLITS[1])
    comparison: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    keys = sorted({(row["hourly_threshold_mm"], row["product"]) for row in development})
    for threshold, product in keys:
        ranked = sorted(
            [row for row in development if row["hourly_threshold_mm"] == threshold and row["product"] == product],
            key=lambda row: row["rank"],
        )
        selected = tuple(row["model"] for row in ranked[:3])
        selections.append({
            "hourly_threshold_mm": threshold,
            "product": product,
            "selection_source": "development_2023_2025_only",
            "selected_models": ",".join(selected),
            "holdout_opened_after_selection": True,
        })
        for label, models in (
            ("development_selected_top3_median", selected),
            ("all8_median_baseline", ACTIVE_MODELS),
        ):
            consensus = _consensus_frame(holdout, models)
            cadence = max(NATIVE_CADENCE_HOURS[model] for model in models)
            scored = score_frame(
                consensus,
                split="holdout_2026_h1",
                model=label,
                thresholds=(threshold,),
                native_cadence_hours=cadence,
            )
            row = next(item for item in scored if item["product"] == product)
            row["selected_models"] = ",".join(models)
            row["selection_source"] = "development_2023_2025_only" if label.startswith("development") else "fixed_all_active_models"
            comparison.append(row)
    return selections, comparison


def denominator_comparison(pairs: pd.DataFrame) -> list[dict[str, Any]]:
    """Expose why wet-hour, episode, expanded-hour, and block counts differ."""
    rows: list[dict[str, Any]] = []
    observations = pairs[["valid_time_utc", "observed_rain"]].drop_duplicates("valid_time_utc")
    for split in SPLITS:
        frame = _split_frame(observations, split).sort_values("valid_time_utc")
        values = frame.set_index("valid_time_utc")["observed_rain"].asfreq("h")
        for threshold in RAIN_REPLAY_THRESHOLDS_MM_H:
            score = event_window_metrics_for_model(
                pd.DataFrame({"Datetime": values.index, "forecast": values.values, "obs": values.values}),
                threshold=float(threshold),
                windows=(0, 1),
                block_hours=3,
                block_mode="sum",
                block_threshold=float(threshold),
                timestamp_label="interval_end",
            )
            legacy = values.rolling(3, min_periods=1, center=True).max().ge(float(threshold)).sum()
            strict = int(score["pm0h"]["observed_events"])
            rows.append({
                "split": split.name,
                "hourly_threshold_mm": float(threshold),
                "valid_hourly_samples": int(values.notna().sum()),
                "strict_wet_hours": strict,
                "unique_contiguous_episodes": int(score["pm1h"]["observed_events"]),
                "legacy_centered_expanded_wet_samples": int(legacy),
                "legacy_inflation_vs_strict": _round(_safe_ratio(int(legacy), strict)),
                "complete_3h_blocks": int(score["3h_block"]["sample_size"]),
                "wet_complete_3h_same_numeric": int(score["3h_block"]["observed_events"]),
                "legacy_status": "diagnostic_only_future_leaking_transformation",
            })
    return rows


def _weekly_vectors(
    frame: pd.DataFrame,
    threshold: float,
    product: str,
    cadence: int,
) -> dict[str, np.ndarray]:
    source = frame.copy()
    source["week"] = source["valid_time_utc"].dt.to_period("W-SUN")
    vectors: dict[str, np.ndarray] = {}
    for period, week in source.groupby("week", sort=True):
        rows = score_frame(
            week,
            split="bootstrap",
            model="candidate",
            thresholds=(threshold,),
            native_cadence_hours=cadence,
        )
        metric = next(row for row in rows if row["product"] == product)
        vectors[str(period)] = np.asarray([
            int(metric["hits"]),
            int(metric["misses"]),
            int(metric["false_alarms"]),
            int(metric["correct_negatives"] or 0),
        ], dtype=np.int64)
    return vectors


def _metrics_from_counts(totals: np.ndarray, conventional: bool) -> dict[str, np.ndarray]:
    hits, misses, false_alarms, correct = (totals[:, index].astype(float) for index in range(4))
    pod = np.divide(hits, hits + misses, out=np.full_like(hits, np.nan), where=(hits + misses) > 0)
    far = np.divide(false_alarms, hits + false_alarms, out=np.full_like(hits, np.nan), where=(hits + false_alarms) > 0)
    csi = np.divide(hits, hits + misses + false_alarms, out=np.full_like(hits, np.nan), where=(hits + misses + false_alarms) > 0)
    result = {"POD": pod, "FAR": far, "CSI": csi}
    if conventional:
        total = hits + misses + false_alarms + correct
        expected = np.divide(
            (hits + misses) * (hits + false_alarms) + (correct + misses) * (correct + false_alarms),
            total,
            out=np.zeros_like(total),
            where=total > 0,
        )
        denom = total - expected
        result["HSS"] = np.divide(
            hits + correct - expected,
            denom,
            out=np.full_like(total, np.nan),
            where=denom > 0,
        )
    return result


def bootstrap_holdout(
    pairs: pd.DataFrame,
    selections: list[dict[str, Any]],
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> list[dict[str, Any]]:
    """Seven-day block bootstrap for the primary threshold on frozen candidates."""
    holdout = _split_frame(pairs, SPLITS[1])
    target_products = (
        "strict_hourly",
        "episode_pm1h",
        "episode_pm2h",
        "complete_3h_rate_equivalent",
    )
    selection_lookup = {
        row["product"]: tuple(row["selected_models"].split(","))
        for row in selections
        if float(row["hourly_threshold_mm"]) == PRIMARY_THRESHOLD_MM_H
        and row["product"] in target_products
    }
    rng = np.random.default_rng(seed)
    output: list[dict[str, Any]] = []
    for product in target_products:
        candidates = (
            ("development_selected_top3_median", selection_lookup[product]),
            ("all8_median_baseline", ACTIVE_MODELS),
        )
        vector_maps: dict[str, dict[str, np.ndarray]] = {}
        model_labels: dict[str, tuple[str, ...]] = {}
        for candidate, models in candidates:
            frame = _consensus_frame(holdout, models)
            cadence = max(NATIVE_CADENCE_HOURS[model] for model in models)
            vector_maps[candidate] = _weekly_vectors(
                frame, PRIMARY_THRESHOLD_MM_H, product, cadence
            )
            model_labels[candidate] = tuple(models)

        common_weeks = sorted(set.intersection(*[set(values) for values in vector_maps.values()]))
        if not common_weeks:
            continue
        sampled = rng.integers(0, len(common_weeks), size=(replicates, len(common_weeks)))
        sampled_metrics: dict[str, dict[str, np.ndarray]] = {}
        for candidate, _ in candidates:
            vectors = np.asarray([vector_maps[candidate][week] for week in common_weeks])
            totals = vectors[sampled].sum(axis=1)
            sampled_metrics[candidate] = _metrics_from_counts(
                totals,
                conventional=product in {"strict_hourly", "complete_3h_rate_equivalent"},
            )
            for metric_name, values in sampled_metrics[candidate].items():
                finite = values[np.isfinite(values)]
                output.append({
                    "candidate": candidate,
                    "product": product,
                    "hourly_threshold_mm": PRIMARY_THRESHOLD_MM_H,
                    "metric": metric_name,
                    "bootstrap_unit": "calendar_7_day_block",
                    "bootstrap_replicates": int(replicates),
                    "finite_replicates": int(len(finite)),
                    "estimate_mean": _round(finite.mean()) if len(finite) else None,
                    "ci95_low": _round(np.quantile(finite, 0.025)) if len(finite) else None,
                    "ci95_high": _round(np.quantile(finite, 0.975)) if len(finite) else None,
                    "selected_models": ",".join(model_labels[candidate]),
                    "comparison": "absolute_metric",
                })

        selected_metrics = sampled_metrics["development_selected_top3_median"]
        baseline_metrics = sampled_metrics["all8_median_baseline"]
        for metric_name in selected_metrics:
            delta = selected_metrics[metric_name] - baseline_metrics[metric_name]
            finite = delta[np.isfinite(delta)]
            output.append({
                "candidate": "development_selected_top3_minus_all8",
                "product": product,
                "hourly_threshold_mm": PRIMARY_THRESHOLD_MM_H,
                "metric": metric_name,
                "bootstrap_unit": "calendar_7_day_block",
                "bootstrap_replicates": int(replicates),
                "finite_replicates": int(len(finite)),
                "estimate_mean": _round(finite.mean()) if len(finite) else None,
                "ci95_low": _round(np.quantile(finite, 0.025)) if len(finite) else None,
                "ci95_high": _round(np.quantile(finite, 0.975)) if len(finite) else None,
                "selected_models": ",".join(model_labels["development_selected_top3_median"]),
                "comparison": "paired_delta_selected_minus_all8",
            })
    return output


def _write_report(
    path: Path,
    summary: dict[str, Any],
    rankings: list[dict[str, Any]],
    ensemble_rows: list[dict[str, Any]],
    denominator_rows: list[dict[str, Any]],
    stability_rows: list[dict[str, Any]],
    bootstrap_rows: list[dict[str, Any]],
) -> None:
    holdout_primary = [
        row for row in rankings
        if row["split"] == "holdout_2026_h1"
        and float(row["hourly_threshold_mm"]) == PRIMARY_THRESHOLD_MM_H
        and row["rank"] <= 3
    ]
    lines = [
        "# WAWP Rainfall Shadow Replay",
        "",
        "## Standing",
        "",
        "- Audit only: no live weighting, QM, consensus, or TAF behavior was changed.",
        "- Source: Open-Meteo continuous historical forecast stream; not model-run or lead-aware.",
        "- Development: 2023-01-01 through 2025-12-31 UTC.",
        "- Independent holdout: 2026-01-01 through 2026-06-30 UTC.",
        "- Missing hours are unknown, episodes break at gaps, and incomplete 3-hour blocks are excluded.",
        "",
        "## Data Gate",
        "",
        f"- Exact joined rows: {summary['data_audit']['raw_exact_join_rows']:,}",
        f"- Usable rows: {summary['data_audit']['usable_pair_rows']:,}",
        f"- Quarantined rows: {summary['data_audit']['quarantined_rows']:,}",
        f"- Duplicate model/valid-time rows: {summary['data_audit']['duplicate_model_valid_time_rows']:,}",
        "",
        "## Why Counts Differ",
        "",
        "Strict wet hours, contiguous episodes, and complete 3-hour accumulations are different verification units. The legacy centered maximum expands an event into neighboring hours and uses future information; it is retained only as a diagnostic denominator and is never ranked.",
        "",
        "| Split | Threshold | Strict Hours | Episodes | Legacy Expanded | Complete 3h Wet |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in denominator_rows:
        if float(row["hourly_threshold_mm"]) == PRIMARY_THRESHOLD_MM_H:
            lines.append(
                f"| {row['split']} | {row['hourly_threshold_mm']:.1f} mm/h | {row['strict_wet_hours']:,} | "
                f"{row['unique_contiguous_episodes']:,} | {row['legacy_centered_expanded_wet_samples']:,} | "
                f"{row['wet_complete_3h_same_numeric']:,} |"
            )
    lines.extend([
        "",
        "## Holdout Top Three by Product at 1.5 mm/h",
        "",
        "| Product | Rank | Model | CSI | POD | FAR | Frequency Bias |",
        "|---|---:|---|---:|---:|---:|---:|",
    ])
    for row in sorted(holdout_primary, key=lambda item: (item["product"], item["rank"])):
        lines.append(
            f"| {row['product']} | {row['rank']} | {row['model']} | {row['CSI']} | "
            f"{row['POD']} | {row['FAR']} | {row['frequency_bias']} |"
        )
    lines.extend([
        "",
        "## Frozen Ensemble Holdout Comparison at 1.5 mm/h",
        "",
        "| Product | Candidate | CSI | POD | FAR | Models |",
        "|---|---|---:|---:|---:|---|",
    ])
    for row in ensemble_rows:
        if float(row["hourly_threshold_mm"]) == PRIMARY_THRESHOLD_MM_H:
            lines.append(
                f"| {row['product']} | {row['model']} | {row['CSI']} | {row['POD']} | "
                f"{row['FAR']} | {row['selected_models']} |"
            )
    lines.extend([
        "",
        "## Rank Stability at 1.5 mm/h",
        "",
        "| Product | Development Top Three Retained in Holdout | Mean Absolute Rank Change |",
        "|---|---:|---:|",
    ])
    primary_stability = [
        row for row in stability_rows
        if float(row["hourly_threshold_mm"]) == PRIMARY_THRESHOLD_MM_H
    ]
    for product in sorted({row["product"] for row in primary_stability}):
        rows = [row for row in primary_stability if row["product"] == product]
        retained = sum(bool(row["development_top3"]) and bool(row["holdout_top3"]) for row in rows)
        mean_change = np.mean([abs(int(row["rank_change_holdout_minus_development"])) for row in rows])
        lines.append(f"| {product} | {retained} of 3 | {mean_change:.2f} |")

    lines.extend([
        "",
        "## Paired Holdout Bootstrap Delta at 1.5 mm/h",
        "",
        "Delta is development-selected top-three median minus all-eight median. Positive POD/CSI/HSS is favorable; negative FAR is favorable. An interval crossing zero is inconclusive.",
        "",
        "| Product | Metric | Mean Delta | 95% CI |",
        "|---|---|---:|---:|",
    ])
    paired = [
        row for row in bootstrap_rows
        if row.get("comparison") == "paired_delta_selected_minus_all8"
    ]
    for row in paired:
        lines.append(
            f"| {row['product']} | {row['metric']} | {row['estimate_mean']} | "
            f"{row['ci95_low']} to {row['ci95_high']} |"
        )
    lines.extend([
        "",
        "## Operational Verdict",
        "",
        f"**{summary['operational_verdict']}**",
        "",
        "The development-selected ensemble improves detection in some products, but most CSI/HSS intervals cross zero, false-alarm rates remain high, and rankings move materially between development and holdout. The plus/minus two-hour selected ensemble has a significantly worse FAR than the all-eight median. This evidence does not justify activating event-window weights.",
        "",
        "## Interpretation Boundary",
        "",
        "The holdout can compare rainfall occurrence and timing definitions for this continuous historical stream. It cannot establish model-run lead skill, because archived initialization and lead provenance are absent. No candidate should influence operations until meteorological review and live multi-init shadow evidence agree.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_replay(
    db_path: Path,
    output_dir: Path,
    location: str = DEFAULT_LOCATION,
    bootstrap_replicates: int = BOOTSTRAP_REPLICATES,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with _read_only_connection(db_path) as conn:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        pairs, data_audit = load_historical_rain_pairs(conn, location=location)

    metric_rows: list[dict[str, Any]] = []
    amount_rows: list[dict[str, Any]] = []
    split_summary: list[dict[str, Any]] = []
    for split in SPLITS:
        split_frame = _split_frame(pairs, split)
        split_summary.append({
            "name": split.name,
            "start_utc_inclusive": split.start.isoformat(),
            "end_utc_exclusive": split.end.isoformat(),
            "pair_rows": int(len(split_frame)),
            "distinct_valid_hours": int(split_frame["valid_time_utc"].nunique()),
            "observed_wet_hours_ge_1_5": int(
                split_frame[["valid_time_utc", "observed_rain"]]
                .drop_duplicates("valid_time_utc")["observed_rain"].ge(PRIMARY_THRESHOLD_MM_H).sum()
            ),
        })
        for model in ACTIVE_MODELS:
            model_frame = split_frame[split_frame["model"] == model]
            metric_rows.extend(score_frame(
                model_frame,
                split=split.name,
                model=model,
                native_cadence_hours=NATIVE_CADENCE_HOURS[model],
            ))
            amount_rows.append(_amount_metrics(model_frame, split.name, model))

    rankings = rank_models(metric_rows)
    stability_rows = ranking_stability(rankings)
    selections, ensemble_rows = development_selected_ensemble_rows(pairs, metric_rows)
    denominator_rows = denominator_comparison(pairs)
    bootstrap_rows = bootstrap_holdout(
        pairs,
        selections,
        replicates=bootstrap_replicates,
    )
    summary = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "database": str(db_path.resolve()),
            "database_sha256": _sha256(db_path),
            "sqlite_quick_check": quick_check,
            "semantics_version": SEMANTICS_VERSION,
            "replay_version": "rainfall-shadow-replay-v1",
            "source_type": SOURCE_TYPE,
            "lead_aware": LEAD_AWARE,
            "operational_effect": "none",
            "selection_rule": "top three models by development CSI, HSS tie-break, then FAR; evaluated unchanged on holdout",
        },
        "data_audit": data_audit,
        "splits": split_summary,
        "thresholds_mm_per_hour": list(RAIN_REPLAY_THRESHOLDS_MM_H),
        "products": [
            "strict_hourly",
            "episode_pm1h",
            "episode_pm2h",
            "cadence_aware_episode",
            "complete_3h_same_numeric",
            "complete_3h_rate_equivalent",
        ],
        "native_cadence_hours": NATIVE_CADENCE_HOURS,
        "bootstrap": {
            "unit": "calendar_7_day_block",
            "replicates": int(bootstrap_replicates),
            "seed": BOOTSTRAP_SEED,
            "scope": "holdout primary threshold and frozen median ensembles",
        },
        "warnings": [
            "Continuous historical forecasts are not archived individual runs and cannot support lead-aware conclusions.",
            "The same-numeric 3-hour threshold is sensitivity-only; rate-equivalent uses hourly threshold multiplied by three.",
            "Cadence-aware episode tolerance is capped at plus/minus two hours and remains diagnostic for native 6-hour JMA guidance.",
            "Legacy centered expansion is future-leaking and is never used for ranking or promotion.",
        ],
        "operational_verdict": (
            "do not promote rainfall event-window weights from this replay; retain strict, episode, "
            "and complete-block products as separate shadow diagnostics"
        ),
    }

    _write_csv(output_dir / "rainfall_replay_metrics.csv", metric_rows)
    _write_csv(output_dir / "rainfall_amount_metrics.csv", amount_rows)
    _write_csv(output_dir / "model_rankings.csv", rankings)
    _write_csv(output_dir / "ranking_stability.csv", stability_rows)
    _write_csv(output_dir / "development_selections.csv", selections)
    _write_csv(output_dir / "holdout_ensemble_comparison.csv", ensemble_rows)
    _write_csv(output_dir / "metric_denominator_comparison.csv", denominator_rows)
    _write_csv(output_dir / "holdout_bootstrap_ci.csv", bootstrap_rows)
    (output_dir / "rainfall_shadow_replay_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    _write_report(
        output_dir / "RAINFALL_SHADOW_REPLAY_REPORT.md",
        summary,
        rankings,
        ensemble_rows,
        denominator_rows,
        stability_rows,
        bootstrap_rows,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("wawp_forecasts.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("audit/rainfall/replay"))
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--bootstrap-replicates", type=int, default=BOOTSTRAP_REPLICATES)
    args = parser.parse_args()
    summary = run_replay(
        args.database,
        args.output_dir,
        location=args.location,
        bootstrap_replicates=args.bootstrap_replicates,
    )
    print(json.dumps({"output_dir": str(args.output_dir), "splits": summary["splits"]}, indent=2))


if __name__ == "__main__":
    main()
