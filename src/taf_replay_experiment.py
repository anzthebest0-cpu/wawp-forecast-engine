"""Controlled continuous-prior TAF replay experiment.

This runner generates historical machine TAFs without claiming archived
model-run lead awareness.  It uses the Open-Meteo continuous historical stream
as a local historical prior and labels every output accordingly.  QM training
is frozen before the evaluation period, while ensemble weights are calculated
as of each historical issuance using only earlier forecast-observation pairs.

The plain-text outputs contain exactly one headerless, single-line TAF per
line. Configuration and replay metadata are written separately as CSV/JSON.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Support both ``python -m src.taf_replay_experiment`` and direct invocation
# from the repository root, which is how the replay is run in CI and locally.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tafor_generator import generate_tafor


MODELS = [
    "ECMWF_HRES",
    "GFS_GLOBAL",
    "ICON_SEAMLESS",
    "GEM_GLOBAL",
    "CMA_GRAPES_GLOBAL",
    "JMA_GSM",
    "METEOFRANCE_ARPEGE_WORLD",
    "UKMO_GLOBAL_10KM",
]

PAIR_COLUMNS = {
    "Temperature": ("fcst_temperature", "obs_temperature", "linear"),
    "Dewpoint": ("fcst_dewpoint", "obs_dewpoint", "linear"),
    "Pressure": ("fcst_pressure", "obs_pressure", "linear"),
    "Rainfall": ("fcst_rain", "obs_rain", "rain"),
    "Wind": ("fcst_wind_speed", "obs_wind_speed", "nonnegative"),
    "Wind Gust": ("fcst_wind_gust", "obs_wind_gust", "nonnegative"),
    "Wind Dir.": ("fcst_wind_dir", "obs_wind_dir", "circular"),
}

FORECAST_COLUMNS = {
    "Temperature": "temperature",
    "Dewpoint": "dewpoint",
    "Humidity": "humidity",
    "Pressure": "pressure_msl",
    "Rainfall": "rain",
    "Wind": "wind_speed",
    "Wind Gust": "wind_gust",
    "Wind Dir.": "wind_dir",
    "Visibility": "visibility",
    "Low Clouds": "cloud_cover_low",
    "Mid Clouds": "cloud_cover_mid",
    "High Clouds": "cloud_cover_high",
    "CAPE": "cape",
    "Lifted Index": "lifted_index",
    "Convective Inhibition": "convective_inhib",
    "Weather Code": "weather_code",
}


@dataclass(frozen=True)
class ReplayConfig:
    name: str
    weight_mode: str
    lookback_days: int | None
    qm_mode: str
    conservative_caps: bool = False


CONFIGS = {
    "raw_equal": ReplayConfig("raw_equal", "equal", None, "none"),
    "raw_asof30": ReplayConfig("raw_asof30", "asof", 30, "none"),
    "raw_asof60": ReplayConfig("raw_asof60", "asof", 60, "none"),
    "raw_asof90": ReplayConfig("raw_asof90", "asof", 90, "none"),
    "prior_global_asof60": ReplayConfig("prior_global_asof60", "asof", 60, "global"),
    "prior_regime_asof60": ReplayConfig("prior_regime_asof60", "asof", 60, "regime"),
    "prior_regime_capped_asof60": ReplayConfig(
        "prior_regime_capped_asof60", "asof", 60, "regime", True
    ),
    "prior_regime_capped_asof90": ReplayConfig(
        "prior_regime_capped_asof90", "asof", 90, "regime", True
    ),
}


def _regime(timestamp: pd.Timestamp) -> str:
    hour = int(timestamp.hour)
    if 6 <= hour <= 11:
        return "morning_06_11"
    if 12 <= hour <= 19:
        return "convective_12_19"
    return "night_20_05"


def _circular_distance(a: pd.Series, b: pd.Series) -> pd.Series:
    return ((a - b + 180.0) % 360.0 - 180.0).abs()


def _weighted_average(values: pd.Series, weights: dict[str, float]) -> float:
    valid = values.dropna()
    if valid.empty:
        return float("nan")
    w = np.array([max(0.0, float(weights.get(model, 0.0))) for model in valid.index])
    if w.sum() <= 0:
        w = np.ones(len(valid), dtype=float)
    return float(np.average(valid.astype(float).to_numpy(), weights=w))


def _weighted_circular(values: pd.Series, weights: dict[str, float]) -> float:
    valid = values.dropna()
    if valid.empty:
        return float("nan")
    angles = np.deg2rad(valid.astype(float).to_numpy())
    w = np.array([max(0.0, float(weights.get(model, 0.0))) for model in valid.index])
    if w.sum() <= 0:
        w = np.ones(len(valid), dtype=float)
    return float(np.rad2deg(math.atan2(np.sum(w * np.sin(angles)), np.sum(w * np.cos(angles)))) % 360.0)


def _weighted_median(values: pd.Series, weights: dict[str, float]) -> float:
    valid = values.dropna().astype(float)
    if valid.empty:
        return float("nan")
    ordered = sorted((value, max(0.0, float(weights.get(model, 0.0)))) for model, value in valid.items())
    total = sum(weight for _, weight in ordered)
    if total <= 0:
        return float(np.median([value for value, _ in ordered]))
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= total / 2:
            return float(value)
    return float(ordered[-1][0])


def _weighted_weather_code(values: pd.Series, weights: dict[str, float]) -> float:
    votes: dict[int, float] = {}
    for model, value in values.dropna().items():
        code = int(round(float(value)))
        votes[code] = votes.get(code, 0.0) + max(0.0, float(weights.get(model, 0.0)))
    if not votes:
        return float("nan")
    # Prefer the more significant valid category on a weight tie.
    return float(max(votes, key=lambda code: (votes[code], code)))


def _flatten_taf(raw_taf: str) -> str:
    """Return exactly the user-facing one-line TAF without bulletin header."""
    start = raw_taf.find("TAF WAWP")
    if start < 0:
        raise ValueError(f"TAF generator did not return a WAWP TAF: {raw_taf!r}")
    taf = " ".join(raw_taf[start:].replace("\r", " ").replace("\n", " ").split())
    return taf if taf.endswith("=") else taf + "="


class HistoricalPrior:
    """Empirical continuous-history prior frozen before replay starts."""

    def __init__(self, pairs: pd.DataFrame, cutoff: pd.Timestamp):
        self.cutoff = cutoff
        self.tables: dict[tuple[str, str, str], tuple[np.ndarray, np.ndarray]] = {}
        self.direction_offsets: dict[tuple[str, str], float] = {}
        self._fit(pairs[pairs["valid_dt"] < cutoff].copy())

    def _fit(self, pairs: pd.DataFrame) -> None:
        pairs["regime"] = pairs["valid_dt"].map(_regime)
        for parameter, (fc_col, obs_col, kind) in PAIR_COLUMNS.items():
            for model in MODELS:
                model_rows = pairs[pairs["model"] == model]
                for regime in ("ALL", "morning_06_11", "convective_12_19", "night_20_05"):
                    rows = model_rows if regime == "ALL" else model_rows[model_rows["regime"] == regime]
                    fcst = pd.to_numeric(rows[fc_col], errors="coerce")
                    obs = pd.to_numeric(rows[obs_col], errors="coerce")
                    mask = fcst.notna() & obs.notna()
                    fcst, obs = fcst[mask], obs[mask]
                    min_samples = 75 if kind == "rain" else (200 if parameter == "Wind Gust" else 500)
                    if kind == "rain":
                        wet = (fcst >= 0.1) & (obs >= 0.1)
                        fcst, obs = fcst[wet], obs[wet]
                    if len(fcst) < min_samples:
                        continue
                    if kind == "circular":
                        delta = np.deg2rad(((obs - fcst + 180.0) % 360.0 - 180.0).to_numpy())
                        self.direction_offsets[(model, regime)] = float(
                            np.rad2deg(math.atan2(np.sin(delta).mean(), np.cos(delta).mean()))
                        )
                        continue
                    probabilities = np.linspace(0.0, 1.0, 61)
                    self.tables[(model, parameter, regime)] = (
                        np.maximum.accumulate(np.quantile(fcst.to_numpy(), probabilities)),
                        np.maximum.accumulate(np.quantile(obs.to_numpy(), probabilities)),
                    )

    def apply(
        self,
        value: float,
        model: str,
        parameter: str,
        timestamp: pd.Timestamp,
        *,
        regime_mode: bool,
        conservative_caps: bool,
    ) -> float:
        if pd.isna(value):
            return float("nan")
        regime = _regime(timestamp) if regime_mode else "ALL"
        if parameter == "Wind Dir.":
            offset = self.direction_offsets.get((model, regime), self.direction_offsets.get((model, "ALL")))
            if offset is None:
                return float(value)
            if conservative_caps:
                offset = max(-35.0, min(35.0, offset))
            return float((value + offset) % 360.0)

        # Never manufacture rain from a dry model signal.
        if parameter == "Rainfall" and float(value) < 0.1:
            return float(value)
        table = self.tables.get((model, parameter, regime), self.tables.get((model, parameter, "ALL")))
        if table is None:
            return float(value)
        fc_q, obs_q = table
        mapped = float(np.interp(float(value), fc_q, obs_q))
        if conservative_caps:
            cap = {
                "Temperature": 3.0,
                "Dewpoint": 3.0,
                "Pressure": 4.0,
                "Wind": 6.0,
                "Wind Gust": 8.0,
            }.get(parameter)
            if cap is not None:
                mapped = max(float(value) - cap, min(float(value) + cap, mapped))
            if parameter == "Rainfall":
                mapped = min(mapped, max(1.0, float(value) * 3.0))
        if parameter in {"Wind", "Wind Gust", "Rainfall"}:
            mapped = max(0.0, mapped)
        return mapped


def _load_pairs(conn: sqlite3.Connection) -> pd.DataFrame:
    columns = ["model", "valid_time"] + [column for spec in PAIR_COLUMNS.values() for column in spec[:2]]
    sql = f"SELECT {', '.join(dict.fromkeys(columns))} FROM qm_training_pairs WHERE model IN ({','.join('?' for _ in MODELS)})"
    pairs = pd.read_sql_query(sql, conn, params=MODELS)
    pairs["valid_dt"] = pd.to_datetime(pairs["valid_time"], errors="coerce")
    return pairs.dropna(subset=["valid_dt"])


class AsOfWeightEngine:
    """Fast, strictly historical inverse-error weights for replay cutoffs."""

    def __init__(self, pairs: pd.DataFrame):
        self.series: dict[str, dict[str, tuple[np.ndarray, np.ndarray]]] = {}
        for parameter, (fc_col, obs_col, kind) in PAIR_COLUMNS.items():
            parameter_series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            for model in MODELS:
                rows = pairs.loc[pairs["model"] == model, ["valid_dt", fc_col, obs_col]].copy()
                fcst = pd.to_numeric(rows[fc_col], errors="coerce")
                obs = pd.to_numeric(rows[obs_col], errors="coerce")
                valid = rows["valid_dt"].notna() & fcst.notna() & obs.notna()
                rows = rows.loc[valid].assign(_fcst=fcst.loc[valid], _obs=obs.loc[valid])
                rows = rows.sort_values("valid_dt")
                if kind == "circular":
                    error = np.abs((rows["_fcst"].to_numpy() - rows["_obs"].to_numpy() + 180.0) % 360.0 - 180.0)
                else:
                    error = np.abs(rows["_fcst"].to_numpy() - rows["_obs"].to_numpy())
                # Pandas can retain database timestamps at microsecond
                # resolution. Normalize to nanoseconds to match Timestamp.value
                # and keep future verification rows out of as-of weights.
                times = rows["valid_dt"].to_numpy(dtype="datetime64[ns]").astype("int64")
                parameter_series[model] = (times, np.concatenate(([0.0], np.cumsum(error, dtype=float))))
            self.series[parameter] = parameter_series

    @staticmethod
    def _window_mean(
        series: tuple[np.ndarray, np.ndarray], cutoff: pd.Timestamp, lookback_days: int | None
    ) -> tuple[int, float | None]:
        times, cumulative_error = series
        end = int(np.searchsorted(times, cutoff.value, side="left"))
        start_time = cutoff - pd.Timedelta(days=lookback_days) if lookback_days else None
        start = int(np.searchsorted(times, start_time.value, side="left")) if start_time is not None else 0
        count = end - start
        if count <= 0:
            return 0, None
        return count, float((cumulative_error[end] - cumulative_error[start]) / count)

    def weights(self, cutoff: pd.Timestamp, *, lookback_days: int | None, equal: bool) -> dict[str, dict[str, float]]:
        if equal:
            return {
                parameter: {model: 1.0 / len(MODELS) for model in MODELS}
                for parameter in PAIR_COLUMNS
            }

        weights: dict[str, dict[str, float]] = {}
        for parameter in PAIR_COLUMNS:
            scores: dict[str, float] = {}
            sample_counts: dict[str, int] = {}
            for model in MODELS:
                count, error = self._window_mean(self.series[parameter][model], cutoff, lookback_days)
                sample_counts[model] = count
                if count >= 30 and error is not None:
                    scores[model] = 1.0 / max(error, 0.05)
            raw_total = sum(scores.values())
            raw = {
                model: scores.get(model, 0.0) / raw_total if raw_total else 1.0 / len(MODELS)
                for model in MODELS
            }
            # Shrink sparse historical windows toward equal weights instead of
            # allowing one model to dominate from a handful of matched hours.
            reliability = min(1.0, sum(sample_counts.values()) / (len(MODELS) * 100.0))
            equal_weight = 1.0 / len(MODELS)
            blended = {
                model: reliability * raw[model] + (1.0 - reliability) * equal_weight
                for model in MODELS
            }
            total = sum(blended.values())
            weights[parameter] = {model: blended[model] / total for model in MODELS}
        return weights


def _issue_times(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Official 23/05/11/17Z issuance sequence, including prior-day 23Z."""
    first = start.normalize() - pd.Timedelta(hours=1)
    last = end.normalize() + pd.Timedelta(hours=23)
    candidates = pd.date_range(first, last, freq="h")
    return [timestamp for timestamp in candidates if timestamp.hour in {23, 5, 11, 17}]


def _historical_window(forecasts: pd.DataFrame, issue_utc: pd.Timestamp) -> pd.DataFrame:
    # Stored historical forecast times are WITA. A TAF begins one hour after
    # issue in UTC, which is nine hours after issue on the local WITA timeline.
    local_start = issue_utc + pd.Timedelta(hours=9)
    local_end = local_start + pd.Timedelta(hours=24)
    rows = forecasts[
        (forecasts["forecast_dt"] >= local_start)
        & (forecasts["forecast_dt"] < local_end)
    ].copy()
    return rows


def _build_consensus(
    rows: pd.DataFrame,
    issue_utc: pd.Timestamp,
    weights: dict[str, dict[str, float]],
    prior: HistoricalPrior,
    config: ReplayConfig,
) -> tuple[pd.DataFrame, dict, dict]:
    local_start = issue_utc + pd.Timedelta(hours=9)
    index = pd.date_range(local_start, periods=24, freq="h")
    consensus = pd.DataFrame({"Datetime": index})
    model_data: dict[str, dict[str, pd.Series]] = {"Rainfall": {}}
    qm_rain_data: dict[str, dict[int, float]] = {}

    for label, db_column in FORECAST_COLUMNS.items():
        pivot = rows.pivot(index="forecast_dt", columns="model", values=db_column).reindex(index=index, columns=MODELS)
        if label in PAIR_COLUMNS and config.qm_mode != "none":
            corrected = pivot.copy()
            for model in MODELS:
                corrected[model] = [
                    prior.apply(
                        value,
                        model,
                        label,
                        timestamp,
                        regime_mode=config.qm_mode == "regime",
                        conservative_caps=config.conservative_caps,
                    )
                    for timestamp, value in pivot[model].items()
                ]
            pivot = corrected
        if label == "Wind Dir.":
            consensus[label] = [_weighted_circular(pivot.loc[time], weights.get(label, {})) for time in index]
        elif label == "Visibility":
            consensus[label] = [min(9999.0, _weighted_median(pivot.loc[time], weights.get("Rainfall", {}))) for time in index]
        elif label == "Weather Code":
            consensus[label] = [_weighted_weather_code(pivot.loc[time], weights.get("Rainfall", {})) for time in index]
        else:
            weight_key = label if label in weights else "Rainfall"
            consensus[label] = [_weighted_average(pivot.loc[time], weights.get(weight_key, {})) for time in index]
        if label == "Rainfall":
            for model in MODELS:
                model_data["Rainfall"][model] = pivot[model].reset_index(drop=True)
                qm_rain_data[model] = {
                    position: float(value) if pd.notna(value) else 0.0
                    for position, value in enumerate(pivot[model].to_numpy())
                }

    consensus["Precip Probability"] = [
        100.0 * sum(
            weights.get("Rainfall", {}).get(model, 0.0)
            for model, value in model_data["Rainfall"].items()
            if position < len(value) and pd.notna(value.iloc[position]) and float(value.iloc[position]) >= 0.1
        )
        for position in range(24)
    ]
    consensus["Prob Precip 1.0mm"] = consensus["Precip Probability"]
    # The live TAF generator predates the dashboard label and reads ``Rain``.
    consensus["Rain"] = consensus["Rainfall"]
    consensus["Condition"] = np.where(consensus["Rainfall"].fillna(0.0) >= 1.0, "Rain", "Normal")
    if {"Temperature", "Dewpoint"}.issubset(consensus.columns):
        valid = consensus["Temperature"].notna() & consensus["Dewpoint"].notna()
        consensus.loc[valid, "Dewpoint"] = consensus.loc[valid, ["Temperature", "Dewpoint"]].min(axis=1)
    return consensus, model_data, qm_rain_data


def _load_historical_forecasts(conn: sqlite3.Connection, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    # Add a one-day edge so the final scheduled issuance can produce a full
    # 24-hour TAF validity window.
    params = [
        (start - pd.Timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        (end + pd.Timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
        *MODELS,
    ]
    query = f"""
        SELECT forecast_time, model, {', '.join(FORECAST_COLUMNS.values())}
        FROM openmeteo_forecasts
        WHERE run_init_utc = 'historical_forecast_api'
          AND forecast_time >= ? AND forecast_time < ?
          AND model IN ({','.join('?' for _ in MODELS)})
    """
    frame = pd.read_sql_query(query, conn, params=params)
    frame["forecast_dt"] = pd.to_datetime(frame["forecast_time"], errors="coerce")
    return frame.dropna(subset=["forecast_dt"])


def run_replay(
    db_path: Path,
    output_dir: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    config_names: Iterable[str],
    limit: int | None = None,
) -> dict:
    configs = [CONFIGS[name] for name in config_names]
    output_dir.mkdir(parents=True, exist_ok=True)
    training_cutoff = start.normalize()

    with sqlite3.connect(db_path) as conn:
        pairs = _load_pairs(conn)
        forecasts = _load_historical_forecasts(conn, start, end)
    prior = HistoricalPrior(pairs, training_cutoff)
    weight_engine = AsOfWeightEngine(pairs)
    issues = _issue_times(start, end)
    if limit is not None:
        issues = issues[:limit]

    config_lines: dict[str, list[str]] = {config.name: [] for config in configs}
    metadata_rows: list[dict] = []
    skipped_issuances: list[str] = []
    weights_cache: dict[tuple[pd.Timestamp, str, int | None], dict[str, dict[str, float]]] = {}

    for issue_utc in issues:
        rows = _historical_window(forecasts, issue_utc)
        expected_times = 24 * len(MODELS)
        if len(rows) < expected_times * 0.55:
            skipped_issuances.append(issue_utc.strftime("%Y-%m-%d %H:%M:%S"))
            continue
        asof_cutoff = issue_utc + pd.Timedelta(hours=8)
        for config in configs:
            cache_key = (asof_cutoff, config.weight_mode, config.lookback_days)
            if cache_key not in weights_cache:
                weights_cache[cache_key] = weight_engine.weights(
                    asof_cutoff,
                    lookback_days=config.lookback_days,
                    equal=config.weight_mode == "equal",
                )
            weights = weights_cache[cache_key]
            consensus, model_data, qm_rain = _build_consensus(rows, issue_utc, weights, prior, config)
            taf = generate_tafor(consensus, model_data, qm_rain, weights)
            if not taf or not taf.get("taf_text"):
                continue
            compact_taf = _flatten_taf(taf["taf_text"])
            config_lines[config.name].append(compact_taf)
            metadata_rows.append({
                "config": config.name,
                "issuance_utc": issue_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "valid_start_wita": (issue_utc + pd.Timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
                "taf": compact_taf,
                "weight_mode": config.weight_mode,
                "lookback_days": config.lookback_days or "",
                "qm_mode": config.qm_mode,
                "conservative_caps": config.conservative_caps,
                "weights_json": json.dumps(weights, sort_keys=True),
                "source_label": "continuous_historical_prior_not_lead_aware",
            })

    for config in configs:
        # One line per TAF; no bulletin headers, blank paragraphs, or prose.
        (output_dir / f"{config.name}_tafs.txt").write_text(
            "\n".join(config_lines[config.name]) + ("\n" if config_lines[config.name] else ""),
            encoding="utf-8",
        )
    with (output_dir / "replay_metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metadata_rows[0]) if metadata_rows else ["config"])
        writer.writeheader()
        writer.writerows(metadata_rows)
    manifest = {
        "experiment": "continuous_historical_prior_taf_replay",
        "source_label": "continuous_historical_prior_not_lead_aware",
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "qm_training_cutoff": training_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        "weight_policy": "computed as of each issuance from earlier continuous forecast-observation pairs only",
        "human_taf_comparison": "pending official human TAF archive",
        "metar_comparison": "pending METAR archive; AWOS verification can be added separately",
        "models": MODELS,
        "configs": [asdict(config) for config in configs],
        "issue_count_attempted": len(issues),
        "issue_count_completed": len(issues) - len(skipped_issuances),
        "skipped_issuances_utc": skipped_issuances,
        "taf_count_by_config": {name: len(lines) for name, lines in config_lines.items()},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=root / "wawp_forecasts.db")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--output", type=Path, default=root / "artifacts" / "taf_replay_2026_h1")
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--limit", type=int, default=None, help="Limit issuances for a quick smoke run.")
    args = parser.parse_args()
    config_names = [name.strip() for name in args.configs.split(",") if name.strip()]
    unknown = sorted(set(config_names) - set(CONFIGS))
    if unknown:
        raise SystemExit(f"Unknown configuration(s): {', '.join(unknown)}")
    manifest = run_replay(
        args.db,
        args.output,
        pd.Timestamp(args.start),
        pd.Timestamp(args.end),
        config_names,
        args.limit,
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
