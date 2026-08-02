"""Mahringer-style verification of January-June 2026 original WAWP TAFs.

This offline audit uses half-hourly METARs plus SPECIs. It converts each TAF
validity hour and each observation hour into ranges, then compares the minimum
and maximum categorical states. It is intentionally separate from the older
strict episode-overlap experiment.
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

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src import taf_native_h1_comparison as h1
from src.legacy_taf_verification import HEADER_RE, WeatherState
from src.taf_native_verification import GROUP_RE, NativeTAF, _hour_state_candidates, parse_native_taf


UTC = timezone.utc
PERIODS = tuple(f"2026-{month:02d}" for month in range(1, 7))
VISIBILITY_THRESHOLDS = (150, 350, 600, 800, 1500, 3000, 5000)
CEILING_THRESHOLDS = (100, 200, 500, 1000, 1500)
WIND_SPEED_THRESHOLDS = (7, 15, 25, 35, 45, 55)
WIND_RE = re.compile(r"^(?P<direction>\d{3}|VRB)(?P<speed>\d{2})(?:G(?P<gust>\d{2,3}))?KT$")
CLOUD_RE = re.compile(r"^(?P<amount>FEW|SCT|BKN|OVC)(?P<base>\d{3})(?:CB|TCU)?$")
VV_RE = re.compile(r"^VV(?P<base>\d{3})$")
REPORT_RE = re.compile(
    r"^(?:METAR|SPECI)(?:\s+COR)?\s+WAWP\s+\d{6}Z\s+(?P<body>.+?)=?$"
)
CURRENT_END_RE = re.compile(r"\s+(?:NOSIG|BECMG|TEMPO|RMK)\b")
MALFORMED_CLOUD_RE = re.compile(r"\b(?:FEW|SCT|BKN|OVC)\d{2}(?:CB|TCU)?\b")
DEFAULT_OBSERVATIONS = Path(
    r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\metar_standalone\mahringer_2026\metar_wawp_2026_h1.csv"
)
DEFAULT_OUTPUT = Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_mahringer_verification_2026")
DEFAULT_PDF = Path(r"D:\UJI_PERFORMA_MODEL\meteologix-wawp-main\output\pdf\WAWP_Jan_Jun_2026_Mahringer_Verification_Audit.pdf")
OLD_SUMMARY = Path(
    r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_native_h1_best_config_comparison_2026\h1_native_jan_jun_summary.json"
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _category(value: int | None, thresholds: tuple[int, ...]) -> int | None:
    if value is None:
        return None
    for index, threshold in enumerate(thresholds):
        if value < threshold:
            return index
    return len(thresholds)


def _weather_tokens(text: str) -> list[str]:
    return [token.upper().strip("=") for token in str(text or "").split()]


def _token_has_rain(token: str) -> bool:
    core = token.lstrip("+-")
    return "RA" in core


def _token_has_thunderstorm(token: str) -> bool:
    core = token.lstrip("+-")
    return core.startswith("TS") or core.startswith("VCTS") or core.startswith("RETS")


def _significant_weather_class(text: str) -> int:
    """Return the highest Mahringer/Austro Control present-weather class."""
    classes = [0]
    for raw in _weather_tokens(text):
        token = raw.lstrip("+-")
        if _token_has_thunderstorm(raw) or token in {"SQ", "FC", "+FC"}:
            classes.append(6)
        elif "FZRA" in token or "FZDZ" in token:
            classes.append(5)
        elif any(code in token for code in ("SN", "SG", "PL", "GR", "GS")):
            classes.append(4)
        elif token in {"BLSN", "DRSN"}:
            classes.append(3)
        elif not raw.startswith("-") and (
            "RA" in token or "DZ" in token or token in {"SH", "VCSH", "RERA", "REDZ"}
        ):
            classes.append(2)
        elif "FZFG" in token:
            classes.append(1)
    return max(classes)


def _visibility(tokens: list[str]) -> int | None:
    if "CAVOK" in tokens:
        return 9999
    for token in tokens:
        if re.fullmatch(r"\d{4}", token):
            return int(token)
    return None


def _ceiling(tokens: list[str]) -> int | None:
    if "CAVOK" in tokens or any(token in {"NSC", "NCD", "SKC"} for token in tokens):
        return 99999
    if "//////" in tokens:
        return None
    bases: list[int] = []
    cloud_field_seen = False
    for token in tokens:
        match = CLOUD_RE.match(token)
        if match:
            cloud_field_seen = True
            if match.group("amount") in {"BKN", "OVC"}:
                bases.append(int(match.group("base")) * 100)
        vertical = VV_RE.match(token)
        if vertical:
            cloud_field_seen = True
            bases.append(int(vertical.group("base")) * 100)
    if bases:
        return min(bases)
    return 99999 if cloud_field_seen else None


def _wind(tokens: list[str]) -> tuple[int | None, int | None, int | None]:
    for token in tokens:
        match = WIND_RE.match(token)
        if match:
            direction = None if match.group("direction") == "VRB" else int(match.group("direction"))
            return direction, int(match.group("speed")), int(match.group("gust")) if match.group("gust") else None
    return None, None, None


def parse_observation(row: dict[str, str]) -> dict[str, Any]:
    text = str(row["metar_text"]).strip()
    match = REPORT_RE.match(text)
    if not match:
        raise ValueError(f"Malformed canonical METAR/SPECI: {text!r}")
    current = CURRENT_END_RE.split(match.group("body"), maxsplit=1)[0]
    tokens = _weather_tokens(current)
    direction, speed, gust = _wind(tokens)
    visibility = _visibility(tokens)
    ceiling = _ceiling(tokens)
    explicit_weather = any(
        _token_has_rain(token) or _token_has_thunderstorm(token) or _significant_weather_class(token) > 0
        for token in tokens
    )
    weather_usable = explicit_weather or (
        visibility is not None and ceiling is not None and "////" not in tokens and "//////" not in tokens
    )
    return {
        **row,
        "observed_at": datetime.fromisoformat(row["observed_at_utc"].replace("Z", "+00:00")).astimezone(UTC),
        "current_text": current,
        "wind_direction": direction,
        "wind_speed": speed,
        "wind_gust": gust,
        "visibility": visibility,
        "ceiling": ceiling,
        "weather_class": _significant_weather_class(current) if weather_usable else None,
        "any_rain": any(_token_has_rain(token) for token in tokens) if weather_usable else None,
        "thunderstorm": any(_token_has_thunderstorm(token) for token in tokens) if weather_usable else None,
        "weather_usable": weather_usable,
    }


def load_observations(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return [parse_observation(row) for row in csv.DictReader(handle)]


def _state_ceiling(state: WeatherState) -> int | None:
    if state.cloud_amount in {"BKN", "OVC", "VV"} and state.cloud_base_ft is not None:
        return state.cloud_base_ft
    if state.cloud_amount is not None:
        return 99999
    return None


def _clause_ceiling(text: str) -> tuple[bool, int | None]:
    tokens = _weather_tokens(text)
    has_cloud = "CAVOK" in tokens or any(
        CLOUD_RE.match(token) or VV_RE.match(token) or token in {"NSC", "NCD", "SKC"}
        for token in tokens
    )
    return has_cloud, _ceiling(tokens) if has_cloud else None


def _taf_raw_clauses(taf: NativeTAF) -> tuple[str, list[str]]:
    matches = list(GROUP_RE.finditer(taf.text))
    header = HEADER_RE.search(taf.text)
    if not header:
        raise ValueError(f"Malformed TAF: {taf.text!r}")
    base_end = matches[0].start() if matches else len(taf.text)
    base = taf.text[header.end():base_end]
    groups = [
        taf.text[match.end():(matches[index + 1].start() if index + 1 < len(matches) else len(taf.text))]
        for index, match in enumerate(matches)
    ]
    return base, groups


def _taf_ceiling_candidates(taf: NativeTAF, hour: datetime) -> list[int]:
    base_raw, group_raw = _taf_raw_clauses(taf)
    _, base_ceiling = _clause_ceiling(base_raw)

    def prevailing(at: datetime) -> int | None:
        value = base_ceiling
        for group, raw in zip(taf.groups, group_raw):
            if (group.kind == "FM" and at >= group.start) or (group.kind == "BECMG" and at >= group.end):
                supplied, candidate = _clause_ceiling(raw)
                if supplied:
                    value = candidate
        return value

    end = hour + timedelta(hours=1)
    candidates = [prevailing(hour), prevailing(end - timedelta(seconds=1))]
    for group, raw in zip(taf.groups, group_raw):
        if hour < group.end and group.start < end:
            supplied, candidate = _clause_ceiling(raw)
            if supplied:
                candidates.append(candidate)
    return [value for value in candidates if value is not None]


def _direction_difference(left: int, right: int) -> int:
    difference = abs(left - right) % 360
    return min(difference, 360 - difference)


def _lead_bucket(lead_hours: int) -> str:
    if lead_hours <= 6:
        return "L1_1_6h"
    if lead_hours <= 12:
        return "L2_7_12h"
    if lead_hours <= 18:
        return "L3_13_18h"
    return "L4_19_24h"


def _binary_metrics(rows: Iterable[dict[str, Any]], forecast_key: str, observed_key: str) -> dict[str, Any]:
    hits = misses = false_alarms = correct_negatives = 0
    for row in rows:
        forecast = bool(row[forecast_key])
        observed = bool(row[observed_key])
        if forecast and observed:
            hits += 1
        elif forecast:
            false_alarms += 1
        elif observed:
            misses += 1
        else:
            correct_negatives += 1
    total = hits + misses + false_alarms + correct_negatives
    random_correct = (
        ((hits + misses) * (hits + false_alarms))
        + ((correct_negatives + misses) * (correct_negatives + false_alarms))
    ) / total if total else 0.0
    return {
        "sample_size": total,
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_negatives": correct_negatives,
        "POD": round(hits / (hits + misses), 4) if hits + misses else None,
        "FAR": round(false_alarms / (hits + false_alarms), 4) if hits + false_alarms else None,
        "CSI": round(hits / (hits + misses + false_alarms), 4) if hits + misses + false_alarms else None,
        "frequency_bias": round((hits + false_alarms) / (hits + misses), 4) if hits + misses else None,
        "accuracy": round((hits + correct_negatives) / total, 4) if total else None,
        "HSS": round((hits + correct_negatives - random_correct) / (total - random_correct), 4)
        if total and total != random_correct else None,
    }


def _class_metrics(rows: list[dict[str, Any]], side: str, target: int) -> dict[str, Any]:
    prepared = [
        {"forecast": row[f"forecast_weather_{side}"] == target, "observed": row[f"observed_weather_{side}"] == target}
        for row in rows
    ]
    return _binary_metrics(prepared, "forecast", "observed")


def _range_accuracy(rows: list[dict[str, Any]], element: str) -> dict[str, Any]:
    relevant = [row for row in rows if row.get(f"{element}_eligible")]
    minimum_hits = sum(row[f"forecast_{element}_min"] == row[f"observed_{element}_min"] for row in relevant)
    maximum_hits = sum(row[f"forecast_{element}_max"] == row[f"observed_{element}_max"] for row in relevant)
    return {
        "eligible_hours": len(relevant),
        "minimum_category_accuracy": round(minimum_hits / len(relevant), 4) if relevant else None,
        "maximum_category_accuracy": round(maximum_hits / len(relevant), 4) if relevant else None,
        "mean_category_accuracy": round((minimum_hits + maximum_hits) / (2 * len(relevant)), 4) if relevant else None,
    }


def _human_candidates(period: str) -> tuple[list[h1.Candidate], int]:
    unique, duplicates = h1._unique_by_valid_start(h1._human_candidates(period))
    return list(unique.values()), len(duplicates)


def _taf_syntax_issues(taf: NativeTAF) -> list[str]:
    issues: list[str] = []
    validity_hours = int((taf.valid_end - taf.valid_start).total_seconds() // 3600)
    if validity_hours != 24:
        issues.append(f"validity_hours_{validity_hours}")
    if MALFORMED_CLOUD_RE.search(taf.text):
        issues.append("malformed_cloud_height")
    for group in taf.groups:
        if group.start < taf.valid_start or group.start >= taf.valid_end or group.end > taf.valid_end:
            issues.append(f"{group.kind.lower()}_outside_validity")
    for index, left in enumerate(taf.groups):
        for right in taf.groups[index + 1:]:
            if left.kind == right.kind and left.start < right.end and right.start < left.end:
                issues.append(f"overlapping_{left.kind.lower()}_groups")
    return sorted(set(issues))


def build_hour_rows(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_hour: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        by_hour[observation["observed_at"].replace(minute=0, second=0, microsecond=0)].append(observation)

    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for period in PERIODS:
        candidates, duplicate_count = _human_candidates(period)
        if duplicate_count:
            exclusions.append({"period": period, "reason": "duplicate_taf_source", "count": duplicate_count})
        for candidate in candidates:
            if candidate.validity_repaired:
                exclusions.append({
                    "period": period,
                    "reason": "invalid_validity_repaired_excluded",
                    "source_id": candidate.source_id,
                    "taf": candidate.taf_text,
                })
                continue
            try:
                taf = parse_native_taf(candidate.taf_text, candidate.parse_period)
            except ValueError as error:
                exclusions.append({"period": period, "reason": "taf_parse_error", "source_id": candidate.source_id, "detail": str(error)})
                continue
            validity_hours = int((taf.valid_end - taf.valid_start).total_seconds() // 3600)
            syntax_issues = _taf_syntax_issues(taf)
            if syntax_issues:
                exclusions.append({
                    "period": period,
                    "reason": "invalid_taf_syntax_excluded",
                    "source_id": candidate.source_id,
                    "validity_hours": validity_hours,
                    "syntax_issues": ";".join(syntax_issues),
                    "taf": taf.text,
                })
                continue
            hour = taf.valid_start
            while hour < taf.valid_end:
                reports = by_hour.get(hour, [])
                states = _hour_state_candidates(taf, hour)
                lead_hours = int((hour - taf.issue_time).total_seconds() // 3600)
                row: dict[str, Any] = {
                    "period": period,
                    "source_id": candidate.source_id,
                    "issuance_utc": _iso(taf.issue_time),
                    "valid_hour_utc": _iso(hour),
                    "lead_hours": lead_hours,
                    "lead_bucket": _lead_bucket(lead_hours),
                    "observation_reports": len(reports),
                    "taf": taf.text,
                }

                weather_values = [report["weather_class"] for report in reports if report["weather_class"] is not None]
                if len(weather_values) >= 2:
                    forecast_weather = [_significant_weather_class(state.weather or "") for state in states]
                    row.update({
                        "weather_eligible": True,
                        "forecast_weather_min": min(forecast_weather),
                        "forecast_weather_max": max(forecast_weather),
                        "observed_weather_min": min(weather_values),
                        "observed_weather_max": max(weather_values),
                        "forecast_any_rain": any(any(_token_has_rain(token) for token in _weather_tokens(state.weather or "")) for state in states),
                        "observed_any_rain": any(bool(report["any_rain"]) for report in reports if report["any_rain"] is not None),
                        "forecast_thunderstorm": any(any(_token_has_thunderstorm(token) for token in _weather_tokens(state.weather or "")) for state in states),
                        "observed_thunderstorm": any(bool(report["thunderstorm"]) for report in reports if report["thunderstorm"] is not None),
                    })
                else:
                    row["weather_eligible"] = False

                for element, field, thresholds in (
                    ("visibility", "visibility", VISIBILITY_THRESHOLDS),
                    ("ceiling", "ceiling", CEILING_THRESHOLDS),
                    ("wind_speed", "wind_speed", WIND_SPEED_THRESHOLDS),
                ):
                    observed_values = [report[field] for report in reports if report[field] is not None]
                    if element == "visibility":
                        forecast_values = [state.visibility_m for state in states if state.visibility_m is not None]
                    elif element == "ceiling":
                        forecast_values = _taf_ceiling_candidates(taf, hour)
                    else:
                        forecast_values = [state.wind_speed for state in states if state.wind_speed is not None]
                    eligible = len(observed_values) >= 2 and bool(forecast_values)
                    row[f"{element}_eligible"] = eligible
                    if eligible:
                        observed_categories = [_category(value, thresholds) for value in observed_values]
                        forecast_categories = [_category(value, thresholds) for value in forecast_values]
                        row.update({
                            f"forecast_{element}_min": min(forecast_categories),
                            f"forecast_{element}_max": max(forecast_categories),
                            f"observed_{element}_min": min(observed_categories),
                            f"observed_{element}_max": max(observed_categories),
                        })

                direction_observations = [report for report in reports if report["wind_speed"] is not None]
                direction_scores: list[bool] = []
                forecast_directions = [state.wind_direction for state in states if not state.wind_is_variable and state.wind_direction is not None]
                forecast_has_vrb = any(state.wind_is_variable for state in states)
                for report in direction_observations:
                    if report["wind_speed"] < 7:
                        direction_scores.append(True)
                    elif report["wind_direction"] is not None:
                        differences = [_direction_difference(report["wind_direction"], value) for value in forecast_directions]
                        if forecast_has_vrb:
                            differences.append(180)
                        if differences:
                            direction_scores.append(min(differences) <= 30)
                row["wind_direction_eligible"] = len(direction_scores) >= 2
                if row["wind_direction_eligible"]:
                    row["wind_direction_correct_fraction"] = round(sum(direction_scores) / len(direction_scores), 4)
                rows.append(row)
                hour += timedelta(hours=1)
    return rows, exclusions


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    weather = [row for row in rows if row.get("weather_eligible")]
    summary = {
        "candidate_taf_hours": len(rows),
        "weather_eligible_hours": len(weather),
        "weather_coverage_percent": round(100 * len(weather) / len(rows), 2) if rows else 0.0,
        "any_rain_hourly_range": _binary_metrics(weather, "forecast_any_rain", "observed_any_rain"),
        "thunderstorm_hourly_range": _binary_metrics(weather, "forecast_thunderstorm", "observed_thunderstorm"),
        "mahringer_weather_classes": {
            "rain_class_2_minimum": _class_metrics(weather, "min", 2),
            "rain_class_2_maximum": _class_metrics(weather, "max", 2),
            "thunderstorm_class_6_minimum": _class_metrics(weather, "min", 6),
            "thunderstorm_class_6_maximum": _class_metrics(weather, "max", 6),
        },
        "range_accuracy": {
            element: _range_accuracy(rows, element)
            for element in ("visibility", "ceiling", "wind_speed")
        },
    }
    direction = [row["wind_direction_correct_fraction"] for row in rows if row.get("wind_direction_eligible")]
    summary["wind_direction"] = {
        "eligible_hours": len(direction),
        "mean_correct_fraction": round(sum(direction) / len(direction), 4) if direction else None,
        "rule": "Direction deviation <=30 degrees when observed wind >=7 kt; lower observed speeds count as operationally correct.",
    }
    summary["lead_buckets"] = {
        bucket: {
            "eligible_hours": len(selected),
            "any_rain": _binary_metrics(selected, "forecast_any_rain", "observed_any_rain"),
            "thunderstorm": _binary_metrics(selected, "forecast_thunderstorm", "observed_thunderstorm"),
        }
        for bucket in ("L1_1_6h", "L2_7_12h", "L3_13_18h", "L4_19_24h")
        for selected in [[row for row in weather if row["lead_bucket"] == bucket]]
    }
    return summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row}) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{100 * float(value):.1f}%"


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=colors.HexColor("#17324D"), alignment=TA_CENTER, spaceAfter=8),
        "subtitle": ParagraphStyle("subtitle", parent=base["BodyText"], fontSize=9, leading=13, alignment=TA_CENTER, textColor=colors.HexColor("#50657A"), spaceAfter=12),
        "h": ParagraphStyle("h", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor("#17324D"), spaceBefore=8, spaceAfter=5),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=8.5, leading=12, textColor=colors.HexColor("#243447"), spaceAfter=5),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontSize=7, leading=9, textColor=colors.HexColor("#50657A")),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontSize=7, leading=8.5, textColor=colors.HexColor("#243447")),
        "header": ParagraphStyle("header", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7, leading=8.5, textColor=colors.white),
    }


def _table(rows: list[list[Any]], widths: list[float], styles: dict[str, ParagraphStyle]) -> Table:
    wrapped = [
        [Paragraph(str(value), styles["header"] if row_index == 0 else styles["cell"]) for value in row]
        for row_index, row in enumerate(rows)
    ]
    table = Table(wrapped, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D2DC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _footer(canvas: Any, document: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#6B7C8F"))
    canvas.drawString(14 * mm, 8 * mm, "WAWP experimental verification - not an official operational score")
    canvas.drawRightString(landscape(A4)[0] - 14 * mm, 8 * mm, f"Page {document.page}")
    canvas.restoreState()


def _build_pdf(payload: dict[str, Any], output: Path) -> None:
    styles = _styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output), pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=15 * mm)
    combined = payload["combined"]
    old = payload.get("previous_strict_episode_result", {})
    story: list[Any] = [
        Paragraph("WAWP January-June 2026 Mahringer Verification Audit", styles["title"]),
        Paragraph("Original human TAFs verified against the new half-hourly METAR plus SPECI workbook", styles["subtitle"]),
        Paragraph("Executive Finding", styles["h"]),
        Paragraph(
            f"The previously reported rain POD of {_pct(old.get('POD'))} was arithmetically correct for a strict one-to-one episode-overlap experiment, but it was not the Mahringer method. Under the Mahringer-style hourly range calculation, broad rain-bearing condition POD is <b>{_pct(combined['any_rain_hourly_range']['POD'])}</b> over {combined['any_rain_hourly_range']['sample_size']:,} quality-eligible forecast hours. These values answer different questions and must not be mixed.",
            styles["body"],
        ),
        Paragraph("Method Applied", styles["h"]),
        Paragraph(
            "Each valid TAF hour is expanded into all conditions that can apply from the prevailing forecast, FM, BECMG, TEMPO, PROB and PROB TEMPO groups. Each observation hour uses the METAR at the hour, the 30-minute METAR and intervening SPECIs. At least two usable observations are required for each element. Separate minimum and maximum categorical comparisons are produced. Incorrect-validity TAFs that required repair are excluded from the primary score.",
            styles["body"],
        ),
        Paragraph("Monthly Rain And Thunderstorm Detection", styles["h"]),
    ]
    monthly_rows = [["Month", "Candidate hours", "Wx eligible", "Coverage", "Rain POD", "Rain FAR", "Rain CSI", "TS POD", "TS FAR", "TS CSI"]]
    for period in PERIODS:
        item = payload["months"][period]
        rain = item["any_rain_hourly_range"]
        storm = item["thunderstorm_hourly_range"]
        monthly_rows.append([
            period, item["candidate_taf_hours"], item["weather_eligible_hours"], f"{item['weather_coverage_percent']:.1f}%",
            _pct(rain["POD"]), _pct(rain["FAR"]), _pct(rain["CSI"]), _pct(storm["POD"]), _pct(storm["FAR"]), _pct(storm["CSI"]),
        ])
    story += [_table(monthly_rows, [22*mm, 25*mm, 22*mm, 20*mm, 20*mm, 20*mm, 20*mm, 20*mm, 20*mm, 20*mm], styles), Spacer(1, 4*mm)]
    broad = [["Metric", "Broad rain-bearing conditions", "Thunderstorm"]]
    for label, key in (("Eligible hours", "sample_size"), ("Hits", "hits"), ("Misses", "misses"), ("False alarms", "false_alarms"), ("POD", "POD"), ("FAR", "FAR"), ("CSI", "CSI"), ("HSS", "HSS")):
        rain_value = combined["any_rain_hourly_range"][key]
        storm_value = combined["thunderstorm_hourly_range"][key]
        broad.append([label, _pct(rain_value) if key in {"POD", "FAR", "CSI", "HSS"} else rain_value, _pct(storm_value) if key in {"POD", "FAR", "CSI", "HSS"} else storm_value])
    story += [Paragraph("Combined Hourly Diagnostic", styles["h"]), _table(broad, [60*mm, 78*mm, 78*mm], styles)]
    story += [PageBreak(), Paragraph("Paper-Aligned Minimum And Maximum Weather Classes", styles["h"])]
    class_rows = [["Class/table", "Samples", "Hits", "Misses", "False alarms", "POD", "FAR", "CSI", "HSS"]]
    for name, metric in combined["mahringer_weather_classes"].items():
        class_rows.append([name.replace("_", " "), metric["sample_size"], metric["hits"], metric["misses"], metric["false_alarms"], _pct(metric["POD"]), _pct(metric["FAR"]), _pct(metric["CSI"]), _pct(metric["HSS"])])
    story += [_table(class_rows, [52*mm, 22*mm, 18*mm, 20*mm, 25*mm, 20*mm, 20*mm, 20*mm, 20*mm], styles)]
    story += [Paragraph("Other TAF Elements", styles["h"])]
    element_rows = [["Element", "Eligible hours", "Minimum category accuracy", "Maximum category accuracy", "Mean category accuracy"]]
    for element, metric in combined["range_accuracy"].items():
        element_rows.append([element.replace("_", " "), metric["eligible_hours"], _pct(metric["minimum_category_accuracy"]), _pct(metric["maximum_category_accuracy"]), _pct(metric["mean_category_accuracy"])])
    direction = combined["wind_direction"]
    element_rows.append(["wind direction", direction["eligible_hours"], "n/a", "n/a", _pct(direction["mean_correct_fraction"])])
    story += [_table(element_rows, [48*mm, 30*mm, 47*mm, 47*mm, 43*mm], styles)]
    story += [Paragraph("Lead-Time Rain Skill", styles["h"])]
    lead_rows = [["Lead bucket", "Eligible hours", "Hits", "Misses", "False alarms", "POD", "FAR", "CSI"]]
    for bucket, data in combined["lead_buckets"].items():
        metric = data["any_rain"]
        lead_rows.append([bucket, data["eligible_hours"], metric["hits"], metric["misses"], metric["false_alarms"], _pct(metric["POD"]), _pct(metric["FAR"]), _pct(metric["CSI"])])
    story += [_table(lead_rows, [37*mm, 30*mm, 22*mm, 22*mm, 30*mm, 24*mm, 24*mm, 24*mm], styles)]
    story += [
        Paragraph("Coverage And Interpretation", styles["h"]),
        Paragraph(
            "March is not a complete verification month: its routine METAR column ends at 5 March 03:00 UTC. April and May are distinct in the new workbook and no longer inherit the previous duplicate-month problem. Weather, visibility and ceiling scores use only hours with two usable observations; AUTO reports containing //// and ////// are unknown for those elements, not dry or CAVOK. Wind gust is not promoted because Mahringer requires continuous or suitable maximum-gust evidence.",
            styles["body"],
        ),
        Paragraph("Verdict", styles["h"]),
        Paragraph(
            "The statement that the forecaster achieved only about 6% rain POD across six months is not valid as a Mahringer conclusion. It describes the older strict episode matcher on a compromised observation archive. The hourly range result in this report is the appropriate Mahringer-style diagnostic for this workbook, subject to the stated March and AUTO-sensor coverage limits.",
            styles["body"],
        ),
        Paragraph("Reference", styles["h"]),
        Paragraph("G. Mahringer (2008), Terminal aerodrome forecast verification in Austro Control using time windows and ranges of forecast conditions, Meteorological Applications 15, 113-123, DOI 10.1002/met.62.", styles["small"]),
    ]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def run(observations_path: Path, output_dir: Path, pdf_path: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    observations = load_observations(observations_path)
    hour_rows, exclusions = build_hour_rows(observations)
    months = {period: _summarize([row for row in hour_rows if row["period"] == period]) for period in PERIODS}
    combined = _summarize(hour_rows)
    previous: dict[str, Any] = {}
    if OLD_SUMMARY.exists():
        old_payload = json.loads(OLD_SUMMARY.read_text(encoding="utf-8"))
        previous = old_payload["combined"]["systems"]["original_human"]["events"]["rain"]["episode"]
    payload = {
        "method_version": "wawp_mahringer_hourly_range_v1",
        "scope": "Original human WAWP TAFs, January-June 2026; offline experimental verification",
        "observation_source": str(observations_path),
        "observation_reports": len(observations),
        "previous_strict_episode_result": previous,
        "months": months,
        "combined": combined,
        "exclusions": exclusions,
        "method_notes": [
            "TAF FM, BECMG, TEMPO, PROB and PROB TEMPO conditions form the forecast range for each hour.",
            "Two usable observations per element-hour are required.",
            "Significant weather classes follow Mahringer Table I; light rain remains class 0 but is included in the separate broad any-rain diagnostic.",
            "Wind gust is not scored without continuous or suitable maximum-gust observations.",
            "Repaired invalid TAF validity periods are excluded from the primary result.",
        ],
    }
    _write_csv(output_dir / "mahringer_hourly_ledger.csv", hour_rows)
    _write_csv(output_dir / "mahringer_exclusions.csv", exclusions)
    (output_dir / "mahringer_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _build_pdf(payload, pdf_path)
    return {"summary": str(output_dir / "mahringer_summary.json"), "ledger": str(output_dir / "mahringer_hourly_ledger.csv"), "pdf": str(pdf_path), "combined": combined}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    args = parser.parse_args()
    print(json.dumps(run(args.observations, args.output_dir, args.pdf), indent=2))


if __name__ == "__main__":
    main()
