"""Shadow verification for strict hours, displaced episodes, and 3-hour products."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from src.meteorological_contract import interval_end_block_labels


@dataclass(frozen=True)
class EventWindowConfig:
    threshold: float
    windows: tuple[int, ...] = (0, 1, 2)
    block_hours: int = 3


@dataclass(frozen=True)
class EventEpisode:
    start: pd.Timestamp
    end: pd.Timestamp
    peak_time: pd.Timestamp
    peak_value: float
    duration_hours: int


def _metric_value(metrics: dict, key: str, field: str, default: float = 0.0) -> float:
    value = (metrics.get(key) or {}).get(field, default)
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _event_count(metrics: dict) -> int:
    # Episode counts are invariant across timing tolerances. Prefer that
    # denominator over wet-hour or three-hour-bin counts.
    for key in ("pm1h", "pm2h"):
        metric = metrics.get(key) or {}
        if metric.get("verification_unit") != "one_to_one_episode":
            continue
        try:
            return int(metric.get("observed_events") or 0)
        except (TypeError, ValueError):
            pass
    try:
        return int((metrics.get("pm0h") or {}).get("observed_events") or 0)
    except (TypeError, ValueError):
        return 0


def _forecast_event_count(metrics: dict) -> int:
    for key in ("pm1h", "pm2h"):
        metric = metrics.get(key) or {}
        if metric.get("verification_unit") != "one_to_one_episode":
            continue
        try:
            return int(metric.get("forecast_events") or 0)
        except (TypeError, ValueError):
            pass
    try:
        return int((metrics.get("pm0h") or {}).get("forecast_events") or 0)
    except (TypeError, ValueError):
        return 0


def _rain_event_score(metrics: dict, min_events: int) -> float:
    skill = (
        0.25 * max(0.0, _metric_value(metrics, "pm0h", "HSS"))
        + 0.30 * max(0.0, _metric_value(metrics, "pm1h", "CSI"))
        + 0.20 * max(0.0, _metric_value(metrics, "pm2h", "CSI"))
        + 0.25 * max(0.0, _metric_value(metrics, "3h_block", "HSS"))
    )
    window_weights = {"pm0h": 0.25, "pm1h": 0.30, "pm2h": 0.20, "3h_block": 0.25}
    weighted_far = sum(
        weight * _metric_value(metrics, key, "FAR", 1.0)
        for key, weight in window_weights.items()
    )
    strict_far = _metric_value(metrics, "pm0h", "FAR", 1.0)

    far_penalty = 0.25 * max(0.0, weighted_far - 0.35) + 0.15 * max(0.0, strict_far - 0.50)
    sample_shrink = min(1.0, _event_count(metrics) / max(float(min_events), 1.0))
    return max(0.0, skill - far_penalty) * sample_shrink


def _gust_event_score(metrics: dict, min_events: int) -> float:
    window_weights = {"pm0h": 0.25, "pm1h": 0.30, "pm2h": 0.25, "3h_block": 0.20}
    timing_skill = 0.0
    weighted_far = 0.0
    for key, weight in window_weights.items():
        hss = max(0.0, _metric_value(metrics, key, "HSS")) if key in {"pm0h", "3h_block"} else 0.0
        csi = max(0.0, _metric_value(metrics, key, "CSI"))
        timing_skill += weight * ((0.55 * hss) + (0.45 * csi) if hss else csi)
        weighted_far += weight * _metric_value(metrics, key, "FAR", 1.0)

    peak_mae = (metrics.get("amount_or_peak_error") or {}).get("MAE")
    try:
        peak_mae = float(peak_mae)
    except (TypeError, ValueError):
        peak_mae = None
    peak_score = 0.0 if peak_mae is None else 1.0 / (1.0 + max(0.0, peak_mae) / 10.0)

    far_penalty = 0.20 * max(0.0, weighted_far - 0.45)
    sample_shrink = min(1.0, _event_count(metrics) / max(float(min_events), 1.0))
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
        forecast_events = _forecast_event_count(metrics)

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
            "pm1h_csi": round(_metric_value(metrics, "pm1h", "CSI"), 4),
            "pm2h_csi": round(_metric_value(metrics, "pm2h", "CSI"), 4),
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
            "metric_schema_version": "event-window-v2-shadow",
        }

    total = sum(eligible.values())
    event_weights = {m: (eligible.get(m, 0.0) / total if total > 0 else 0.0) for m in models}
    return {
        "applied": True,
        "reason": "event-window skill blended into event-sensitive weights",
        "min_events": int(min_events),
        "event_weights": {m: round(float(event_weights[m]), 6) for m in models},
        "model_scores": model_scores,
        "metric_schema_version": "event-window-v2-shadow",
    }


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return float(numerator / denominator) if denominator else None


def _score_aligned_binary(
    obs_event: np.ndarray,
    fc_event: np.ndarray,
    timestamps: pd.DatetimeIndex,
) -> dict:
    obs_event = np.asarray(obs_event, dtype=bool)
    fc_event = np.asarray(fc_event, dtype=bool)
    hits = int(np.sum(obs_event & fc_event))
    misses = int(np.sum(obs_event & ~fc_event))
    false_alarms = int(np.sum(~obs_event & fc_event))
    correct_neg = int(np.sum(~obs_event & ~fc_event))
    total = int(len(timestamps))
    pod = _safe_ratio(hits, hits + misses)
    far = _safe_ratio(false_alarms, hits + false_alarms)
    csi = _safe_ratio(hits, hits + misses + false_alarms)
    expected = (
        ((hits + misses) * (hits + false_alarms))
        + ((correct_neg + misses) * (correct_neg + false_alarms))
    ) / total if total else 0.0
    denom = total - expected
    hss = (hits + correct_neg - expected) / denom if denom > 0 else None

    return {
        "threshold": None,
        "window_hours": 0,
        "verification_unit": "sample",
        "sample_size": total,
        "observed_events": int(obs_event.sum()),
        "forecast_events": int(fc_event.sum()),
        "hits": int(hits),
        "misses": int(misses),
        "false_alarms": int(false_alarms),
        "correct_negatives": int(correct_neg),
        "POD": round(pod, 4) if pod is not None else None,
        "FAR": round(far, 4) if far is not None else None,
        "CSI": round(csi, 4) if csi is not None else None,
        "HSS": round(float(hss), 4) if hss is not None else None,
        "mean_abs_timing_error_h": 0.0 if hits else None,
        "mean_signed_timing_error_h": 0.0 if hits else None,
    }


def _extract_episodes(values: pd.Series, threshold: float) -> list[EventEpisode]:
    episodes: list[EventEpisode] = []
    active: list[tuple[pd.Timestamp, float]] = []

    def close_episode() -> None:
        if not active:
            return
        peak_time, peak_value = max(active, key=lambda item: item[1])
        episodes.append(EventEpisode(
            start=active[0][0],
            end=active[-1][0],
            peak_time=peak_time,
            peak_value=float(peak_value),
            duration_hours=len(active),
        ))
        active.clear()

    previous_time: pd.Timestamp | None = None
    for timestamp, raw_value in values.items():
        timestamp = pd.Timestamp(timestamp)
        wet = pd.notna(raw_value) and float(raw_value) >= threshold
        consecutive = previous_time is not None and timestamp - previous_time == pd.Timedelta(hours=1)
        if not wet or (active and not consecutive):
            close_episode()
        if wet:
            active.append((timestamp, float(raw_value)))
        previous_time = timestamp
    close_episode()
    return episodes


def _match_episodes(
    observed: list[EventEpisode],
    forecast: list[EventEpisode],
    window_h: int,
) -> list[tuple[int, int]]:
    """Maximum-cardinality, minimum-timing-error ordered one-to-one matching."""
    n_obs, n_fc = len(observed), len(forecast)
    matches = np.zeros((n_obs + 1, n_fc + 1), dtype=np.int32)
    costs = np.zeros((n_obs + 1, n_fc + 1), dtype=float)
    action = np.zeros((n_obs + 1, n_fc + 1), dtype=np.uint8)

    for i in range(1, n_obs + 1):
        for j in range(1, n_fc + 1):
            options = [
                (matches[i - 1, j], costs[i - 1, j], 1),
                (matches[i, j - 1], costs[i, j - 1], 2),
            ]
            offset = (
                forecast[j - 1].peak_time - observed[i - 1].peak_time
            ).total_seconds() / 3600.0
            if abs(offset) <= window_h:
                options.append((matches[i - 1, j - 1] + 1, costs[i - 1, j - 1] + abs(offset), 3))
            best = max(options, key=lambda item: (item[0], -item[1], item[2] == 3))
            matches[i, j], costs[i, j], action[i, j] = best

    pairs: list[tuple[int, int]] = []
    i, j = n_obs, n_fc
    while i > 0 and j > 0:
        if action[i, j] == 3:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif action[i, j] == 1:
            i -= 1
        else:
            j -= 1
    return list(reversed(pairs))


def _score_episode_window(
    observed: list[EventEpisode],
    forecast: list[EventEpisode],
    window_h: int,
    evaluation_hours: int,
) -> dict:
    pairs = _match_episodes(observed, forecast, window_h)
    hits = len(pairs)
    misses = len(observed) - hits
    false_alarms = len(forecast) - hits
    offsets = [
        (forecast[j].peak_time - observed[i].peak_time).total_seconds() / 3600.0
        for i, j in pairs
    ]
    peak_errors = [forecast[j].peak_value - observed[i].peak_value for i, j in pairs]
    duration_errors = [forecast[j].duration_hours - observed[i].duration_hours for i, j in pairs]
    pod = _safe_ratio(hits, hits + misses)
    far = _safe_ratio(false_alarms, hits + false_alarms)
    csi = _safe_ratio(hits, hits + misses + false_alarms)
    return {
        "threshold": None,
        "window_hours": int(window_h),
        "verification_unit": "one_to_one_episode",
        "sample_size": int(evaluation_hours),
        "observed_events": int(len(observed)),
        "forecast_events": int(len(forecast)),
        "hits": int(hits),
        "misses": int(misses),
        "false_alarms": int(false_alarms),
        "correct_negatives": None,
        "POD": round(pod, 4) if pod is not None else None,
        "FAR": round(far, 4) if far is not None else None,
        "CSI": round(csi, 4) if csi is not None else None,
        "HSS": None,
        "mean_abs_timing_error_h": round(float(np.mean(np.abs(offsets))), 3) if offsets else None,
        "mean_signed_timing_error_h": round(float(np.mean(offsets)), 3) if offsets else None,
        "peak_MAE": round(float(np.mean(np.abs(peak_errors))), 3) if peak_errors else None,
        "peak_Bias": round(float(np.mean(peak_errors)), 3) if peak_errors else None,
        "duration_MAE_h": round(float(np.mean(np.abs(duration_errors))), 3) if duration_errors else None,
        "matching": "ordered maximum-cardinality, minimum-total-peak-time-error",
    }


def _complete_block_values(
    values: pd.Series,
    block_hours: int,
    mode: str,
    timestamp_label: str = "interval_end",
) -> pd.Series:
    if block_hours <= 0:
        raise ValueError("block_hours must be positive")
    if timestamp_label == "interval_end":
        labels = interval_end_block_labels(pd.DatetimeIndex(values.index), block_hours)
    elif timestamp_label == "interval_start":
        labels = values.index.floor(f"{int(block_hours)}h")
    else:
        raise ValueError("timestamp_label must be 'interval_end' or 'interval_start'")
    groups = values.groupby(labels)
    counts = groups.count()
    if mode == "sum":
        aggregated = groups.sum(min_count=block_hours)
    elif mode == "max":
        aggregated = groups.max()
    else:
        raise ValueError("block_mode must be 'sum' or 'max'")
    return aggregated[counts.eq(block_hours)].dropna()


def event_window_metrics_for_model(
    df_model: pd.DataFrame,
    threshold: float,
    windows: Iterable[int] = (0, 1, 2),
    block_hours: int = 3,
    block_mode: str = "sum",
    block_threshold: float | None = None,
    timestamp_label: str = "interval_end",
    amount_mode: str = "peak",
) -> dict:
    if df_model.empty:
        return {}

    df = df_model.copy()
    df["Datetime"] = pd.to_datetime(df["Datetime"], errors="coerce")
    df = df.dropna(subset=["Datetime"]).sort_values("Datetime")
    if df.empty:
        return {}

    df = df.drop_duplicates(subset=["Datetime"], keep="last").set_index("Datetime").asfreq("h")
    forecast = pd.to_numeric(df["forecast"], errors="coerce")
    obs = pd.to_numeric(df["obs"], errors="coerce")
    valid = forecast.notna() & obs.notna()
    paired_forecast = forecast.where(valid)
    paired_obs = obs.where(valid)
    timestamps = pd.DatetimeIndex(df.index[valid])
    if len(timestamps) == 0:
        return {}

    strict_obs = paired_obs[valid]
    strict_forecast = paired_forecast[valid]
    obs_event = strict_obs.ge(threshold).to_numpy()
    fc_event = strict_forecast.ge(threshold).to_numpy()
    out = {
        "schema_version": "event-window-v2-shadow",
        "status": "shadow_only",
    }
    for window in windows:
        if int(window) == 0:
            score = _score_aligned_binary(obs_event, fc_event, timestamps)
        else:
            observed_episodes = _extract_episodes(paired_obs, threshold)
            forecast_episodes = _extract_episodes(paired_forecast, threshold)
            score = _score_episode_window(
                observed_episodes,
                forecast_episodes,
                int(window),
                evaluation_hours=int(valid.sum()),
            )
        score["threshold"] = threshold
        score["threshold_unit"] = "value per source interval"
        out[f"pm{int(window)}h"] = score

    block_obs_values = _complete_block_values(
        paired_obs, block_hours, block_mode, timestamp_label=timestamp_label
    )
    block_fc_values = _complete_block_values(
        paired_forecast, block_hours, block_mode, timestamp_label=timestamp_label
    )
    complete_blocks = block_obs_values.index.intersection(block_fc_values.index)
    block_obs_values = block_obs_values.loc[complete_blocks]
    block_fc_values = block_fc_values.loc[complete_blocks]
    effective_block_threshold = threshold if block_threshold is None else float(block_threshold)
    block_score = _score_aligned_binary(
        block_obs_values.ge(effective_block_threshold).to_numpy(),
        block_fc_values.ge(effective_block_threshold).to_numpy(),
        pd.DatetimeIndex(complete_blocks),
    )
    block_score["threshold"] = effective_block_threshold
    block_score["block_hours"] = int(block_hours)
    block_score["aggregation"] = block_mode
    block_score["timestamp_label"] = timestamp_label
    block_score["block_label"] = "block_end" if timestamp_label == "interval_end" else "block_start"
    block_score["verification_unit"] = f"complete_{block_hours}h_{block_mode}"
    block_score["threshold_unit"] = f"value/{block_hours}h" if block_mode == "sum" else "peak value"
    amount_errors = block_fc_values - block_obs_values
    wet_blocks = block_obs_values.ge(effective_block_threshold)
    wet_amount_errors = amount_errors[wet_blocks]
    block_score["amount_MAE"] = (
        round(float(wet_amount_errors.abs().mean()), 3) if not wet_amount_errors.empty else None
    )
    block_score["amount_Bias"] = (
        round(float(wet_amount_errors.mean()), 3) if not wet_amount_errors.empty else None
    )
    out[f"{int(block_hours)}h_block"] = block_score

    widest_key = f"pm{int(max(windows))}h"
    widest = out.get(widest_key) or {}
    out["amount_or_peak_error"] = {
        "threshold": threshold,
        "window_hours": int(max(windows)),
        "mode": amount_mode,
        "event_count": int(widest.get("hits") or 0),
        "MAE": widest.get("peak_MAE"),
        "Bias": widest.get("peak_Bias"),
    }
    return out


def event_window_metrics(
    df_long: pd.DataFrame,
    threshold: float,
    windows: Iterable[int] = (0, 1, 2),
    block_hours: int = 3,
    block_mode: str = "sum",
    block_threshold: float | None = None,
    timestamp_label: str = "interval_end",
) -> dict:
    if df_long.empty or not {"Model", "Datetime", "forecast", "obs"}.issubset(df_long.columns):
        return {}
    metrics = {}
    for model, df_model in df_long.groupby("Model"):
        metrics[str(model)] = event_window_metrics_for_model(
            df_model,
            threshold,
            windows,
            block_hours,
            block_mode=block_mode,
            block_threshold=block_threshold,
            timestamp_label=timestamp_label,
        )
    return metrics
