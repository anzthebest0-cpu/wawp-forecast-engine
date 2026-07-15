"""Dual-score comparison of human and frozen configuration-tweak TAFs.

For each January-June 2026 month this offline experiment compares the original
human TAF against the three configurations with a complete frozen Jan-Jun
replay archive:

* ``control_current``
* ``rain_50_20``
* ``rain_50_20_becmg3``

Two deliberately different methods are reported side by side:

1. ``workbook_legacy`` reproduces the original worksheet-style category rules,
   source-month boundary, and TEMPO adjustment.
2. ``taf_native_reference`` uses group-aware TAF interpretation and strict
   event-episode verification with no timing grace.

Nothing in this module alters operational guidance, source workbooks, or the
forecast database.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from src import taf_native_h1_comparison as h1
from src.legacy_taf_verification import (
    METRICS,
    _aggregate_issuance,
    _read_metar_rows,
    active_state,
    parse_metar,
    parse_taf,
    repair_validity_to_24h,
    score_hour,
)
from src.taf_native_verification import (
    _has_complete_cadence_coverage,
    _interval_match_counts,
    _observations_in_validity,
    _probability_scores,
    _range_rows,
    _summarize_ranges,
    _weather_has_event,
    forecast_event_intervals,
    observed_event_episodes,
    parse_native_taf,
)


CONFIGS = ("control_current", "rain_50_20", "rain_50_20_becmg3")
SYSTEMS = ("original_human",) + tuple(f"machine_{name}" for name in CONFIGS)
LABELS = {
    "original_human": "Original human TAF",
    "machine_control_current": "control_current",
    "machine_rain_50_20": "rain_50_20",
    "machine_rain_50_20_becmg3": "rain_50_20_becmg3",
}
CONFIG_NOTES = {
    "machine_control_current": "40% wet-model threshold; current bridge policy.",
    "machine_rain_50_20": "50% wet-model threshold; 20% agreement; no bridge.",
    "machine_rain_50_20_becmg3": "rain_50_20 plus stricter BECMG wording qualification.",
}
ROOT = Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS")
OUT = ROOT / "taf_dual_scoring_h1_config_comparison_2026"


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = sorted({key for row in rows for key in row}) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _candidate_maps(period: str) -> tuple[dict[str, dict[datetime, h1.Candidate]], list[dict[str, Any]]]:
    raw = {"original_human": h1._human_candidates(period)}
    raw.update({f"machine_{name}": h1._machine_candidates(period, name) for name in CONFIGS})
    maps: dict[str, dict[datetime, h1.Candidate]] = {}
    audit: list[dict[str, Any]] = []
    for system, rows in raw.items():
        unique, duplicates = h1._unique_by_valid_start(rows)
        maps[system] = unique
        audit.extend({"period": period, "system": system, "reason": "exact_duplicate_source_taf", "source_id": item.source_id, "valid_start_utc": _iso(item.valid_start)} for item in duplicates)
    return maps, audit


def _common_starts(maps: dict[str, dict[datetime, h1.Candidate]]) -> list[datetime]:
    return sorted(set.intersection(*(set(values) for values in maps.values())))


def _legacy_index(period: str) -> dict[tuple[int, int], dict[str, Any]]:
    if period == "2026-06":
        _, hourly, _ = h1.extract_june_workbook(h1.JUNE_WORKBOOK)
        rows = []
        for row in hourly:
            at = datetime.fromisoformat(row["observed_at_utc"].replace("Z", "+00:00"))
            rows.append({"metar_day": at.day, "metar_hour": at.hour, "metar_text": row["metar_text"], "rainfall_raw_tenths_mm": None})
    else:
        rows = _read_metar_rows(h1.CANONICAL_METAR / f"metar_wawp_{period}.csv")
    return {
        (int(row["metar_day"]), int(row["metar_hour"])): row
        for row in rows if row.get("metar_day") is not None and row.get("metar_hour") is not None
    }


def _legacy_rain(row: dict[str, Any]) -> str:
    value = row.get("rainfall_raw_tenths_mm")
    if value is None:
        # June's source workbook has no independent HUJAN amount column.
        # Its historical Endapan assessment is therefore reproduced from the
        # explicit METAR weather report rather than inventing a zero amount.
        return "RA" if h1._weather_has_event(str(row.get("metar_text") or ""), "rain") else "0"
    try:
        return "RA" if value is not None and float(value) / 10.0 > 4.0 else "0"
    except (TypeError, ValueError):
        return "0"


def _workbook_scores(period: str, maps: dict[str, dict[datetime, h1.Candidate]], starts: list[datetime]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    index = _legacy_index(period)
    issuance_rows: list[dict[str, Any]] = []
    hourly_rows: list[dict[str, Any]] = []
    for valid_start in starts:
        for system in SYSTEMS:
            candidate = maps[system][valid_start]
            source = parse_taf(candidate.taf_text, candidate.parse_period)
            taf, repaired, reported_hours = repair_validity_to_24h(source)
            rows: list[dict[str, Any]] = []
            current = taf.valid_start
            while current < taf.valid_end:
                # Original worksheet scoring never pulls an observation from
                # the next source-month sheet for the final 23Z issuance.
                observation = index.get((current.day, current.hour)) if current.strftime("%Y-%m") == period else None
                change_group, forecast, tempo_has_wind = active_state(taf, current)
                scores = {metric: None for metric in METRICS}
                if observation:
                    observed = parse_metar(str(observation.get("metar_text") or ""))
                    scores = score_hour(forecast, observed, _legacy_rain(observation), legacy_missing_visibility_high=True, observed_metar_text=str(observation.get("metar_text") or ""))
                row = {
                    "period": period, "system": system, "issuance_utc": _iso(taf.issue_time), "valid_start_utc": _iso(taf.valid_start),
                    "valid_time_utc": _iso(current), "change_group": change_group, "tempo_has_wind": tempo_has_wind,
                    "metar_text": observation.get("metar_text", "") if observation else "", "validity_repaired": repaired,
                    **{f"workbook_legacy_{metric}": scores[metric] for metric in METRICS},
                }
                rows.append(row)
                hourly_rows.append(row)
                current += timedelta(hours=1)
            summary = _aggregate_issuance(rows, "workbook_legacy")
            record = {"period": period, "system": system, "issuance_utc": _iso(taf.issue_time), "valid_start_utc": _iso(taf.valid_start), "validity_repaired": repaired, "reported_validity_hours": reported_hours, "taf": taf.text}
            for metric, values in summary.items():
                for field, value in values.items():
                    record[f"{metric}_{field}"] = value
            issuance_rows.append(record)
    aggregate: dict[str, Any] = {}
    for system in SYSTEMS:
        relevant = [row for row in issuance_rows if row["system"] == system]
        metrics: dict[str, Any] = {}
        for metric in METRICS:
            values = [float(row[f"{metric}_legacy_score_percent"]) for row in relevant if row.get(f"{metric}_legacy_score_percent") is not None]
            metrics[metric] = {"issuance_count": len(values), "score_percent": round(sum(values) / len(values), 4) if values else None}
        values = [item["score_percent"] for item in metrics.values() if item["score_percent"] is not None]
        core = [metrics[name]["score_percent"] for name in ("wind_direction", "wind_speed", "wind_gust", "rain_occurrence") if metrics[name]["score_percent"] is not None]
        aggregate[system] = {"metrics": metrics, "all_elements_mean_percent": round(sum(values) / len(values), 4) if values else None, "core_mean_percent": round(sum(core) / len(core), 4) if core else None, "issuances": len(relevant)}
    return aggregate, issuance_rows, hourly_rows


def _native_scores(period: str, maps: dict[str, dict[datetime, h1.Candidate]], starts: list[datetime]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    observations, cadence, _ = h1._month_observations(period)
    episodes: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    probabilities: list[dict[str, Any]] = []
    ranges: list[dict[str, Any]] = []
    excluded = 0
    for valid_start in starts:
        parsed = {system: parse_native_taf(maps[system][valid_start].taf_text, maps[system][valid_start].parse_period) for system in SYSTEMS}
        windows = {(taf.valid_start, taf.valid_end) for taf in parsed.values()}
        if len(windows) != 1:
            excluded += 1
            continue
        reference_taf = parsed["original_human"]
        validity_observations = _observations_in_validity(reference_taf, observations)
        if not _has_complete_cadence_coverage(reference_taf, validity_observations, cadence):
            excluded += 1
            continue
        for system, taf in parsed.items():
            for range_row in _range_rows(taf, validity_observations):
                ranges.append({"period": period, "system": system, **range_row})
            for event in ("rain", "thunderstorm"):
                forecast = forecast_event_intervals(taf, event)
                observed = observed_event_episodes(validity_observations, event, sample_interval=cadence)
                counts = _interval_match_counts(forecast, observed)
                episodes.append({"period": period, "system": system, "issuance_utc": _iso(taf.issue_time), "valid_start_utc": _iso(taf.valid_start), "event": event, **counts})
                for observation in validity_observations:
                    at = datetime.fromisoformat(observation["observed_at_utc"].replace("Z", "+00:00"))
                    samples.append({"period": period, "system": system, "issuance_utc": _iso(taf.issue_time), "valid_time_utc": observation["observed_at_utc"], "event": event, "forecast_event": any(start <= at < end for start, end in forecast), "observed_event": _weather_has_event(observation["metar_text"], event), "metar_text": observation["metar_text"]})
                probabilities.extend({"period": period, "system": system, **row} for row in _probability_scores(taf, validity_observations, event))
    aggregate: dict[str, Any] = {}
    for system in SYSTEMS:
        event_summary: dict[str, Any] = {}
        for event in ("rain", "thunderstorm"):
            totals = defaultdict(int)
            for row in episodes:
                if row["system"] == system and row["event"] == event:
                    for key in ("forecast_episodes", "observed_episodes", "hits", "misses", "false_alarms"):
                        totals[key] += int(row[key])
            event_rows = [{"forecast": bool(row["forecast_event"]), "observed": bool(row["observed_event"])} for row in samples if row["system"] == system and row["event"] == event]
            probability_rows = [row for row in probabilities if row["system"] == system and row["event"] == event]
            event_summary[event] = {"episode": h1._event_metrics(dict(totals)), "sample": h1._sample_event_metrics(event_rows), "probability_groups": len(probability_rows), "mean_brier": round(sum(float(row["brier_component"]) for row in probability_rows) / len(probability_rows), 4) if probability_rows else None}
        aggregate[system] = {"events": event_summary, "ranges": _summarize_ranges([row for row in ranges if row["system"] == system]), "issuances": len({row["valid_start_utc"] for row in episodes if row["system"] == system}), "excluded_common_issuances": excluded}
    return aggregate, episodes, samples, probabilities + ranges


def _fmt(value: Any, percent: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{100 * float(value):.1f}%" if percent else f"{float(value):.3f}"


def _score_pct(value: Any) -> str:
    """Format original workbook scores, which are already percentage points."""
    return "n/a" if value is None else f"{float(value):.1f}%"


def _styles() -> dict[str, Any]:
    return h1._styles()


def _table(rows: list[list[Any]], widths: list[float], styles: dict[str, Any], small: bool = False) -> Any:
    return h1._pdf_table(rows, widths, styles, small=small)


def _footer(canvas: Any, document: Any) -> None:
    h1._footer(canvas, document)


def _workbook_table(data: dict[str, Any], styles: dict[str, Any]) -> Any:
    rows = [["Workbook-style metric", *[LABELS[system] for system in SYSTEMS]]]
    for metric in (*METRICS, "all_elements_mean_percent", "core_mean_percent"):
        label = metric.replace("_", " ").title()
        values = []
        for system in SYSTEMS:
            value = data[system].get(metric) if metric.endswith("mean_percent") else data[system]["metrics"][metric]["score_percent"]
            values.append(_score_pct(value))
        rows.append([label, *values])
    return _table(rows, [49 * h1.mm, 49 * h1.mm, 49 * h1.mm, 49 * h1.mm, 49 * h1.mm], styles, small=True)


def _native_table(data: dict[str, Any], event: str, styles: dict[str, Any]) -> Any:
    rows = [[f"Reference metric: {event}", *[LABELS[system] for system in SYSTEMS]]]
    for field, label, pct in (("POD", "Episode POD", True), ("FAR", "Episode FAR", True), ("CSI", "Episode CSI", True), ("frequency_bias", "Episode frequency bias", False), ("HSS", "Exact-sample HSS", False), ("accuracy", "Exact-sample accuracy", True)):
        rows.append([label, *[_fmt(data[system]["events"][event]["episode" if field in {"POD", "FAR", "CSI", "frequency_bias"} else "sample"][field], percent=pct) for system in SYSTEMS]])
    return _table(rows, [49 * h1.mm, 49 * h1.mm, 49 * h1.mm, 49 * h1.mm, 49 * h1.mm], styles, small=True)


def _build_month_pdf(month: dict[str, Any], output: Path) -> None:
    styles = _styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output), pagesize=landscape(A4), leftMargin=14 * h1.mm, rightMargin=14 * h1.mm, topMargin=14 * h1.mm, bottomMargin=16 * h1.mm)
    count = month["scope"]
    story = [
        Paragraph(f"WAWP {month['period']}: Human TAF vs Configuration Tweaks", styles["title"]),
        Paragraph("Two scoring methods are intentionally shown together. They answer different questions and must not be blended into one ranking.", styles["body"]),
        Paragraph("Common Scope", styles["h"]),
        Paragraph(f"Four systems are compared only on the {count['common_candidates']} validity starts present in every frozen source. The referenced method further scores {month['reference']['original_human']['issuances']} complete-coverage TAFs. Original-score calculation preserves the historical source-month boundary, so a final 23Z TAF may have fewer scored legacy slots rather than borrowing next-month observations.", styles["body"]),
        Paragraph("Method 1: Original Workbook-Style Score", styles["h"]),
        Paragraph("This reproduces the historical category thresholds: direction within 60 degrees or light-wind allowance, wind speed within 10 kt, exact gust equality, visibility bands, rain from the source rainfall threshold above 4 mm for January-May, cloud categories, cloud-base tolerance, and the original non-monotonic TEMPO adjustment. June has no independent rainfall-amount column, so its original-style rain equality uses explicit METAR rain text. It is a reproducibility view, not a rare-event skill score.", styles["body"]),
        _workbook_table(month["workbook"], styles),
        Paragraph("Method 2: Referenced TAF-Native Score", styles["h"]),
        Paragraph("FM becomes prevailing at its stated time; BECMG is a transition and persists; TEMPO is a deterministic temporary window; PROB30/40 is probabilistic and excluded from deterministic event POD/FAR/CSI. Rain and thunderstorm episodes must overlap exactly; there is no timing grace. This is a WAWP adaptation of group-aware TAF verification principles, not an official regulator score.", styles["body"]),
        _native_table(month["reference"], "rain", styles), Spacer(1, 3 * h1.mm), _native_table(month["reference"], "thunderstorm", styles),
        Paragraph("Configuration Notes", styles["h"]),
        Paragraph("<br/>".join(f"<b>{LABELS[system]}</b>: {CONFIG_NOTES[system]}" for system in SYSTEMS if system != "original_human"), styles["small"]),
        Paragraph("Reading The Difference", styles["h"]),
        Paragraph("A high workbook-style percentage can be driven by correct dry hours or broad categorical tolerances. The referenced episode CSI, POD, FAR, frequency bias, and HSS expose whether rain or thunderstorm warnings are timely and proportionate. The two methods should therefore be reported side by side rather than averaged.", styles["body"]),
    ]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _overall_workbook(months: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for system in SYSTEMS:
        metrics = {}
        for metric in METRICS:
            values = [month["workbook"][system]["metrics"][metric]["score_percent"] for month in months if month["workbook"][system]["metrics"][metric]["score_percent"] is not None]
            metrics[metric] = {"score_percent": round(sum(values) / len(values), 4) if values else None}
        all_values = [metrics[key]["score_percent"] for key in METRICS if metrics[key]["score_percent"] is not None]
        core_values = [metrics[key]["score_percent"] for key in ("wind_direction", "wind_speed", "wind_gust", "rain_occurrence") if metrics[key]["score_percent"] is not None]
        result[system] = {"metrics": metrics, "all_elements_mean_percent": round(sum(all_values) / len(all_values), 4) if all_values else None, "core_mean_percent": round(sum(core_values) / len(core_values), 4) if core_values else None}
    return result


def _overall_reference(months: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for system in SYSTEMS:
        events = {}
        for event in ("rain", "thunderstorm"):
            episode = defaultdict(int)
            sample = defaultdict(int)
            for month in months:
                source = month["reference"][system]["events"][event]
                for key in ("forecast_episodes", "observed_episodes", "hits", "misses", "false_alarms"):
                    episode[key] += int(source["episode"][key])
                for key in ("hits", "misses", "false_alarms", "correct_negatives"):
                    sample[key] += int(source["sample"][key])
            synthetic = ([{"forecast": True, "observed": True}] * sample["hits"] + [{"forecast": False, "observed": True}] * sample["misses"] + [{"forecast": True, "observed": False}] * sample["false_alarms"] + [{"forecast": False, "observed": False}] * sample["correct_negatives"])
            events[event] = {"episode": h1._event_metrics(dict(episode)), "sample": h1._sample_event_metrics(synthetic)}
        result[system] = {"events": events}
    return result


def _build_summary_pdf(months: list[dict[str, Any],], overall_workbook: dict[str, Any], overall_reference: dict[str, Any], output: Path) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(str(output), pagesize=landscape(A4), leftMargin=14 * h1.mm, rightMargin=14 * h1.mm, topMargin=14 * h1.mm, bottomMargin=16 * h1.mm)
    monthly_rows = [["Month", "Common starts", "Human all elements", "control_current", "rain_50_20", "becmg3", "Reference rain CSI: human / 50-20 / becmg3"]]
    for month in months:
        ref = month["reference"]
        monthly_rows.append([month["period"], month["scope"]["common_candidates"], _score_pct(month["workbook"]["original_human"]["all_elements_mean_percent"]), _score_pct(month["workbook"]["machine_control_current"]["all_elements_mean_percent"]), _score_pct(month["workbook"]["machine_rain_50_20"]["all_elements_mean_percent"]), _score_pct(month["workbook"]["machine_rain_50_20_becmg3"]["all_elements_mean_percent"]), " / ".join(_fmt(ref[s]["events"]["rain"]["episode"]["CSI"], True) for s in ("original_human", "machine_rain_50_20", "machine_rain_50_20_becmg3"))])
    story = [
        Paragraph("WAWP January-June 2026 Dual-Score TAF Comparison", styles["title"]),
        Paragraph("Original human TAFs compared against every frozen configuration with a complete Jan-Jun replay source. The six other rain persistence variants remain January-May-only and are intentionally excluded from this headline comparison.", styles["body"]),
        Paragraph("Monthly Side-By-Side", styles["h"]), _table(monthly_rows, [25*h1.mm, 26*h1.mm, 35*h1.mm, 35*h1.mm, 35*h1.mm, 35*h1.mm, 62*h1.mm], styles, small=True),
        Paragraph("Method 1: Original Workbook-Style Calculation", styles["h"]), _workbook_table(overall_workbook, styles),
        Paragraph("Method 2: Referenced TAF-Native Calculation - Rain", styles["h"]), _native_table(overall_reference, "rain", styles),
        Paragraph("Method 2: Referenced TAF-Native Calculation - Thunderstorm", styles["h"]), _native_table(overall_reference, "thunderstorm", styles),
        Paragraph("Verdict", styles["h"]),
        Paragraph("Do not select a configuration from the workbook-style percentage alone: it preserves broad category tolerances, dry-hour dominance, and the legacy TEMPO adjustment. Use it to reconcile with historical reporting. Use the referenced episode metrics to decide rain and thunderstorm readiness. January-May contributed to configuration development; June is the independent month in this archive. No configuration is promoted by this document.", styles["body"]),
    ]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def run(output_dir: Path = OUT, pdf_dir: Path = Path(r"D:\UJI_PERFORMA_MODEL\meteologix-wawp-main\output\pdf")) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    months: list[dict[str, Any]] = []
    legacy_issuance: list[dict[str, Any]] = []
    legacy_hours: list[dict[str, Any]] = []
    native_episodes: list[dict[str, Any]] = []
    native_samples: list[dict[str, Any]] = []
    native_misc: list[dict[str, Any]] = []
    candidate_audit: list[dict[str, Any]] = []
    for period in h1._periods():
        maps, audit = _candidate_maps(period)
        starts = _common_starts(maps)
        workbook, issuance_rows, hourly_rows = _workbook_scores(period, maps, starts)
        reference, episodes, samples, misc = _native_scores(period, maps, starts)
        month = {"period": period, "scope": {"common_candidates": len(starts), "candidate_counts": {system: len(maps[system]) for system in SYSTEMS}}, "workbook": workbook, "reference": reference}
        months.append(month)
        candidate_audit.extend(audit)
        legacy_issuance.extend(issuance_rows); legacy_hours.extend(hourly_rows); native_episodes.extend(episodes); native_samples.extend(samples); native_misc.extend(misc)
        (output_dir / f"{period}_dual_score_summary.json").write_text(json.dumps(month, indent=2) + "\n", encoding="utf-8")
        _build_month_pdf(month, pdf_dir / f"WAWP_{period.replace('-', '_')}_Human_vs_Config_Tweaks_Dual_Scoring.pdf")
    overall_workbook = _overall_workbook(months)
    overall_reference = _overall_reference(months)
    payload = {"experiment": "human_vs_frozen_config_tweaks_dual_scoring", "configs": CONFIGS, "months": months, "overall_workbook_style": overall_workbook, "overall_taf_native_reference": overall_reference}
    (output_dir / "h1_dual_score_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _write_csv(output_dir / "candidate_dedup_audit.csv", candidate_audit)
    _write_csv(output_dir / "workbook_style_issuance_scores.csv", legacy_issuance)
    _write_csv(output_dir / "workbook_style_hourly_scores.csv", legacy_hours)
    _write_csv(output_dir / "reference_event_episodes.csv", native_episodes)
    _write_csv(output_dir / "reference_event_samples.csv", native_samples)
    _write_csv(output_dir / "reference_probability_and_range_rows.csv", native_misc)
    summary_pdf = pdf_dir / "WAWP_Jan_Jun_2026_Human_vs_Config_Tweaks_Dual_Scoring_Summary.pdf"
    _build_summary_pdf(months, overall_workbook, overall_reference, summary_pdf)
    return {"output_dir": str(output_dir), "summary_pdf": str(summary_pdf), "months": months}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--pdf-dir", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\meteologix-wawp-main\output\pdf"))
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.pdf_dir), indent=2, default=str))


if __name__ == "__main__":
    main()
