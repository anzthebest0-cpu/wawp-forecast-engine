"""Forensic signal ledger for the January-May 2026 TAF replay experiment.

This diagnostic does not alter operational guidance.  It reconstructs the
continuous-history consensus behind archived replay TAFs that contain rain or
thunderstorm weather, then joins each forecast event hour to the canonical
METAR evidence used by the event-window verifier.

The ledger answers a deliberately narrow audit question: which rain trigger,
ensemble vote, or thunderstorm proxy path created each warning, and did that
warning match an observed event within the allowed +/-2 hour displacement
window?
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.taf_core import RainConfig
from src.taf_replay_event_window_verification import (
    TS_RE,
    _forecast_event,
    _metar_by_time,
    _observed_event,
    _parse_utc,
    _read_csv,
)
from src.legacy_taf_verification import parse_taf, repair_validity_to_24h
from src.taf_replay_experiment import (
    CONFIGS,
    MODELS,
    AsOfWeightEngine,
    HistoricalPrior,
    _build_consensus,
    _historical_window,
    _load_historical_forecasts,
    _load_pairs,
)


UTC = timezone.utc
WINDOW_HOURS = 2
LEDGER_EVENTS = ("rain_any", "thunderstorm")


def _event_taf(text: str) -> bool:
    """Whether archived TAF text contains a rain or thunderstorm token."""
    return bool("RA" in str(text or "").upper() or TS_RE.search(str(text or "").upper()))


def _match_forecasts(
    rows: list[dict[str, Any]], event: str, window_hours: int = WINDOW_HOURS
) -> dict[int, int]:
    """Return one-to-one forecast-index to observed-index matches.

    This mirrors the verifier's closest-unused matching rule, but retains the
    individual pairing needed for a forensic forecast-event ledger.
    """
    observed_indices = [index for index, row in enumerate(rows) if row["observed_events"][event]]
    forecast_indices = [index for index, row in enumerate(rows) if row["forecast_events"][event]]
    used_forecasts: set[int] = set()
    matches: dict[int, int] = {}
    for observed_index in observed_indices:
        observed_time = rows[observed_index]["valid_time"]
        candidates = [
            forecast_index
            for forecast_index in forecast_indices
            if forecast_index not in used_forecasts
            and abs((rows[forecast_index]["valid_time"] - observed_time).total_seconds())
            <= window_hours * 3600
        ]
        if not candidates:
            continue
        selected = min(
            candidates,
            key=lambda forecast_index: (
                abs((rows[forecast_index]["valid_time"] - observed_time).total_seconds()),
                rows[forecast_index]["valid_time"],
            ),
        )
        used_forecasts.add(selected)
        matches[selected] = observed_index
    return matches


def _rain_trigger_hours(consensus: pd.DataFrame) -> list[dict[str, Any]]:
    """Expose the Phase-0 amount/probability/bridge rain signal per hour."""
    raw: list[bool] = []
    reasons: list[str] = []
    for _, row in consensus.iterrows():
        amount = float(row.get("Rainfall", 0.0) or 0.0)
        probability = float(row.get("Precip Probability", 0.0) or 0.0)
        amount_trigger = amount >= RainConfig.CONSENSUS_THR
        probability_trigger = probability >= 40.0
        raw.append(amount_trigger or probability_trigger)
        if amount_trigger and probability_trigger:
            reasons.append("amount_and_probability")
        elif amount_trigger:
            reasons.append("consensus_amount")
        elif probability_trigger:
            reasons.append("wet_model_probability")
        else:
            reasons.append("no_direct_signal")

    bridged = raw[:]
    position = 0
    while position < len(raw):
        if raw[position]:
            position += 1
            continue
        gap_start = position
        while position < len(raw) and not raw[position]:
            position += 1
        gap_end = position
        gap_is_bridgeable = (
            gap_end - gap_start <= 2
            and gap_start > 0
            and gap_end < len(raw)
            and raw[gap_start - 1]
            and raw[gap_end]
            and all(
                float(consensus.iloc[index].get("Rainfall", 0.0) or 0.0)
                >= RainConfig.BRIDGE_THR
                for index in range(gap_start, gap_end)
            )
        )
        if gap_is_bridgeable:
            for index in range(gap_start, gap_end):
                bridged[index] = True
                reasons[index] = "bridged_consensus_gap"

    return [
        {
            "rain_signal_active": bool(bridged[index]),
            "rain_trigger_reason": reasons[index],
            "amount_threshold_met": bool(float(consensus.iloc[index].get("Rainfall", 0.0) or 0.0) >= RainConfig.CONSENSUS_THR),
            "probability_threshold_met": bool(float(consensus.iloc[index].get("Precip Probability", 0.0) or 0.0) >= 40.0),
        }
        for index in range(len(consensus))
    ]


def _rain_model_diagnostics(
    model_data: dict[str, dict[str, pd.Series]],
    weights: dict[str, dict[str, float]],
    hour: int,
) -> dict[str, Any]:
    """Mirror the weighted vote and spread penalty used by ``rain_agreement``."""
    values: list[tuple[str, float, float]] = []
    rain_weights = weights.get("Rainfall", {})
    for model, series in model_data.get("Rainfall", {}).items():
        if hour >= len(series) or pd.isna(series.iloc[hour]):
            continue
        value = float(series.iloc[hour])
        values.append((model, value, max(0.0, float(rain_weights.get(model, 0.0)))))
    if not values:
        return {
            "rain_model_count": 0,
            "rain_models_wet": 0,
            "rain_weighted_vote_pct": 0.0,
            "rain_weighted_agreement_pct": 0.0,
            "rain_spread_mm": None,
            "rain_spread_penalty": None,
            "wet_models": "",
        }
    total_weight = sum(weight for _, _, weight in values)
    wet = [(model, value, weight) for model, value, weight in values if value >= RainConfig.VOTE_THR]
    if total_weight <= 0:
        vote = len(wet) / len(values)
    else:
        vote = sum(weight for _, _, weight in wet) / total_weight
    spread = float(np.std([value for _, value, _ in values])) if len(values) >= 2 else 0.0
    penalty = 1.0 / (1.0 + RainConfig.SPREAD_FACTOR * spread) if vote > 0 and len(values) >= 2 else 1.0
    return {
        "rain_model_count": len(values),
        "rain_models_wet": len(wet),
        "rain_weighted_vote_pct": round(vote * 100.0, 3),
        "rain_weighted_agreement_pct": round(vote * penalty * 100.0, 3),
        "rain_spread_mm": round(spread, 3),
        "rain_spread_penalty": round(penalty, 4),
        "wet_models": ",".join(model for model, _, _ in wet),
    }


def _ts_proxy_reason(row: pd.Series) -> str:
    """State the first TSRA branch that applies to the consensus hour."""
    rain = float(row.get("Rainfall", 0.0) or 0.0)
    if rain <= 0.1:
        return "not_rainy_enough_for_ts_proxy"
    weather_code = row.get("Weather Code")
    try:
        if int(round(float(weather_code))) in {95, 96, 99}:
            return "weather_code_thunderstorm"
    except (TypeError, ValueError):
        pass
    try:
        month = int(pd.Timestamp(row["Datetime"]).month)
        cape_threshold = 500 if month in {11, 12, 1, 2, 3, 4} else 800
        cape = float(row.get("CAPE"))
        lifted_index = float(row.get("Lifted Index"))
        cin = float(row.get("Convective Inhibition"))
        local_hour = int(pd.Timestamp(row["Datetime"]).hour)
        if cape >= cape_threshold and lifted_index <= -2.0 and cin <= 100.0 and (rain >= 5.0 or 11 <= local_hour <= 21):
            return "cape_li_cin_convective_window"
    except (TypeError, ValueError):
        pass
    try:
        local_hour = int(pd.Timestamp(row["Datetime"]).hour)
        if rain >= 7.5 and 11 <= local_hour <= 21 and float(row.get("Humidity")) >= 85.0:
            return "heavy_rain_humidity_convective_window"
    except (TypeError, ValueError):
        pass
    return "no_ts_proxy_trigger"


def _causal_source_time(
    taf_text: str,
    issuance_utc: datetime,
    valid_time: datetime,
    event: str,
) -> tuple[datetime, str]:
    """Trace an active event back to the group that introduced it.

    A completed BECMG is persistent. Its later active hours may be dry in the
    reconstructed consensus, so the causal hour is group completion instead.
    """
    period = (issuance_utc + timedelta(hours=1)).strftime("%Y-%m")
    parsed, _, _ = repair_validity_to_24h(parse_taf(taf_text, period))
    for group in parsed.groups:
        if (
            group.kind == "TEMPO"
            and group.start <= valid_time < group.end
            and _forecast_event(group.remainder, event)
        ):
            return group.start, "tempo_window_start"
    completed = [
        group
        for group in parsed.groups
        if group.kind == "BECMG" and group.end <= valid_time and _forecast_event(group.remainder, event)
    ]
    if completed:
        return completed[-1].end, "becmg_establishment"
    if _forecast_event(parsed.base.weather, event):
        return parsed.valid_start, "base_state"
    return valid_time, "active_hour_fallback"


def _common_starts(root: Path) -> set[str]:
    reports = root / "VERIFICATION_REPORTS"
    replay_rows = _read_csv(reports / "taf_replay_multiconfig_verification_2026" / "taf_replay_multiconfig_issuance.csv")
    original_rows = _read_csv(reports / "original_taf_structured_baseline_2026" / "historical_preconfigured_taf_issuance.csv")
    replay_starts = {
        (_parse_utc(row["issuance_utc"]) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        for row in replay_rows
    }
    return replay_starts & {row["taf_valid_start_utc"] for row in original_rows}


def _prepare_hourly_rows(
    hourly_path: Path,
    metar: dict[datetime, dict[str, Any]],
    common_starts: set[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in _read_csv(hourly_path):
        issue = _parse_utc(row["issuance_utc"])
        valid_start = (issue + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        if valid_start not in common_starts:
            continue
        valid_time = _parse_utc(row["valid_time_utc"])
        observed_row = metar.get(valid_time)
        if observed_row is None:
            continue
        observed = {
            "rain_any": bool(observed_row["event_rain_eligible"] and _observed_event(observed_row, "rain_any")),
            "rain_heavy": bool(observed_row["event_rain_eligible"] and _observed_event(observed_row, "rain_heavy")),
            "thunderstorm": _observed_event(observed_row, "thunderstorm"),
        }
        grouped[(row["configuration"], row["issuance_utc"])].append({
            **row,
            "valid_time": valid_time,
            "observed_events": observed,
            "forecast_events": {event: _forecast_event(row.get("forecast_weather"), event) for event in LEDGER_EVENTS},
            "metar_rainfall_tenths_mm": observed_row.get("rainfall_raw_tenths_mm"),
        })
    for rows in grouped.values():
        rows.sort(key=lambda row: row["valid_time"])
    return grouped


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# WAWP Replay Event Signal Ledger",
        "",
        "## Scope",
        "",
        f"This diagnostic examines the {summary['event_taf_count']:,} archived replay TAFs that contain RA or TS wording over {summary['common_valid_start_count']} shared validity starts. It does not change operational calculations or promote any configuration.",
        "",
        "Each ledger row is a forecast hour with RA and/or TS in the parsed archived TAF. The row traces the event back to its base state, TEMPO start, or BECMG establishment, then shows the reconstructed continuous-history consensus inputs and whether the forecast event was paired one-to-one with a quality-eligible observed event inside the same 24-hour TAF validity period at plus/minus 2 hours.",
        "",
        "## Trigger Summary",
        "",
        "| Configuration | Event | TAF state | Source | Rain trigger | Forecast hours | Matched | False alarms | Mean rain | Mean wet vote | Mean adjusted agreement |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["trigger_summary"]:
        lines.append(
            f"| `{row['configuration']}` | {row['event']} | {row['taf_change_group']} | {row['event_source_relation']} | {row['rain_trigger_reason']} | "
            f"{row['forecast_hours']} | {row['matched']} | {row['false_alarms']} | {row['mean_consensus_rain_mm']:.2f} | "
            f"{row['mean_weighted_vote_pct']:.1f}% | {row['mean_adjusted_agreement_pct']:.1f}% |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- `consensus_amount` means the weighted consensus reached the 1.0 mm hourly threshold. `wet_model_probability` means the consensus amount stayed below 1.0 mm but at least 40% of model weight was wet at 0.1 mm or more. `bridged_consensus_gap` means a short 0.2 mm or greater gap was filled between two rainy hours. `becmg_establishment` avoids falsely attributing a persistent BECMG rain state to later, already-dry consensus hours.",
        "- `rain_weighted_vote_pct` is the model-weight share at or above 1.0 mm. `rain_weighted_agreement_pct` applies the operational ensemble-spread penalty, so a split ensemble receives less confidence than an equally weighted coherent ensemble.",
        "- `matched_pm2h` is timing-tolerant evidence, not automatic approval for a broad TAF group. `false_alarm_pm2h` identifies a forecast event hour that could not be uniquely paired to an observed event in that TAF validity period.",
        "- Thunderstorm rows retain the same rain trigger evidence and add `ts_proxy_reason`. A TS proxy is diagnostic evidence only; it does not establish verified convective skill.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    root: Path,
    db_path: Path,
    output_dir: Path,
    max_event_tafs: int | None = None,
) -> dict[str, Any]:
    reports = root / "VERIFICATION_REPORTS"
    replay_dir = reports / "taf_replay_multiconfig_verification_2026"
    replay_metadata_path = reports / "taf_replay_multiconfig_2026" / "replay_metadata.csv"
    if not replay_metadata_path.exists():
        replay_metadata_path = root / "meteologix-wawp-main" / "artifacts" / "taf_replay_2026_h1" / "replay_metadata.csv"
    if not replay_metadata_path.exists():
        raise FileNotFoundError(f"Replay metadata not found: {replay_metadata_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    common_starts = _common_starts(root)
    metar = _metar_by_time(reports / "metar_standalone" / "canonical")
    hourly_by_key = _prepare_hourly_rows(
        replay_dir / "taf_replay_multiconfig_hourly.csv", metar, common_starts
    )
    metadata = {
        (row["config"], row["issuance_utc"]): row
        for row in _read_csv(replay_metadata_path)
        if _event_taf(row.get("taf", ""))
        and (_parse_utc(row["issuance_utc"]) + timedelta(hours=1)).isoformat().replace("+00:00", "Z") in common_starts
    }
    event_keys = sorted(set(hourly_by_key) & set(metadata))
    if max_event_tafs is not None:
        event_keys = event_keys[:max_event_tafs]

    with sqlite3.connect(db_path) as conn:
        pairs = _load_pairs(conn)
        forecasts = _load_historical_forecasts(
            conn,
            pd.Timestamp("2026-01-01"),
            pd.Timestamp("2026-05-31"),
        )
    prior = HistoricalPrior(pairs, pd.Timestamp("2026-01-01"))
    weight_engine = AsOfWeightEngine(pairs)
    weights_cache: dict[tuple[pd.Timestamp, str, int | None], dict[str, dict[str, float]]] = {}
    ledger_rows: list[dict[str, Any]] = []
    reconstruction_skips: list[dict[str, str]] = []

    for configuration, issuance_value in event_keys:
        metadata_row = metadata[(configuration, issuance_value)]
        config = CONFIGS[configuration]
        issue_utc = _parse_utc(issuance_value).replace(tzinfo=None)
        rows = _historical_window(forecasts, pd.Timestamp(issue_utc))
        if len(rows) < 24 * len(MODELS) * 0.55:
            reconstruction_skips.append({"configuration": configuration, "issuance_utc": issuance_value, "reason": "insufficient_historical_model_rows"})
            continue
        asof_cutoff = pd.Timestamp(issue_utc) + pd.Timedelta(hours=8)
        cache_key = (asof_cutoff, config.weight_mode, config.lookback_days)
        if cache_key not in weights_cache:
            weights_cache[cache_key] = weight_engine.weights(
                asof_cutoff,
                lookback_days=config.lookback_days,
                equal=config.weight_mode == "equal",
            )
        weights = weights_cache[cache_key]
        consensus, model_data, _ = _build_consensus(rows, pd.Timestamp(issue_utc), weights, prior, config)
        triggers = _rain_trigger_hours(consensus)
        valid_index = {
            (pd.Timestamp(row["Datetime"]) - pd.Timedelta(hours=8)).to_pydatetime().replace(tzinfo=UTC): position
            for position, (_, row) in enumerate(consensus.iterrows())
        }
        issuance_rows = hourly_by_key[(configuration, issuance_value)]
        matched = {event: _match_forecasts(issuance_rows, event) for event in LEDGER_EVENTS}
        for hour_index, hourly in enumerate(issuance_rows):
            valid_time = hourly["valid_time"]
            consensus_position = valid_index.get(valid_time)
            if consensus_position is None:
                continue
            for event in LEDGER_EVENTS:
                if not hourly["forecast_events"][event]:
                    continue
                source_time, source_relation = _causal_source_time(
                    metadata_row.get("taf") or hourly.get("taf") or "",
                    _parse_utc(issuance_value),
                    valid_time,
                    event,
                )
                source_position = valid_index.get(source_time, consensus_position)
                consensus_row = consensus.iloc[source_position]
                model_diag = _rain_model_diagnostics(model_data, weights, source_position)
                paired_observation = matched[event].get(hour_index)
                observed_time = issuance_rows[paired_observation]["valid_time"] if paired_observation is not None else None
                event_row = {
                    "configuration": configuration,
                    "issuance_utc": issuance_value,
                    "valid_time_utc": valid_time.isoformat().replace("+00:00", "Z"),
                    "event": event,
                    "taf_change_group": hourly.get("change_group") or "unknown",
                    "event_source_relation": source_relation,
                    "event_source_time_utc": source_time.isoformat().replace("+00:00", "Z"),
                    "forecast_weather": hourly.get("forecast_weather") or "",
                    "archived_taf": metadata_row.get("taf") or hourly.get("taf") or "",
                    "observed_event_at_valid_hour": int(hourly["observed_events"][event]),
                    "event_match_status_pm2h": "matched_pm2h" if paired_observation is not None else "false_alarm_pm2h",
                    "matched_observed_time_utc": observed_time.isoformat().replace("+00:00", "Z") if observed_time else "",
                    "timing_offset_h": round((valid_time - observed_time).total_seconds() / 3600.0, 2) if observed_time else None,
                    "metar_text": hourly.get("metar_text") or "",
                    "metar_rainfall_tenths_mm": hourly.get("metar_rainfall_tenths_mm"),
                    "consensus_rain_mm": round(float(consensus_row.get("Rainfall", 0.0) or 0.0), 3),
                    "precip_probability_pct": round(float(consensus_row.get("Precip Probability", 0.0) or 0.0), 3),
                    "cape_jkg": _rounded_or_none(consensus_row.get("CAPE")),
                    "lifted_index": _rounded_or_none(consensus_row.get("Lifted Index")),
                    "cin_jkg": _rounded_or_none(consensus_row.get("Convective Inhibition")),
                    "weather_code": _rounded_or_none(consensus_row.get("Weather Code")),
                    "relative_humidity_pct": _rounded_or_none(consensus_row.get("Humidity")),
                    "ts_proxy_reason": _ts_proxy_reason(consensus_row),
                    **triggers[source_position],
                    **model_diag,
                }
                ledger_rows.append(event_row)

    summary_rows = []
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ledger_rows:
        grouped[(row["configuration"], row["event"], row["taf_change_group"], row["event_source_relation"], row["rain_trigger_reason"])].append(row)
    for key, values in sorted(grouped.items()):
        forecast_hours = len(values)
        summary_rows.append({
            "configuration": key[0],
            "event": key[1],
            "taf_change_group": key[2],
            "event_source_relation": key[3],
            "rain_trigger_reason": key[4],
            "forecast_hours": forecast_hours,
            "matched": sum(row["event_match_status_pm2h"] == "matched_pm2h" for row in values),
            "false_alarms": sum(row["event_match_status_pm2h"] == "false_alarm_pm2h" for row in values),
            "mean_consensus_rain_mm": round(sum(row["consensus_rain_mm"] for row in values) / forecast_hours, 3),
            "mean_weighted_vote_pct": round(sum(row["rain_weighted_vote_pct"] for row in values) / forecast_hours, 3),
            "mean_adjusted_agreement_pct": round(sum(row["rain_weighted_agreement_pct"] for row in values) / forecast_hours, 3),
        })

    summary = {
        "scope": "experimental historical TAF replay forensic ledger; no operational pipeline changes",
        "common_valid_start_count": len(common_starts),
        "event_taf_count": len(event_keys),
        "ledger_event_hour_count": len(ledger_rows),
        "reconstruction_skip_count": len(reconstruction_skips),
        "match_window_hours": WINDOW_HOURS,
        "rain_thresholds": {
            "consensus_amount_mm": RainConfig.CONSENSUS_THR,
            "wet_model_vote_mm": RainConfig.VOTE_THR,
            "wet_model_probability_pct": 40.0,
            "bridge_amount_mm": RainConfig.BRIDGE_THR,
        },
        "trigger_summary": summary_rows,
        "reconstruction_skips": reconstruction_skips,
    }
    ledger_fields = [
        "configuration", "issuance_utc", "valid_time_utc", "event", "taf_change_group", "event_source_relation", "event_source_time_utc", "forecast_weather", "archived_taf",
        "observed_event_at_valid_hour", "event_match_status_pm2h", "matched_observed_time_utc", "timing_offset_h", "metar_text", "metar_rainfall_tenths_mm",
        "consensus_rain_mm", "precip_probability_pct", "amount_threshold_met", "probability_threshold_met", "rain_signal_active", "rain_trigger_reason",
        "rain_model_count", "rain_models_wet", "rain_weighted_vote_pct", "rain_weighted_agreement_pct", "rain_spread_mm", "rain_spread_penalty", "wet_models",
        "cape_jkg", "lifted_index", "cin_jkg", "weather_code", "relative_humidity_pct", "ts_proxy_reason",
    ]
    _write_csv(output_dir / "taf_replay_event_signal_ledger.csv", ledger_rows, ledger_fields)
    _write_csv(
        output_dir / "taf_replay_event_signal_summary.csv",
        summary_rows,
        ["configuration", "event", "taf_change_group", "event_source_relation", "rain_trigger_reason", "forecast_hours", "matched", "false_alarms", "mean_consensus_rain_mm", "mean_weighted_vote_pct", "mean_adjusted_agreement_pct"],
    )
    (output_dir / "taf_replay_event_signal_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_markdown(output_dir / "TAF_REPLAY_EVENT_SIGNAL_LEDGER.md", summary)
    return summary


def _rounded_or_none(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL"))
    parser.add_argument("--db", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\meteologix-wawp-main\wawp_forecasts.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_replay_event_ledger_2026"),
    )
    parser.add_argument(
        "--max-event-tafs",
        type=int,
        default=None,
        help="Reconstruct only the first N event-bearing TAFs for a smoke check.",
    )
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.db, args.output_dir, args.max_event_tafs), indent=2))


if __name__ == "__main__":
    main()
