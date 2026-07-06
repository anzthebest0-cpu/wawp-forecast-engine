"""Event-window verification for displaced rain and gust forecasts.

Strict hourly scoring is useful, but tropical convective rain and gust peaks are
often displaced by one or two hours. These helpers score whether a model caught
the event near the observation time without pretending the exact hour was
perfect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EventWindowConfig:
    threshold: float
    windows: tuple[int, ...] = (0, 1, 2)
    block_hours: int = 3


def _metric_value(metrics: dict, key: str, field: str, default: float = 0.0) -> float:
    value = (metrics.get(key) or {}).get(field, default)
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _event_count(metrics: dict) -> int:
    counts = []
    for key in ("pm0h", "pm1h", "pm2h", "3h_block"):
        value = (metrics.get(key) or {}).get("observed_events")
        try:
            counts.append(int(value or 0))
        except (TypeError, ValueError):
            pass
    return max(counts) if counts else 0


def _rain_event_score(metrics: dict, min_events: int) -> float:
    window_weights = {"pm0h": 0.25, "pm1h": 0.30, "pm2h": 0.25, "3h_block": 0.20}
    skill = sum(weight * max(0.0, _metric_value(metrics, key, "HSS")) for key, weight in window_weights.items())
    weighted_far = sum(weight * _metric_value(metrics, key, "FAR", 1.0) for key, weight in window_weights.items())
    strict_far = _metric_value(metrics, "pm0h", "FAR", 1.0)

    far_penalty = 0.25 * max(0.0, weighted_far - 0.35) + 0.15 * max(0.0, strict_far - 0.50)
    sample_shrink = min(1.0, _event_count(metrics) / max(float(min_events * 3), 1.0))
    return max(0.0, skill - far_penalty) * sample_shrink


def _gust_event_score(metrics: dict, min_events: int) -> float:
    window_weights = {"pm0h": 0.25, "pm1h": 0.30, "pm2h": 0.25, "3h_block": 0.20}
    timing_skill = 0.0
    weighted_far = 0.0
    for key, weight in window_weights.items():
        hss = max(0.0, _metric_value(metrics, key, "HSS"))
        csi = max(0.0, _metric_value(metrics, key, "CSI"))
        timing_skill += weight * ((0.55 * hss) + (0.45 * csi))
        weighted_far += weight * _metric_value(metrics, key, "FAR", 1.0)

    peak_mae = (metrics.get("amount_or_peak_error") or {}).get("MAE")
    try:
        peak_mae = float(peak_mae)
    except (TypeError, ValueError):
        peak_mae = None
    peak_score = 0.0 if peak_mae is None else 1.0 / (1.0 + max(0.0, peak_mae) / 10.0)

    far_penalty = 0.20 * max(0.0, weighted_far - 0.45)
    sample_shrink = min(1.0, _event_count(metrics) / max(float(min_events * 3), 1.0))
    return max(0.0, (0.80 * timing_skill) + (0.20 * peak_score) - far_penalty) * sample_shrink


def event_window_weight_scores(
    event_metrics: dict,
    parameter: str,
    models: Iterable[str],
    min_events: int = 10,
) -> dict:
    """Convert rain/gust event-window verification into optional model weights.

    This is deliberately conservative. It does not replace the normal advanced
    weights by itself; callers should blend these event weights into the normal
    weights only for event-sensitive parameters.
    """
    model_scores = {}
    for model in models:
        metrics = event_metrics.get(model) or {}
        events = _event_count(metrics)
        forecast_events = max(
            int((metrics.get(key) or {}).get("forecast_events") or 0)
            for key in ("pm0h", "pm1h", "pm2h", "3h_block")
        ) if metrics else 0

        if events < min_events:
            model_scores[model] = {
                "eligible": False,
                "reason": f"needs at least {min_events} observed events",
                "score": 0.0,
                "observed_events": events,
                "forecast_events": forecast_events,
            }
            continue

        if parameter == "Rainfall":
            score = _rain_event_score(metrics, min_events)
        elif parameter == "Wind Gust":
            score = _gust_event_score(metrics, min_events)
        else:
            score = 0.0

        model_scores[model] = {
            "eligible": score > 0.0,
            "reason": "usable event-window skill" if score > 0.0 else "no positive event-window skill",
            "score": round(float(score), 6),
            "observed_events": events,
            "forecast_events": forecast_events,
            "pm0h_hss": round(_metric_value(metrics, "pm0h", "HSS"), 4),
            "pm1h_hss": round(_metric_value(metrics, "pm1h", "HSS"), 4),
            "pm2h_hss": round(_metric_value(metrics, "pm2h", "HSS"), 4),
            "block_hss": round(_metric_value(metrics, "3h_block", "HSS"), 4),
            "pm2h_far": round(_metric_value(metrics, "pm2h", "FAR", 1.0), 4),
        }

    eligible = {m: s["score"] for m, s in model_scores.items() if s["eligible"] and s["score"] > 0.0}
    if len(eligible) < 2:
        return {
            "applied": False,
            "reason": "event-window weighting pending until at least two models have positive event skill",
            "min_events": int(min_events),
            "event_weights": {},
            "model_scores": model_scores,
        }

    total = sum(eligible.values())
    event_weights = {m: (eligible.get(m, 0.0) / total if total > 0 else 0.0) for m in models}
    return {
        "applied": True,
        "reason": "event-window skill blended into event-sensitive weights",
        "min_events": int(min_events),
        "event_weights": {m: round(float(event_weights[m]), 6) for m in models},
        "model_scores": model_scores,
    }


def _score_binary(obs_event: np.ndarray, fc_event: np.ndarray, timestamps: pd.DatetimeIndex, window_h: int) -> dict:
    obs_event = np.asarray(obs_event, dtype=bool)
    fc_event = np.asarray(fc_event, dtype=bool)
    hits = 0
    misses = 0
    false_alarms = 0
    timing_offsets: list[float] = []

    event_times = timestamps[fc_event]
    for obs_time, is_obs in zip(timestamps, obs_event):
        if not is_obs:
            continue
        if len(event_times) == 0:
            misses += 1
            continue
        offsets = (event_times - obs_time).total_seconds() / 3600.0
        in_window = np.abs(offsets) <= window_h
        if np.any(in_window):
            hits += 1
            timing_offsets.append(float(offsets[in_window][np.argmin(np.abs(offsets[in_window]))]))
        else:
            misses += 1

    obs_times = timestamps[obs_event]
    for fc_time, is_fc in zip(timestamps, fc_event):
        if not is_fc:
            continue
        if len(obs_times) == 0:
            false_alarms += 1
            continue
        offsets = (obs_times - fc_time).total_seconds() / 3600.0
        if not np.any(np.abs(offsets) <= window_h):
            false_alarms += 1

    total = int(len(timestamps))
    correct_neg = max(0, total - hits - misses - false_alarms)
    pod = hits / (hits + misses) if (hits + misses) else 0.0
    far = false_alarms / (hits + false_alarms) if (hits + false_alarms) else 1.0
    csi = hits / (hits + misses + false_alarms) if (hits + misses + false_alarms) else 0.0
    expected = (
        ((hits + misses) * (hits + false_alarms))
        + ((correct_neg + misses) * (correct_neg + false_alarms))
    ) / total if total else 0.0
    denom = total - expected
    hss = (hits + correct_neg - expected) / denom if denom > 0 else 0.0

    return {
        "threshold": None,
        "window_hours": int(window_h),
        "sample_size": total,
        "observed_events": int(obs_event.sum()),
        "forecast_events": int(fc_event.sum()),
        "hits": int(hits),
        "misses": int(misses),
        "false_alarms": int(false_alarms),
        "correct_negatives": int(correct_neg),
        "POD": round(float(pod), 4),
        "FAR": round(float(far), 4),
        "CSI": round(float(csi), 4),
        "HSS": round(float(hss), 4),
        "mean_abs_timing_error_h": round(float(np.mean(np.abs(timing_offsets))), 3) if timing_offsets else None,
        "mean_signed_timing_error_h": round(float(np.mean(timing_offsets)), 3) if timing_offsets else None,
    }


def _contiguous_block_event(values: pd.Series, threshold: float, block_hours: int) -> pd.Series:
    if block_hours <= 1:
        return values >= threshold
    rolling_peak = values.rolling(block_hours, min_periods=1, center=True).max()
    return rolling_peak >= threshold


def event_window_metrics_for_model(
    df_model: pd.DataFrame,
    threshold: float,
    windows: Iterable[int] = (0, 1, 2),
    block_hours: int = 3,
    amount_mode: str = "peak",
) -> dict:
    if df_model.empty:
        return {}

    df = df_model.copy()
    df["Datetime"] = pd.to_datetime(df["Datetime"], errors="coerce")
    df = df.dropna(subset=["Datetime", "forecast", "obs"]).sort_values("Datetime")
    if df.empty:
        return {}

    df = df.drop_duplicates(subset=["Datetime"], keep="last").set_index("Datetime").asfreq("h")
    forecast = pd.to_numeric(df["forecast"], errors="coerce")
    obs = pd.to_numeric(df["obs"], errors="coerce")
    valid = forecast.notna() & obs.notna()
    forecast = forecast[valid]
    obs = obs[valid]
    timestamps = pd.DatetimeIndex(obs.index)
    if len(timestamps) == 0:
        return {}

    obs_event = (obs >= threshold).to_numpy()
    fc_event = (forecast >= threshold).to_numpy()
    out = {}
    for window in windows:
        score = _score_binary(obs_event, fc_event, timestamps, int(window))
        score["threshold"] = threshold
        out[f"pm{int(window)}h"] = score

    block_obs = _contiguous_block_event(obs, threshold, block_hours).to_numpy()
    block_fc = _contiguous_block_event(forecast, threshold, block_hours).to_numpy()
    block_score = _score_binary(block_obs, block_fc, timestamps, 0)
    block_score["threshold"] = threshold
    block_score["block_hours"] = int(block_hours)
    out[f"{int(block_hours)}h_block"] = block_score

    amount_errors = []
    for obs_time, obs_value, is_obs in zip(timestamps, obs.to_numpy(), obs_event):
        if not is_obs:
            continue
        windowed = forecast[(forecast.index >= obs_time - pd.Timedelta(hours=max(windows))) &
                            (forecast.index <= obs_time + pd.Timedelta(hours=max(windows)))]
        if windowed.empty:
            continue
        matched_value = float(windowed.max()) if amount_mode == "peak" else float(windowed.iloc[0])
        amount_errors.append(matched_value - float(obs_value))
    out["amount_or_peak_error"] = {
        "threshold": threshold,
        "window_hours": int(max(windows)),
        "mode": amount_mode,
        "event_count": len(amount_errors),
        "MAE": round(float(np.mean(np.abs(amount_errors))), 3) if amount_errors else None,
        "Bias": round(float(np.mean(amount_errors)), 3) if amount_errors else None,
    }
    return out


def event_window_metrics(df_long: pd.DataFrame, threshold: float, windows: Iterable[int] = (0, 1, 2), block_hours: int = 3) -> dict:
    if df_long.empty or not {"Model", "Datetime", "forecast", "obs"}.issubset(df_long.columns):
        return {}
    metrics = {}
    for model, df_model in df_long.groupby("Model"):
        metrics[str(model)] = event_window_metrics_for_model(df_model, threshold, windows, block_hours)
    return metrics
