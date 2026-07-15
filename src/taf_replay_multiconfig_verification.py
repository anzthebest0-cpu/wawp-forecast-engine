"""Verify all historical TAF replay configurations against canonical METAR data.

This is deliberately independent of the copied Excel verification workbooks.
It reads the eight generated configurations from ``replay_metadata.csv`` and
scores their original text against the canonical January-May 2026 METAR
archive.  Results are emitted as an audit CSV, a per-issuance CSV, JSON, and
a concise Markdown comparison report.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.legacy_taf_verification import (
    METRICS,
    _aggregate_issuance,
    _is_true,
    _quality_rain_provenance,
    _quality_row_is_eligible,
    _read_metar_rows,
    _write_csv,
    active_state_structured,
    parse_metar,
    parse_taf,
    repair_validity_to_24h,
    score_hour,
)


UTC = timezone.utc
PERIODS = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05")
CORE_METRICS = ("wind_direction", "wind_speed", "wind_gust", "rain_occurrence")


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _read_replay_metadata(path: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    manifest_path = path.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    configs = {config["name"]: config for config in manifest["configs"]}
    return rows, configs


def _metar_indexes(metar_dir: Path) -> tuple[dict[str, dict[tuple[int, int], dict[str, Any]]], dict[datetime, dict[str, Any]], dict[str, dict[str, int]]]:
    legacy_by_period: dict[str, dict[tuple[int, int], dict[str, Any]]] = {}
    quality_by_time: dict[datetime, dict[str, Any]] = {}
    quality_counts: dict[str, dict[str, int]] = {}
    for period in PERIODS:
        rows = _read_metar_rows(metar_dir / f"metar_wawp_{period}.csv")
        legacy_by_period[period] = {
            (row["metar_day"], row["metar_hour"]): row
            for row in rows
            if row["metar_day"] is not None and row["metar_hour"] is not None
        }
        quality_by_time.update({row["observed_at"]: row for row in rows if _quality_row_is_eligible(row)})
        quality_counts[period] = {
            "normalized_rain_timestamp_rows": sum(
                row.get("observed_at") is not None
                and not _is_true(row.get("rain_source_timestamp_matches_observed_at"))
                and _is_true(row.get("metar_time_group_matches_rain_timestamp"))
                for row in rows
            ),
            "unaligned_rain_timestamp_rows": sum(
                row.get("observed_at") is not None
                and not _is_true(row.get("rain_source_timestamp_matches_observed_at"))
                and not _is_true(row.get("metar_time_group_matches_rain_timestamp"))
                for row in rows
            ),
            "invalid_calendar_rows": sum(row.get("observed_at") is None for row in rows),
            "non_hourly_rows": sum(row.get("metar_minute") not in {0, None} for row in rows),
        }
    return legacy_by_period, quality_by_time, quality_counts


def _observed_rain(row: dict[str, Any]) -> str:
    value = row.get("rainfall_raw_tenths_mm")
    return "RA" if value is not None and value / 10.0 > 4.0 else "0"


def _configuration_counts(taf_text: str) -> dict[str, int]:
    words = set(taf_text.replace("=", "").split())
    return {
        "contains_rain": int(any("RA" in word for word in words)),
        "contains_thunderstorm": int(any("TS" in word for word in words)),
        "contains_tempo": int("TEMPO" in words),
        "contains_becmg": int("BECMG" in words),
    }


def _verify_replay_row(
    row: dict[str, str],
    legacy_by_period: dict[str, dict[tuple[int, int], dict[str, Any]]],
    quality_by_time: dict[datetime, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    issue_time = _parse_utc(row["issuance_utc"])
    # Replay issuance is always one hour before its validity start (23Z->00Z,
    # 05Z->06Z, 11Z->12Z, 17Z->18Z). Use that dated metadata to disambiguate
    # month-boundary TAF groups before parsing their day-only syntax.
    expected_start = issue_time + timedelta(hours=1)
    period = expected_start.strftime("%Y-%m")
    if period not in PERIODS:
        return None
    source_taf = parse_taf(row["taf"], period)
    taf, repaired, reported_hours = repair_validity_to_24h(source_taf)
    if taf.valid_start != expected_start:
        raise ValueError(
            f"Replay TAF validity does not match metadata: {row['config']} {row['issuance_utc']} "
            f"-> {taf.valid_start.isoformat()} (expected {expected_start.isoformat()})"
        )
    hourly: list[dict[str, Any]] = []
    current = taf.valid_start
    while current < taf.valid_end:
        current_period = current.strftime("%Y-%m")
        legacy_row = (
            legacy_by_period[current_period].get((current.day, current.hour))
            if current_period in legacy_by_period else None
        )
        quality_row = quality_by_time.get(current)
        change_group, forecast, tempo_has_wind = active_state_structured(taf, current)
        source_row = quality_row or legacy_row
        hourly_row: dict[str, Any] = {
            "configuration": row["config"],
            "period": period,
            "issuance_utc": row["issuance_utc"],
            "valid_time_utc": current.isoformat().replace("+00:00", "Z"),
            "taf": taf.text,
            "change_group": change_group,
            "tempo_has_wind": tempo_has_wind,
            "reported_taf_valid_end_utc": source_taf.valid_end.isoformat().replace("+00:00", "Z"),
            "reported_validity_hours": reported_hours,
            "validity_repaired_to_24h": repaired,
            "forecast_wind_direction": "VRB" if forecast.wind_is_variable else forecast.wind_direction,
            "forecast_wind_speed": forecast.wind_speed,
            "forecast_wind_gust": forecast.wind_gust,
            "forecast_visibility_m": forecast.visibility_m,
            "forecast_weather": forecast.weather,
            "forecast_cloud_amount": forecast.cloud_amount,
            "forecast_cloud_base_ft": forecast.cloud_base_ft,
            "metar_text": source_row.get("metar_text") if source_row else "",
            "metar_time_group": source_row.get("metar_time_group") if source_row else "",
            "quality_rain_provenance": "not_scored_missing_observation",
        }
        legacy_scores = {metric: None for metric in METRICS}
        quality_scores = {metric: None for metric in METRICS}
        if legacy_row:
            observed = parse_metar(legacy_row.get("metar_text", ""))
            legacy_scores = score_hour(
                forecast,
                observed,
                _observed_rain(legacy_row),
                legacy_missing_visibility_high=True,
                observed_metar_text=legacy_row.get("metar_text", ""),
            )
            hourly_row.update({f"legacy_observed_{key}": value for key, value in asdict(observed).items()})
        if quality_row:
            observed = parse_metar(quality_row.get("metar_text", ""))
            quality_scores = score_hour(
                forecast,
                observed,
                _observed_rain(quality_row),
                legacy_missing_visibility_high=False,
                observed_metar_text=quality_row.get("metar_text", ""),
            )
            rain_eligible, provenance = _quality_rain_provenance(quality_row)
            hourly_row["quality_rain_provenance"] = provenance
            if not rain_eligible:
                quality_scores["rain_occurrence"] = None
            hourly_row.update({f"quality_observed_{key}": value for key, value in asdict(observed).items()})
        hourly_row.update({f"legacy_compatibility_{metric}": legacy_scores[metric] for metric in METRICS})
        hourly_row.update({f"quality_gated_{metric}": quality_scores[metric] for metric in METRICS})
        hourly.append(hourly_row)
        current += timedelta(hours=1)

    summary: dict[str, Any] = {
        "configuration": row["config"],
        "period": period,
        "issuance_utc": row["issuance_utc"],
        "taf": taf.text,
        "validity_repaired_to_24h": repaired,
        "reported_validity_hours": reported_hours,
        **_configuration_counts(taf.text),
    }
    for mode in ("legacy_compatibility", "quality_gated"):
        for metric, values in _aggregate_issuance(hourly, mode).items():
            for field, value in values.items():
                summary[f"{mode}_{metric}_{field}"] = value
    return hourly, summary


def _summarize_issuances(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    metric_summary: dict[str, dict[str, float | int | None]] = {}
    for metric in METRICS:
        values = [
            float(row[f"{mode}_{metric}_legacy_score_percent"])
            for row in rows
            if row.get(f"{mode}_{metric}_legacy_score_percent") is not None
        ]
        metric_summary[metric] = {
            "issuance_count": len(values),
            "score_percent": sum(values) / len(values) if values else None,
        }
    available_all = [metric_summary[metric]["score_percent"] for metric in METRICS if metric_summary[metric]["score_percent"] is not None]
    available_core = [metric_summary[metric]["score_percent"] for metric in CORE_METRICS if metric_summary[metric]["score_percent"] is not None]
    return {
        "metrics": metric_summary,
        "all_elements_mean_percent": sum(available_all) / len(available_all) if available_all else None,
        "core_mean_percent": sum(available_core) / len(available_core) if available_core else None,
        "taf_count": len(rows),
        "rain_taf_count": sum(int(row["contains_rain"]) for row in rows),
        "thunderstorm_taf_count": sum(int(row["contains_thunderstorm"]) for row in rows),
        "tempo_taf_count": sum(int(row["contains_tempo"]) for row in rows),
        "becmg_taf_count": sum(int(row["contains_becmg"]) for row in rows),
        "repaired_validity_count": sum(_is_true(row["validity_repaired_to_24h"]) for row in rows),
    }


def _score(value: Any) -> str:
    return "not scored" if value is None else f"{float(value):.2f}%"


def _write_markdown(path: Path, config_info: dict[str, dict[str, Any]], result: dict[str, Any]) -> None:
    lines = [
        "# Multi-Configuration Historical TAF Verification",
        "",
        "## Scope",
        "",
        "This report verifies the eight machine-generated configurations in `artifacts/taf_replay_2026_h1/replay_metadata.csv` against canonical WAWP METAR evidence for January-May 2026. It does not use cached Excel formula outputs.",
        "",
        "Every configuration uses the same historical continuous forecast archive. The comparison isolates weight lookback, historical-prior QM mode, and conservative caps; it does not prove lead-aware operational QM skill.",
        "",
        "## Configuration Definitions",
        "",
        "| Configuration | Weights | QM | Conservative caps |",
        "| --- | --- | --- | --- |",
    ]
    for name, info in sorted(config_info.items()):
        weights = "equal" if info["weight_mode"] == "equal" else f"as-of {info['lookback_days']} d"
        qm = {"none": "raw", "global": "historical prior, global", "regime": "historical prior, regime"}[info["qm_mode"]]
        lines.append(f"| `{name}` | {weights} | {qm} | {'yes' if info['conservative_caps'] else 'no'} |")

    lines.extend([
        "",
        "## Overall Quality-Gated Ranking",
        "",
        "Scores are equal averages of each issuance. `All elements` averages every available category; `Core` averages wind direction, wind speed, gust, and rain occurrence. These broad legacy thresholds are useful for comparing configurations, not for declaring operational readiness.",
        "",
        "| Rank | Configuration | All elements | Core | Rain TAFs | TS TAFs | TEMPO TAFs | BECMG TAFs |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    ranking = sorted(
        result["overall"].items(),
        key=lambda item: item[1]["quality_gated"]["all_elements_mean_percent"] or -1,
        reverse=True,
    )
    for rank, (name, summary) in enumerate(ranking, start=1):
        quality = summary["quality_gated"]
        lines.append(
            f"| {rank} | `{name}` | {_score(quality['all_elements_mean_percent'])} | "
            f"{_score(quality['core_mean_percent'])} | {quality['rain_taf_count']} | "
            f"{quality['thunderstorm_taf_count']} | {quality['tempo_taf_count']} | {quality['becmg_taf_count']} |"
        )

    lines.extend([
        "",
        "## Monthly Quality-Gated Comparison",
        "",
        "| Month | Configuration | All elements | Core | Rain occurrence | Visibility | Cloud amount | Cloud base |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for period in PERIODS:
        for name in sorted(config_info):
            summary = result["monthly"][period][name]["quality_gated"]
            metrics = summary["metrics"]
            lines.append(
                f"| {period} | `{name}` | {_score(summary['all_elements_mean_percent'])} | "
                f"{_score(summary['core_mean_percent'])} | {_score(metrics['rain_occurrence']['score_percent'])} | "
                f"{_score(metrics['visibility']['score_percent'])} | {_score(metrics['cloud_amount']['score_percent'])} | "
                f"{_score(metrics['cloud_base']['score_percent'])} |"
            )

    lines.extend([
        "",
        "## Data Handling",
        "",
        "- March and May HUJAN dates are normalized only where their day/hour still matches the paired METAR sequence. This is recorded per row as `timestamp_normalized_from_row_alignment`.",
        "- April has 24 invalid day-31 observations; these remain unavailable to the quality-gated score.",
        "- One May 00:30Z record remains non-hourly and is not forced into an hourly verification slot.",
        "- Any impossible generated TAF validity is evaluated under a documented 24-hour repair rule. The source string and original duration remain in the audit exports.",
        "- TAF groups are evaluated as structured TAF state: completed BECMG changes persist, while active TEMPO changes temporarily overlay the prevailing state.",
        "",
        "## Deliverables",
        "",
        "- `taf_replay_multiconfig_hourly.csv`: per-hour TAF/METAR audit evidence.",
        "- `taf_replay_multiconfig_issuance.csv`: per-TAF category scores and configuration event counts.",
        "- `taf_replay_multiconfig_summary.json`: machine-readable monthly and overall comparison.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_verification(metadata_path: Path, metar_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata, config_info = _read_replay_metadata(metadata_path)
    legacy_by_period, quality_by_time, quality_counts = _metar_indexes(metar_dir)
    hourly_rows: list[dict[str, Any]] = []
    issuance_rows: list[dict[str, Any]] = []
    for row in metadata:
        verified = _verify_replay_row(row, legacy_by_period, quality_by_time)
        if verified is None:
            continue
        hourly, issuance = verified
        hourly_rows.extend(hourly)
        issuance_rows.append(issuance)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    overall_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in issuance_rows:
        grouped[(row["period"], row["configuration"])].append(row)
        overall_grouped[row["configuration"]].append(row)
    monthly = {
        period: {
            config: {mode: _summarize_issuances(grouped[(period, config)], mode) for mode in ("legacy_compatibility", "quality_gated")}
            for config in sorted(config_info)
        }
        for period in PERIODS
    }
    overall = {
        config: {mode: _summarize_issuances(overall_grouped[config], mode) for mode in ("legacy_compatibility", "quality_gated")}
        for config in sorted(config_info)
    }
    payload = {
        "experiment": "continuous_historical_prior_taf_replay_multiconfig_verification",
        "metadata_source": str(metadata_path),
        "evaluated_periods": list(PERIODS),
        "configuration_definitions": config_info,
        "data_quality": quality_counts,
        "monthly": monthly,
        "overall": overall,
        "issuance_count": len(issuance_rows),
        "hourly_row_count": len(hourly_rows),
    }
    _write_csv(output_dir / "taf_replay_multiconfig_hourly.csv", hourly_rows)
    _write_csv(output_dir / "taf_replay_multiconfig_issuance.csv", issuance_rows)
    (output_dir / "taf_replay_multiconfig_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    report = output_dir / "TAF_REPLAY_MULTICONFIG_VERIFICATION_REPORT.md"
    _write_markdown(report, config_info, payload)
    return {"report": str(report), "output_dir": str(output_dir), **payload}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=root / "artifacts" / "taf_replay_2026_h1" / "replay_metadata.csv")
    parser.add_argument("--metar-dir", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\metar_standalone\canonical"))
    parser.add_argument("--output-dir", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_replay_multiconfig_verification_2026"))
    args = parser.parse_args()
    result = run_verification(args.metadata, args.metar_dir, args.output_dir)
    print(json.dumps({key: result[key] for key in ("report", "output_dir", "issuance_count", "hourly_row_count")}, indent=2))


if __name__ == "__main__":
    main()
