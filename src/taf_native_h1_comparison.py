"""Compare original WAWP TAFs with the frozen best experimental configuration.

This is an offline January-June 2026 experiment.  It uses the frozen
``raw_asof60 + rain_50_20`` replay candidate and the original human TAF text
under the same TAF-native interpretation.  It neither modifies a workbook nor
changes operational guidance.

The primary rare-event score is strict episode overlap: no arbitrary timing
grace is added.  BECMG is a transition and then persists; TEMPO is a
deterministic temporary window; and PROB30/40 is scored separately with Brier
components instead of being treated as a deterministic alert.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.legacy_taf_verification import INVALID_OBSERVATION_STATUSES, WORKBOOKS, _read_metar_rows
from src.taf_native_verification import (
    NativeTAF,
    _has_complete_cadence_coverage,
    _interval_match_counts,
    _observations_in_validity,
    _probability_scores,
    _range_rows,
    _sample_event_metrics,
    _summarize_ranges,
    _weather_has_event,
    _write_csv,
    _event_metrics,
    forecast_event_intervals,
    observed_event_episodes,
    parse_native_taf,
)
from src.taf_replay_june_holdout import extract_june_workbook


UTC = timezone.utc
OFFICIAL_ISSUE_HOURS = {"05:00", "11:00", "17:00", "23:00"}
BEST_CONFIG = {
    "name": "raw_asof60 + rain_50_20",
    "weight_mode": "asof",
    "lookback_days": 60,
    "qm_mode": "none",
    "rain_gate": "50% wet-model probability, 20% agreement, no bridge",
    "thunderstorm_policy": "broad_current",
    "status": "frozen experimental candidate; not operational guidance",
}
ROOT = Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS")
CANONICAL_METAR = ROOT / "metar_standalone" / "canonical"
ORIGINAL_BASELINE = ROOT / "original_taf_structured_baseline_2026" / "historical_preconfigured_taf_issuance.csv"
JAN_MAY_MACHINE = ROOT / "taf_rain_persistence_sweep_2026" / "replay_metadata.csv"
JUNE_MACHINE = ROOT / "taf_june_holdout_2026" / "june_machine_tafs.csv"
JUNE_WORKBOOK = Path.home() / "Downloads" / "Verifikasi TAF_FORM_Juni_2026 (2).xlsx"


@dataclass(frozen=True)
class Candidate:
    system: str
    period: str
    parse_period: str
    source_id: str
    taf_text: str
    issue_time: datetime
    valid_start: datetime
    valid_end: datetime
    validity_repaired: bool


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _periods() -> tuple[str, ...]:
    return ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")


def _month_observations(period: str) -> tuple[list[dict[str, Any]], timedelta, dict[str, int]]:
    """Load quality-eligible raw METAR text at its true archive cadence."""
    if period == "2026-06":
        raw_metar, _, _ = extract_june_workbook(JUNE_WORKBOOK)
        observations = [row for row in raw_metar if row["observed_at_utc"].startswith(period)]
        cadence = timedelta(minutes=30)
        quality = {
            "source_rows": len(raw_metar),
            "eligible_rows": len(observations),
            "excluded_invalid_rows": 0,
            "excluded_non_hourly_rows": 0,
        }
        return observations, cadence, quality

    rows = _read_metar_rows(CANONICAL_METAR / f"metar_wawp_{period}.csv")
    source_rows = len(rows)
    observations = [
        {
            "observed_at_utc": _iso(row["observed_at"]),
            "metar_text": str(row.get("metar_text") or ""),
            "source_metar_row": row.get("source_metar_sheet_row", ""),
        }
        for row in rows
        if row.get("observed_at") is not None
        and row.get("observed_at_status") not in INVALID_OBSERVATION_STATUSES
        and row.get("metar_minute") == 0
        and str(row.get("metar_text") or "").startswith("METAR WAWP")
    ]
    return observations, timedelta(hours=1), {
        "source_rows": source_rows,
        "eligible_rows": len(observations),
        "excluded_invalid_rows": sum(row.get("observed_at_status") in INVALID_OBSERVATION_STATUSES for row in rows),
        "excluded_non_hourly_rows": sum(row.get("metar_minute") not in {0, None} for row in rows),
    }


def _candidate_from_text(
    system: str,
    period: str,
    source_id: str,
    taf_text: str,
    *,
    parse_period: str | None = None,
    validity_repaired: bool = False,
) -> Candidate:
    resolved_period = parse_period or period
    taf = parse_native_taf(taf_text, resolved_period)
    return Candidate(
        system=system,
        period=period,
        parse_period=resolved_period,
        source_id=str(source_id),
        taf_text=taf.text,
        issue_time=taf.issue_time,
        valid_start=taf.valid_start,
        valid_end=taf.valid_end,
        validity_repaired=validity_repaired,
    )


def _human_candidates(period: str) -> list[Candidate]:
    if period == "2026-06":
        _, _, rows = extract_june_workbook(JUNE_WORKBOOK)
        candidates = [
            _candidate_from_text("original_human", period, f"june-row-{row['source_taf_row']}", row["taf"], validity_repaired=bool(row["validity_repaired_to_24h"]))
            for row in rows
        ]
    else:
        candidates = [
            _candidate_from_text(
                "original_human",
                period,
                f"source-row-{row['source_taf_row']}",
                row["taf_text"],
                parse_period=row["taf_valid_start_utc"][:7],
                validity_repaired=str(row.get("validity_repaired_to_24h", "")).lower() == "true",
            )
            for row in _read_csv(ORIGINAL_BASELINE)
            if row["period"] == period
        ]
    return [candidate for candidate in candidates if candidate.issue_time.strftime("%H:%M") in OFFICIAL_ISSUE_HOURS]


def _machine_candidates(period: str, configuration: str = "rain_50_20") -> list[Candidate]:
    rows: list[dict[str, str]]
    if period == "2026-06":
        rows = [row for row in _read_csv(JUNE_MACHINE) if row["configuration"] == configuration]
    else:
        rows = [
            row for row in _read_csv(JAN_MAY_MACHINE)
            if row["config"] == configuration and row["valid_start_wita"].startswith(period)
        ]
    candidates: list[Candidate] = []
    for index, row in enumerate(rows, start=2):
        parse_period = period
        if period != "2026-06":
            valid_start_local = datetime.fromisoformat(row["valid_start_wita"])
            parse_period = (valid_start_local - timedelta(hours=8)).strftime("%Y-%m")
        candidates.append(_candidate_from_text(f"machine_{configuration}", period, f"machine-row-{index}", row["taf"], parse_period=parse_period))
    return candidates


def _unique_by_valid_start(candidates: Iterable[Candidate]) -> tuple[dict[datetime, Candidate], list[Candidate]]:
    grouped: dict[datetime, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.valid_start].append(candidate)
    unique: dict[datetime, Candidate] = {}
    duplicates: list[Candidate] = []
    for key, rows in grouped.items():
        fingerprints = {row.taf_text for row in rows}
        if len(fingerprints) == 1:
            unique[key] = rows[0]
            duplicates.extend(rows[1:])
        else:
            duplicates.extend(rows)
    return unique, duplicates


def _event_summary(
    episode_rows: list[dict[str, Any]], sample_rows: list[dict[str, Any]], probability_rows: list[dict[str, Any]], system: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for event in ("rain", "thunderstorm"):
        event_episodes = [row for row in episode_rows if row["system"] == system and row["event"] == event]
        totals = defaultdict(int)
        for row in event_episodes:
            for field in ("forecast_episodes", "observed_episodes", "hits", "misses", "false_alarms"):
                totals[field] += int(row[field])
        events = [
            {"forecast": bool(row["forecast_event"]), "observed": bool(row["observed_event"])}
            for row in sample_rows
            if row["system"] == system and row["event"] == event
        ]
        probabilities = [
            row for row in probability_rows if row["system"] == system and row["event"] == event
        ]
        result[event] = {
            "episode": _event_metrics(dict(totals)),
            "sample": _sample_event_metrics(events),
            "probability": {
                "group_count": len(probabilities),
                "mean_brier_score": round(sum(float(row["brier_component"]) for row in probabilities) / len(probabilities), 4) if probabilities else None,
            },
        }
    return result


def _aggregate_scope(rows: list[dict[str, Any]], system: str) -> dict[str, Any]:
    selected = [row for row in rows if row["system"] == system]
    return {
        "issuances": len({row["valid_start_utc"] for row in selected}),
        "sample_count": sum(int(row["sample_count"]) for row in selected),
        "validity_repairs": sum(bool(row["validity_repaired"]) for row in selected),
    }


def _percentage(value: Any) -> str:
    return "n/a" if value is None else f"{100 * float(value):.1f}%"


def _number(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _delta(machine: Any, human: Any, *, higher_is_better: bool = True) -> str:
    if machine is None or human is None:
        return "n/a"
    difference = float(machine) - float(human)
    sign = "+" if difference >= 0 else ""
    label = "better" if (difference > 0) == higher_is_better and difference != 0 else ("worse" if difference != 0 else "same")
    return f"{sign}{difference:.3f} ({label})"


def _bias_delta(machine: Any, human: Any) -> str:
    """Frequency bias is best when it is closer to one, not simply larger."""
    if machine is None or human is None:
        return "n/a"
    difference = float(machine) - float(human)
    sign = "+" if difference >= 0 else ""
    machine_distance = abs(float(machine) - 1.0)
    human_distance = abs(float(human) - 1.0)
    label = "better" if machine_distance < human_distance else ("worse" if machine_distance > human_distance else "same")
    return f"{sign}{difference:.3f} ({label}; target 1.0)"


def _month_verdict(summary: dict[str, Any]) -> str:
    human = summary["systems"]["original_human"]["events"]["rain"]["episode"]
    machine = summary["systems"]["machine_rain_50_20"]["events"]["rain"]["episode"]
    if not human["observed_episodes"]:
        return "No observed rain episodes in the comparable scope; rain skill cannot distinguish the systems."
    if human["observed_episodes"] < 5:
        return "Very small observed-rain sample; differences are descriptive only, not a monthly winner."
    if machine["CSI"] is not None and human["CSI"] is not None and machine["CSI"] > human["CSI"]:
        human_bias_distance = abs((human["frequency_bias"] or 0.0) - 1.0)
        machine_bias_distance = abs((machine["frequency_bias"] or 0.0) - 1.0)
        if machine_bias_distance > human_bias_distance:
            return "Machine candidate detects more rain episodes, but it over-warns materially more; this is not a balanced monthly improvement."
        if machine["FAR"] is not None and human["FAR"] is not None and machine["FAR"] <= human["FAR"]:
            return "Machine candidate has a better descriptive rain balance this month, but this remains experimental evidence."
    if machine["CSI"] is not None and human["CSI"] is not None and machine["CSI"] < human["CSI"]:
        return "Original TAF has the stronger descriptive rain CSI this month; do not claim machine improvement."
    if machine["CSI"] == human["CSI"] and machine["FAR"] is not None and human["FAR"] is not None and machine["FAR"] > human["FAR"]:
        return "Both systems have the same rain CSI, but the machine candidate creates more false alarms; original TAF is more balanced."
    return "Mixed rare-event outcome; use the issuance ledger rather than an overall-accuracy winner."


def _score_month(period: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    observations, cadence, quality = _month_observations(period)
    human_all = _human_candidates(period)
    machine_all = _machine_candidates(period)
    humans, human_duplicate_rows = _unique_by_valid_start(human_all)
    machines, machine_duplicate_rows = _unique_by_valid_start(machine_all)
    common_starts = sorted(set(humans) & set(machines))
    issuances: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    probability_rows: list[dict[str, Any]] = []
    range_rows: list[dict[str, Any]] = []
    exclusions: defaultdict[str, int] = defaultdict(int)

    for valid_start in common_starts:
        human, machine = humans[valid_start], machines[valid_start]
        human_taf = parse_native_taf(human.taf_text, human.parse_period)
        machine_taf = parse_native_taf(machine.taf_text, machine.parse_period)
        if (human_taf.valid_start, human_taf.valid_end) != (machine_taf.valid_start, machine_taf.valid_end):
            exclusions["validity_mismatch"] += 1
            continue
        validity_observations = _observations_in_validity(human_taf, observations)
        if not _has_complete_cadence_coverage(human_taf, validity_observations, cadence):
            exclusions["incomplete_observation_coverage"] += 1
            continue
        for candidate, taf in ((human, human_taf), (machine, machine_taf)):
            issuance = {
                "period": period,
                "system": candidate.system,
                "source_id": candidate.source_id,
                "issuance_utc": _iso(taf.issue_time),
                "valid_start_utc": _iso(taf.valid_start),
                "valid_end_utc": _iso(taf.valid_end),
                "validity_repaired": candidate.validity_repaired,
                "sample_count": len(validity_observations),
                "taf": taf.text,
            }
            issuances.append(issuance)
            for row in _range_rows(taf, validity_observations):
                range_rows.append({"period": period, "system": candidate.system, **row})
            for event in ("rain", "thunderstorm"):
                forecast = forecast_event_intervals(taf, event)
                observed = observed_event_episodes(validity_observations, event, sample_interval=cadence)
                counts = _interval_match_counts(forecast, observed)
                episode_rows.append({
                    "period": period,
                    "system": candidate.system,
                    "issuance_utc": _iso(taf.issue_time),
                    "valid_start_utc": _iso(taf.valid_start),
                    "event": event,
                    **counts,
                    "forecast_intervals_utc": json.dumps([[_iso(start), _iso(end)] for start, end in forecast]),
                    "observed_intervals_utc": json.dumps([[_iso(start), _iso(end)] for start, end in observed]),
                })
                for observation in validity_observations:
                    at = _parse_time(observation["observed_at_utc"])
                    sample_rows.append({
                        "period": period,
                        "system": candidate.system,
                        "issuance_utc": _iso(taf.issue_time),
                        "valid_start_utc": _iso(taf.valid_start),
                        "valid_time_utc": observation["observed_at_utc"],
                        "event": event,
                        "forecast_event": any(start <= at < end for start, end in forecast),
                        "observed_event": _weather_has_event(observation["metar_text"], event),
                        "metar_text": observation["metar_text"],
                    })
                for row in _probability_scores(taf, validity_observations, event):
                    probability_rows.append({"period": period, "system": candidate.system, **row})

    systems: dict[str, Any] = {}
    for system in ("original_human", "machine_rain_50_20"):
        system_ranges = [row for row in range_rows if row["system"] == system]
        systems[system] = {
            "scope": _aggregate_scope(issuances, system),
            "events": _event_summary(episode_rows, sample_rows, probability_rows, system),
            "ranges": _summarize_ranges(system_ranges),
        }
    summary = {
        "period": period,
        "method_version": "taf_native_h1_comparison_v1",
        "candidate": BEST_CONFIG,
        "observation_cadence_minutes": int(cadence.total_seconds() // 60),
        "observation_quality": quality,
        "source_counts": {
            "original_official_candidates": len(human_all),
            "machine_candidates": len(machine_all),
            "original_exact_duplicate_records_deduped": len(human_duplicate_rows),
            "machine_exact_duplicate_records_deduped": len(machine_duplicate_rows),
            "matched_valid_start_candidates": len(common_starts),
            "excluded": dict(exclusions),
        },
        "systems": systems,
    }
    summary["verdict"] = _month_verdict(summary)
    return summary, issuances, episode_rows, sample_rows, probability_rows, range_rows


def _system_table(summary: dict[str, Any], event: str) -> list[list[str]]:
    human = summary["systems"]["original_human"]["events"][event]
    machine = summary["systems"]["machine_rain_50_20"]["events"][event]
    return [
        ["Metric", "Original TAF", "Best config", "Difference"],
        ["Forecast episodes", str(human["episode"]["forecast_episodes"]), str(machine["episode"]["forecast_episodes"]), ""],
        ["Observed episodes", str(human["episode"]["observed_episodes"]), str(machine["episode"]["observed_episodes"]), "same observations"],
        ["Hits / misses / false alarms", f"{human['episode']['hits']} / {human['episode']['misses']} / {human['episode']['false_alarms']}", f"{machine['episode']['hits']} / {machine['episode']['misses']} / {machine['episode']['false_alarms']}", ""],
        ["POD", _percentage(human["episode"]["POD"]), _percentage(machine["episode"]["POD"]), _delta(machine["episode"]["POD"], human["episode"]["POD"])],
        ["FAR", _percentage(human["episode"]["FAR"]), _percentage(machine["episode"]["FAR"]), _delta(machine["episode"]["FAR"], human["episode"]["FAR"], higher_is_better=False)],
        ["CSI", _percentage(human["episode"]["CSI"]), _percentage(machine["episode"]["CSI"]), _delta(machine["episode"]["CSI"], human["episode"]["CSI"])],
        ["Frequency bias", _number(human["episode"]["frequency_bias"]), _number(machine["episode"]["frequency_bias"]), _bias_delta(machine["episode"]["frequency_bias"], human["episode"]["frequency_bias"])],
        ["Sample HSS", _number(human["sample"]["HSS"]), _number(machine["sample"]["HSS"]), _delta(machine["sample"]["HSS"], human["sample"]["HSS"])],
        ["Sample accuracy", _percentage(human["sample"]["accuracy"]), _percentage(machine["sample"]["accuracy"]), _delta(machine["sample"]["accuracy"], human["sample"]["accuracy"])],
        ["PROB groups / Brier", f"{human['probability']['group_count']} / {_number(human['probability']['mean_brier_score'])}", f"{machine['probability']['group_count']} / {_number(machine['probability']['mean_brier_score'])}", "descriptive only"],
    ]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=18, leading=23, textColor=colors.HexColor("#1d3557"), alignment=TA_LEFT),
        "h": ParagraphStyle("h", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=colors.HexColor("#1d3557"), spaceBefore=7, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=8.3, leading=11, textColor=colors.HexColor("#243447")),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName="Helvetica", fontSize=7.1, leading=9, textColor=colors.HexColor("#243447")),
        "header": ParagraphStyle("header", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.white),
        "foot": ParagraphStyle("foot", parent=base["BodyText"], fontName="Helvetica", fontSize=6.5, leading=8, textColor=colors.HexColor("#52616b"), alignment=TA_CENTER),
    }


def _pdf_table(rows: list[list[Any]], widths: list[float], styles: dict[str, ParagraphStyle], *, small: bool = False) -> Table:
    paragraph_style = styles["small"] if small else styles["body"]
    converted = [
        [Paragraph(str(cell), styles["header"] if row_index == 0 else paragraph_style) for cell in row]
        for row_index, row in enumerate(rows)
    ]
    table = Table(converted, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d3557")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d0d8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f7fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _footer(canvas: Any, document: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#52616b"))
    canvas.drawString(14 * mm, 10 * mm, "WAWP experimental TAF-native verification | not operational guidance")
    canvas.drawRightString(283 * mm, 10 * mm, f"Page {document.page}")
    canvas.restoreState()


def _build_month_pdf(summary: dict[str, Any], issuance_rows: list[dict[str, Any]], episode_rows: list[dict[str, Any]], output: Path) -> None:
    styles = _styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(output), pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=16 * mm)
    story: list[Any] = []
    period = summary["period"]
    count = summary["source_counts"]
    story += [
        Paragraph(f"WAWP {period}: Original TAF vs Best Experimental Configuration", styles["title"]),
        Paragraph("Offline, auditable comparison. The machine candidate is frozen from the earlier January-May configuration sweep and is not being promoted by this report.", styles["body"]),
        Spacer(1, 4 * mm),
        Paragraph("Configuration And Reference-Based Interpretation", styles["h"]),
        Paragraph(
            "Machine: <b>raw_asof60 + rain_50_20</b>. Dynamic model weights are recomputed as-of each historical issuance from a preceding 60-day window; empirical QM is disabled; rain requires 50% wet-model probability and 20% agreement with no bridge; thunderstorm policy is broad_current. "
            "TAF semantics: FM becomes prevailing at its stated time; BECMG is a transition and then persists; TEMPO is a deterministic temporary window; PROB30/40 remains probabilistic and is excluded from deterministic POD/FAR/CSI.", styles["body"]),
        Paragraph("Primary event scoring uses exact interval overlap. One forecast episode may match one observed episode only. There is no +/-1 or +/-2 hour grace. This avoids a long warning receiving credit for multiple separate observed episodes.", styles["body"]),
        Paragraph("Reference basis: group-aware TAF verification follows the compatible principles in J. Mahringer, <i>TAF Verification at Austro Control</i> (2008); probability groups are kept separate from deterministic warnings; and rare-event interpretation uses POD, FAR, CSI, HSS, and Brier diagnostics rather than dry-hour accuracy alone. This WAWP implementation is a transparent local adaptation, not an official score standard.", styles["small"]),
        Paragraph("Comparable Scope", styles["h"]),
    ]
    scope_rows = [
        ["Item", "Value"],
        ["Observation cadence", f"{summary['observation_cadence_minutes']} minutes"],
        ["Quality-eligible METAR samples in monthly archive", str(summary["observation_quality"]["eligible_rows"])],
        ["Original official candidates / machine candidates", f"{count['original_official_candidates']} / {count['machine_candidates']}"],
        ["Matched valid-start candidates", str(count["matched_valid_start_candidates"])],
        ["Scored complete-coverage issuances, each system", str(summary["systems"]["original_human"]["scope"]["issuances"])],
        ["Excluded for incomplete observation coverage", str(count["excluded"].get("incomplete_observation_coverage", 0))],
        ["Exact duplicate source records deduped", str(count["original_exact_duplicate_records_deduped"])],
        ["Original source validity repairs in scored scope", str(summary["systems"]["original_human"]["scope"]["validity_repairs"])],
    ]
    story.append(_pdf_table(scope_rows, [85 * mm, 172 * mm], styles))
    for event in ("rain", "thunderstorm"):
        story += [Paragraph(f"{event.title()} Verification", styles["h"]), _pdf_table(_system_table(summary, event), [56 * mm, 69 * mm, 69 * mm, 58 * mm], styles)]
    story += [
        Paragraph("Visibility Range Diagnostic", styles["h"]),
        Paragraph("Visibility is a category-range diagnostic, not an official TAF score. It uses every forecast state that can apply in an hour and all METAR reports available in that hour. Ceiling and gust are intentionally not promoted from these source records.", styles["body"]),
    ]
    visibility = []
    for system, label in (("original_human", "Original TAF"), ("machine_rain_50_20", "Best config")):
        values = summary["systems"][system]["ranges"].get("visibility", {})
        visibility.append([label, values.get("eligible_hours", 0), _percentage(values.get("minimum_category_accuracy")), _percentage(values.get("maximum_category_accuracy")), _percentage(values.get("mean_category_accuracy"))])
    story += [_pdf_table([["System", "Eligible hours", "Min category", "Max category", "Mean"]] + visibility, [60 * mm, 40 * mm, 52 * mm, 52 * mm, 52 * mm], styles), Paragraph("Monthly Verdict", styles["h"]), Paragraph(summary["verdict"], styles["body"]), PageBreak(), Paragraph("Issuance Event Ledger", styles["title"]), Paragraph("The following counts are the calculation inputs behind the monthly primary episode metrics. Full intervals and every sample remain in the accompanying CSV ledgers.", styles["body"]), Spacer(1, 3 * mm)]
    ledger = [["System", "Valid start", "Event", "Forecast eps", "Observed eps", "Hits", "Misses", "False alarms"]]
    for row in sorted(episode_rows, key=lambda item: (item["valid_start_utc"], item["system"], item["event"])):
        ledger.append([
            "Original" if row["system"] == "original_human" else "Best config",
            row["valid_start_utc"].replace("T", " ").replace("Z", ""), row["event"].title(), row["forecast_episodes"], row["observed_episodes"], row["hits"], row["misses"], row["false_alarms"],
        ])
    story.append(_pdf_table(ledger, [29 * mm, 49 * mm, 28 * mm, 30 * mm, 32 * mm, 18 * mm, 22 * mm, 30 * mm], styles, small=True))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _aggregate_months(months: list[dict[str, Any]]) -> dict[str, Any]:
    combined: dict[str, Any] = {"period": "2026-01 to 2026-06", "systems": {}}
    for system in ("original_human", "machine_rain_50_20"):
        events: dict[str, Any] = {}
        for event in ("rain", "thunderstorm"):
            episode = defaultdict(int)
            sample = defaultdict(int)
            probability_components: list[float] = []
            probability_groups = 0
            for month in months:
                source = month["systems"][system]["events"][event]
                for key in ("forecast_episodes", "observed_episodes", "hits", "misses", "false_alarms"):
                    episode[key] += int(source["episode"][key])
                for key in ("hits", "misses", "false_alarms", "correct_negatives"):
                    sample[key] += int(source["sample"][key])
                probability_groups += int(source["probability"]["group_count"])
                if source["probability"]["group_count"] and source["probability"]["mean_brier_score"] is not None:
                    probability_components.extend([float(source["probability"]["mean_brier_score"])] * int(source["probability"]["group_count"]))
            sample_rows = ([{"forecast": True, "observed": True}] * sample["hits"] + [{"forecast": False, "observed": True}] * sample["misses"] + [{"forecast": True, "observed": False}] * sample["false_alarms"] + [{"forecast": False, "observed": False}] * sample["correct_negatives"])
            events[event] = {
                "episode": _event_metrics(dict(episode)),
                "sample": _sample_event_metrics(sample_rows),
                "probability": {"group_count": probability_groups, "mean_brier_score": round(sum(probability_components) / len(probability_components), 4) if probability_components else None},
            }
        combined["systems"][system] = {"events": events, "scope": {"issuances": sum(month["systems"][system]["scope"]["issuances"] for month in months), "sample_count": sum(month["systems"][system]["scope"]["sample_count"] for month in months)}}
    return combined


def _build_summary_pdf(months: list[dict[str, Any]], output: Path) -> None:
    styles = _styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(output), pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=16 * mm)
    all_months = _aggregate_months(months)
    story: list[Any] = [
        Paragraph("WAWP January-June 2026: Original TAF vs Best Experimental Configuration", styles["title"]),
        Paragraph("Comparative offline verification using one common TAF-native calculation strategy. The candidate was selected using January-May development evidence; therefore those months are not independent proof of improvement. June is the independent holdout month in this package.", styles["body"]),
        Paragraph("Monthly Scope And Verdict", styles["h"]),
    ]
    monthly = [["Month", "Cadence", "Comparable issuances", "Rain original CSI", "Rain machine CSI", "Rain original FAR", "Rain machine FAR", "Verdict"]]
    for month in months:
        human = month["systems"]["original_human"]["events"]["rain"]["episode"]
        machine = month["systems"]["machine_rain_50_20"]["events"]["rain"]["episode"]
        monthly.append([month["period"], f"{month['observation_cadence_minutes']} min", month["systems"]["original_human"]["scope"]["issuances"], _percentage(human["CSI"]), _percentage(machine["CSI"]), _percentage(human["FAR"]), _percentage(machine["FAR"]), month["verdict"]])
    story += [_pdf_table(monthly, [22 * mm, 22 * mm, 30 * mm, 27 * mm, 27 * mm, 27 * mm, 27 * mm, 76 * mm], styles, small=True), Paragraph("Combined Descriptive Results", styles["h"])]
    for event in ("rain", "thunderstorm"):
        pseudo = {"systems": all_months["systems"]}
        story += [Paragraph(event.title(), styles["h"]), _pdf_table(_system_table(pseudo, event), [56 * mm, 69 * mm, 69 * mm, 58 * mm], styles)]
    human_rain = all_months["systems"]["original_human"]["events"]["rain"]["episode"]
    machine_rain = all_months["systems"]["machine_rain_50_20"]["events"]["rain"]["episode"]
    verdict = (
        "<b>Verdict: do not promote this candidate from the combined January-June result.</b> January-May informed the candidate selection, so their apparent performance is development evidence rather than an independent test. More importantly, the candidate's greater rain detection is accompanied by a much larger false-alarm burden and forecast frequency bias. June is independent but only one month. The correct next state is to keep this configuration in shadow as a sensitivity reference, retune the rain gate against false alarms, and verify on later untouched months using the same complete-coverage and event-episode rules."
    )
    if machine_rain["CSI"] is not None and human_rain["CSI"] is not None:
        verdict += f" Across the combined descriptive scope, rain CSI is {_percentage(human_rain['CSI'])} for the original TAF and {_percentage(machine_rain['CSI'])} for the machine candidate; this difference is reported, not treated as operational promotion evidence."
    story += [Paragraph("Decision Guardrail", styles["h"]), Paragraph(verdict, styles["body"]), Paragraph("Calculation Strategy", styles["h"]), Paragraph("Both systems were parsed from their original TAF text. Prevailing states come from the base forecast plus completed FM/BECMG changes. TEMPO creates a deterministic temporary warning interval. PROB30/PROB40 stays probabilistic and contributes only Brier components. For rain and thunderstorm, episode POD = hits / (hits + misses), FAR = false alarms / (hits + false alarms), and CSI = hits / (hits + misses + false alarms). A forecast and observed episode are a hit only if their reported windows overlap; no timing displacement allowance is applied. Each forecast episode can count once. Dry-hour accuracy is retained as a diagnostic but never used as the rare-event winner.", styles["body"]), Paragraph("Reference And Limitations", styles["h"]), Paragraph("This is a transparent WAWP adaptation of compatible, group-aware principles in J. Mahringer, <i>TAF Verification at Austro Control</i> (2008), not an official ICAO, WMO, or regulator score. January-April METARs are hourly; May has one excluded non-hourly record; June is half-hourly. A TAF is scored only with complete cadence coverage. Rain and thunderstorm use explicit METAR weather text, so the March/May rainfall-amount timestamp issue does not redefine these event metrics.", styles["body"])]
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def run(output_dir: Path, pdf_dir: Path) -> dict[str, Any]:
    """Create all per-month audit ledgers, PDFs, and the Jan-June summary PDF."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    monthly: list[dict[str, Any]] = []
    all_issuances: list[dict[str, Any]] = []
    all_episodes: list[dict[str, Any]] = []
    all_samples: list[dict[str, Any]] = []
    all_probability_rows: list[dict[str, Any]] = []
    all_range_rows: list[dict[str, Any]] = []
    for period in _periods():
        summary, issuances, episodes, samples, probability_rows, range_rows = _score_month(period)
        monthly.append(summary)
        all_issuances.extend(issuances)
        all_episodes.extend(episodes)
        all_samples.extend(samples)
        all_probability_rows.extend(probability_rows)
        all_range_rows.extend(range_rows)
        (output_dir / f"{period}_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        _build_month_pdf(summary, issuances, episodes, pdf_dir / f"WAWP_{period.replace('-', '_')}_Original_vs_Rain_50_20_TAF_Native_Comparison.pdf")
    _write_csv(output_dir / "h1_native_issuance_scope.csv", all_issuances)
    _write_csv(output_dir / "h1_native_event_episodes.csv", all_episodes)
    _write_csv(output_dir / "h1_native_event_samples.csv", all_samples)
    _write_csv(output_dir / "h1_native_probability_groups.csv", all_probability_rows)
    _write_csv(output_dir / "h1_native_visibility_ranges.csv", all_range_rows)
    payload = {"method_version": "taf_native_h1_comparison_v1", "candidate": BEST_CONFIG, "months": monthly, "combined": _aggregate_months(monthly)}
    (output_dir / "h1_native_jan_jun_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    summary_pdf = pdf_dir / "WAWP_Jan_Jun_2026_Original_vs_Rain_50_20_TAF_Native_Summary.pdf"
    _build_summary_pdf(monthly, summary_pdf)
    return {"output_dir": str(output_dir), "pdf_dir": str(pdf_dir), "summary_pdf": str(summary_pdf), "months": monthly}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "taf_native_h1_best_config_comparison_2026")
    parser.add_argument("--pdf-dir", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\meteologix-wawp-main\output\pdf"))
    args = parser.parse_args()
    result = run(args.output_dir, args.pdf_dir)
    print(json.dumps({"output_dir": result["output_dir"], "summary_pdf": result["summary_pdf"]}, indent=2))


if __name__ == "__main__":
    main()
