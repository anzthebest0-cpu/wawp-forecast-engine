"""Verify replay TAF rain/TS timing and visibility/cloud regimes.

This is an experiment-only companion to ``taf_replay_multiconfig_verification``.
It answers questions that strict hourly category scoring cannot answer fairly:

* did a TAF warn for rain or TS within 0, 1, or 2 hours of an observation?
* does a forecasted RA group cover heavy observed rain, without claiming amount skill?
* are visibility and cloud errors concentrated in dry, rainy, or dawn fog-proxy hours?

The verifier uses the same canonical METAR archive, quality gates, and structured
TAF state interpretation as the first replay evaluation. It never writes to the
operational database or changes the live guidance path.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.legacy_taf_verification import (
    _is_true,
    _quality_rain_provenance,
    _quality_row_is_eligible,
    _read_metar_rows,
)


UTC = timezone.utc
PERIODS = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05")
EVENTS = ("rain_any", "rain_heavy", "thunderstorm")
WINDOWS = (0, 1, 2)
REGIMES = ("dry", "rain", "dawn_fog_proxy")
TS_RE = re.compile(r"\b(?:\+|-)?TS(?:RA|GR|GS|SN)?\b")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Replay metadata deliberately stores UTC as a naive SQL-style timestamp.
    # Treat it as UTC rather than allowing the host locale to reinterpret it.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _forecast_event(weather: Any, event: str) -> bool:
    token = str(weather or "").upper()
    if event in {"rain_any", "rain_heavy"}:
        return "RA" in token
    return bool(TS_RE.search(token))


def _observed_event(row: dict[str, Any], event: str) -> bool:
    rainfall = _as_int(row.get("rainfall_raw_tenths_mm")) or 0
    if event == "rain_any":
        return rainfall >= 1
    if event == "rain_heavy":
        return rainfall > 40
    return bool(TS_RE.search(str(row.get("metar_text") or "").upper()))


def _matching_counts(
    rows: list[dict[str, Any]],
    event: str,
    window_hours: int,
) -> tuple[dict[str, int], list[float]]:
    """One-to-one event matching within a TAF validity window.

    A single long TEMPO group must not be credited for multiple distinct events,
    and a forecast event must not be used to erase several misses. Matching the
    closest unused opposite event keeps POD/FAR/CSI interpretable.
    """
    observed_indices = [index for index, row in enumerate(rows) if row["observed_events"][event]]
    forecast_indices = [index for index, row in enumerate(rows) if row["forecast_events"][event]]
    used_forecasts: set[int] = set()
    hits = 0
    offsets: list[float] = []
    for observed_index in observed_indices:
        observed_time = rows[observed_index]["valid_time"]
        candidates = [
            forecast_index
            for forecast_index in forecast_indices
            if forecast_index not in used_forecasts
            and abs((rows[forecast_index]["valid_time"] - observed_time).total_seconds()) <= window_hours * 3600
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
        hits += 1
        offsets.append((rows[selected]["valid_time"] - observed_time).total_seconds() / 3600.0)

    misses = len(observed_indices) - hits
    false_alarms = len(forecast_indices) - hits
    sample_size = len(rows)
    correct_negatives = max(0, sample_size - hits - misses - false_alarms)
    return {
        "sample_size": sample_size,
        "observed_events": len(observed_indices),
        "forecast_events": len(forecast_indices),
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_negatives": correct_negatives,
    }, offsets


def _event_scores(counts: dict[str, int], offsets: Iterable[float]) -> dict[str, float | int | None]:
    hits = counts["hits"]
    misses = counts["misses"]
    false_alarms = counts["false_alarms"]
    correct_negatives = counts["correct_negatives"]
    total = counts["sample_size"]
    pod = hits / (hits + misses) if hits + misses else None
    far = false_alarms / (hits + false_alarms) if hits + false_alarms else None
    csi = hits / (hits + misses + false_alarms) if hits + misses + false_alarms else None
    expected = (
        ((hits + misses) * (hits + false_alarms))
        + ((correct_negatives + misses) * (correct_negatives + false_alarms))
    ) / total if total else 0.0
    denominator = total - expected
    hss = (hits + correct_negatives - expected) / denominator if denominator > 0 else None
    values = list(offsets)
    return {
        **counts,
        "POD": round(pod, 4) if pod is not None else None,
        "FAR": round(far, 4) if far is not None else None,
        "CSI": round(csi, 4) if csi is not None else None,
        "HSS": round(hss, 4) if hss is not None else None,
        "mean_abs_timing_error_h": round(sum(abs(value) for value in values) / len(values), 3) if values else None,
        "mean_signed_timing_error_h": round(sum(values) / len(values), 3) if values else None,
    }


def _aggregate_event_rows(rows: list[dict[str, Any]], event: str, window_hours: int) -> dict[str, Any]:
    totals = defaultdict(int)
    offsets: list[float] = []
    by_issuance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_issuance[row["issuance_key"]].append(row)
    for issuance_rows in by_issuance.values():
        issuance_rows.sort(key=lambda row: row["valid_time"])
        counts, issuance_offsets = _matching_counts(issuance_rows, event, window_hours)
        for key, value in counts.items():
            totals[key] += value
        offsets.extend(issuance_offsets)
    return _event_scores(dict(totals), offsets)


def _regime_for(row: dict[str, Any]) -> set[str]:
    regimes: set[str] = set()
    if not row["observed_events"]["rain_any"]:
        regimes.add("dry")
    else:
        regimes.add("rain")
    local_hour = (row["valid_time"] + timedelta(hours=8)).hour
    observed_vis = _as_int(row.get("quality_observed_visibility_m"))
    observed_weather = str(row.get("quality_observed_weather") or "").upper()
    is_dawn_proxy = 4 <= local_hour <= 8 and (
        (observed_vis is not None and observed_vis <= 5000) or "BR" in observed_weather or "FG" in observed_weather
    )
    if is_dawn_proxy:
        regimes.add("dawn_fog_proxy")
    return regimes


def _regime_scores(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, int | float | None]]]:
    result: dict[str, dict[str, dict[str, int | float | None]]] = {}
    for regime in REGIMES:
        relevant = [row for row in rows if regime in _regime_for(row)]
        result[regime] = {}
        for metric in ("visibility", "cloud_amount", "cloud_base"):
            values = [
                _as_int(row.get(f"quality_gated_{metric}"))
                for row in relevant
                if _as_int(row.get(f"quality_gated_{metric}")) is not None
            ]
            hits = sum(values)
            eligible = len(values)
            result[regime][metric] = {
                "eligible_hours": eligible,
                "hits": hits,
                "score_percent": round(100.0 * hits / eligible, 2) if eligible else None,
            }
    return result


def _metar_by_time(metar_dir: Path) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    for period in PERIODS:
        for row in _read_metar_rows(metar_dir / f"metar_wawp_{period}.csv"):
            if not _quality_row_is_eligible(row):
                continue
            if row["observed_at"] is None:
                continue
            rain_eligible, _ = _quality_rain_provenance(row)
            row["event_rain_eligible"] = rain_eligible
            result[row["observed_at"]] = row
    return result


def _replay_rows(
    hourly_path: Path,
    metar_by_time: dict[datetime, dict[str, Any]],
    common_starts: set[str],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_csv(hourly_path):
        issuance = _parse_utc(row["issuance_utc"])
        valid_start = (issuance + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        if valid_start not in common_starts:
            continue
        valid_time = _parse_utc(row["valid_time_utc"])
        metar = metar_by_time.get(valid_time)
        if metar is None:
            continue
        observed = {
            "rain_any": bool(metar["event_rain_eligible"] and _observed_event(metar, "rain_any")),
            "rain_heavy": bool(metar["event_rain_eligible"] and _observed_event(metar, "rain_heavy")),
            "thunderstorm": _observed_event(metar, "thunderstorm"),
        }
        result[row["configuration"]].append({
            **row,
            "issuance_key": f"{row['configuration']}|{row['issuance_utc']}",
            "valid_time": valid_time,
            "forecast_events": {event: _forecast_event(row.get("forecast_weather"), event) for event in EVENTS},
            "observed_events": observed,
        })
    return result


def _original_rows(
    hourly_path: Path,
    issuance_path: Path,
    metar_by_time: dict[datetime, dict[str, Any]],
    common_starts: set[str],
) -> list[dict[str, Any]]:
    selected: dict[str, tuple[str, str]] = {}
    for row in _read_csv(issuance_path):
        valid_start = row["taf_valid_start_utc"]
        if valid_start in common_starts and valid_start not in selected:
            selected[valid_start] = (row["source_taf_row"], row["taf_issue_utc"])

    result = []
    for row in _read_csv(hourly_path):
        valid_start = row["taf_valid_start_utc"]
        selection = selected.get(valid_start)
        if selection is None or selection != (row["source_taf_row"], row["taf_issue_utc"]):
            continue
        valid_time = _parse_utc(row["valid_time_utc"])
        metar = metar_by_time.get(valid_time)
        if metar is None:
            continue
        observed = {
            "rain_any": bool(metar["event_rain_eligible"] and _observed_event(metar, "rain_any")),
            "rain_heavy": bool(metar["event_rain_eligible"] and _observed_event(metar, "rain_heavy")),
            "thunderstorm": _observed_event(metar, "thunderstorm"),
        }
        result.append({
            **row,
            "issuance_key": f"original|{row['taf_issue_utc']}",
            "valid_time": valid_time,
            "forecast_events": {event: _forecast_event(row.get("forecast_weather"), event) for event in EVENTS},
            "observed_events": observed,
        })
    return result


def _as_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100.0:.1f}%"


def _event_status(value: dict[str, Any]) -> str:
    events = int(value["observed_events"])
    if events == 0:
        return "not assessable: no observed events"
    if events < 10:
        return "low sample: fewer than 10 observed events"
    return "interpretable with caution"


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# WAWP Replay Event-Window and Regime Verification",
        "",
        "## Scope",
        "",
        f"This experiment compares the eight replay configurations and the original TAF baseline over {summary['common_valid_start_count']} shared TAF valid starts. It uses canonical January-May 2026 METAR evidence and does not change the operational pipeline.",
        "",
        "Rain and TS are matched one-to-one inside each 24-hour TAF validity period. A forecast warning can match an observed event at the exact hour, or within plus/minus 1 or 2 hours. This prevents one long TEMPO group from receiving credit for multiple events.",
        "",
        "Rain-any means a quality-eligible HUJAN value of at least 0.1 mm. Heavy rain means greater than 4.0 mm. The latter measures warning coverage only: TAF text carries a weather category, not a rain amount.",
        "",
        "## Event-window Results",
        "",
    ]
    for event in EVENTS:
        lines.extend([
            f"### {event.replace('_', ' ').title()}",
            "",
            "| Source | Window | Obs | Fcst | POD | FAR | CSI | HSS | Timing MAE | Signed timing | Status |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ])
        for source, metrics in summary["event_metrics"].items():
            for window in WINDOWS:
                value = metrics[event][f"pm{window}h"]
                lines.append(
                    f"| {source} | +/-{window} h | {value['observed_events']} | {value['forecast_events']} | "
                    f"{_as_percent(value['POD'])} | {_as_percent(value['FAR'])} | {_as_percent(value['CSI'])} | "
                    f"{_as_percent(value['HSS'])} | {value['mean_abs_timing_error_h'] if value['mean_abs_timing_error_h'] is not None else 'n/a'} h | "
                    f"{value['mean_signed_timing_error_h'] if value['mean_signed_timing_error_h'] is not None else 'n/a'} h | {_event_status(value)} |"
                )
        lines.append("")
    lines.extend([
        "## Visibility and Cloud Regimes",
        "",
        "`dry` is a quality-eligible hour without measurable rain. `rain` has quality-eligible measurable rain. `dawn_fog_proxy` is 04-08 WITA with either visibility at or below 5 km or BR/FG reported; it is a diagnostic proxy, not a confirmed fog classification.",
        "",
    ])
    for regime in REGIMES:
        lines.extend([
            f"### {regime}",
            "",
            "| Source | Visibility | Cloud amount | Cloud base |",
            "| --- | ---: | ---: | ---: |",
        ])
        for source, metrics in summary["regime_metrics"].items():
            scores = metrics[regime]
            cells = []
            for metric in ("visibility", "cloud_amount", "cloud_base"):
                value = scores[metric]
                cells.append("n/a" if value["score_percent"] is None else f"{value['score_percent']:.2f}% ({value['eligible_hours']} h)")
            lines.append(f"| {source} | {cells[0]} | {cells[1]} | {cells[2]} |")
        lines.append("")
    lines.extend([
        "## Interpretation Guardrails",
        "",
        "- These are repeated TAF-validity checks, not independent station-event counts. They compare the same issuance schedules fairly, but should not be used as climatological event frequencies.",
        "- Original TAFs can include human judgement and amendments. This is a reference comparison, not proof that any replay should replace official forecaster decisions.",
        "- A better POD at plus/minus 2 h is evidence of timing displacement, not permission to issue a broad rain or TS group. FAR and strict-hour results remain safety controls.",
        "- This archive has no quality-eligible hourly rainfall above 4.0 mm for the assessed Jan-May period. Heavy-rain verification is therefore not assessable, not a zero-skill result.",
        "- Do not promote a QM configuration from these outcomes alone. The experiment has no true lead-aware archive provenance.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    root: Path,
    output_dir: Path,
    replay_dir: Path | None = None,
    original_dir: Path | None = None,
) -> dict[str, Any]:
    reports = root / "VERIFICATION_REPORTS"
    replay_dir = replay_dir or reports / "taf_replay_multiconfig_verification_2026"
    original_dir = original_dir or reports / "original_taf_structured_baseline_2026"
    metar_dir = reports / "metar_standalone" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)

    replay_issuance = _read_csv(replay_dir / "taf_replay_multiconfig_issuance.csv")
    original_issuance = _read_csv(original_dir / "historical_preconfigured_taf_issuance.csv")
    replay_starts = {
        (_parse_utc(row["issuance_utc"]) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        for row in replay_issuance
    }
    original_starts = {row["taf_valid_start_utc"] for row in original_issuance}
    common_starts = replay_starts & original_starts
    metar = _metar_by_time(metar_dir)
    replay = _replay_rows(replay_dir / "taf_replay_multiconfig_hourly.csv", metar, common_starts)
    original = _original_rows(
        original_dir / "historical_preconfigured_taf_hourly.csv",
        original_dir / "historical_preconfigured_taf_issuance.csv",
        metar,
        common_starts,
    )
    source_rows: dict[str, list[dict[str, Any]]] = {"original_baseline": original, **dict(sorted(replay.items()))}
    event_metrics = {
        source: {
            event: {f"pm{window}h": _aggregate_event_rows(rows, event, window) for window in WINDOWS}
            for event in EVENTS
        }
        for source, rows in source_rows.items()
    }
    regime_metrics = {source: _regime_scores(rows) for source, rows in source_rows.items()}
    summary = {
        "scope": "experimental historical TAF replay; no operational pipeline changes",
        "common_valid_start_count": len(common_starts),
        "replay_configuration_count": len(replay),
        "quality_metar_hours": len(metar),
        "event_definitions": {
            "rain_any": "quality-eligible rainfall_raw_tenths_mm >= 1 (0.1 mm)",
            "rain_heavy": "quality-eligible rainfall_raw_tenths_mm > 40 (>4.0 mm)",
            "thunderstorm": "TS token in METAR or evaluated TAF weather group",
        },
        "regime_definitions": {
            "dry": "quality-eligible hour without measurable rain",
            "rain": "quality-eligible hour with measurable rain",
            "dawn_fog_proxy": "04-08 WITA with observed visibility <= 5000 m or BR/FG",
        },
        "event_metrics": event_metrics,
        "regime_metrics": regime_metrics,
        "event_assessment": {
            source: {event: _event_status(metrics[event]["pm2h"]) for event in EVENTS}
            for source, metrics in event_metrics.items()
        },
    }

    flat_rows = []
    for source, metrics in event_metrics.items():
        for event in EVENTS:
            for window in WINDOWS:
                flat_rows.append({"source": source, "event": event, "window_hours": window, **metrics[event][f"pm{window}h"]})
    _write_csv(
        output_dir / "taf_replay_event_window_metrics.csv",
        flat_rows,
        ["source", "event", "window_hours", "sample_size", "observed_events", "forecast_events", "hits", "misses", "false_alarms", "correct_negatives", "POD", "FAR", "CSI", "HSS", "mean_abs_timing_error_h", "mean_signed_timing_error_h"],
    )
    flat_regimes = []
    for source, regimes in regime_metrics.items():
        for regime, metrics in regimes.items():
            for metric, value in metrics.items():
                flat_regimes.append({"source": source, "regime": regime, "metric": metric, **value})
    _write_csv(
        output_dir / "taf_replay_regime_metrics.csv",
        flat_regimes,
        ["source", "regime", "metric", "eligible_hours", "hits", "score_percent"],
    )
    (output_dir / "taf_replay_event_window_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_markdown(output_dir / "TAF_REPLAY_EVENT_WINDOW_VERIFICATION.md", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_replay_event_window_verification_2026"),
    )
    parser.add_argument("--replay-dir", type=Path, default=None)
    parser.add_argument("--original-dir", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.output_dir, args.replay_dir, args.original_dir), indent=2))


if __name__ == "__main__":
    main()
