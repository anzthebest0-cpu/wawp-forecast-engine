"""Create detailed PDF reports comparing replay configurations with original TAFs."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


UTC = timezone.utc
METRICS = (
    "wind_direction",
    "wind_speed",
    "wind_gust",
    "visibility",
    "rain_occurrence",
    "cloud_amount",
    "cloud_base",
)
CORE_METRICS = ("wind_direction", "wind_speed", "wind_gust", "rain_occurrence")
PERIODS = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _is_true(value: Any) -> bool:
    return str(value).lower() == "true"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_start_from_replay(row: dict[str, str]) -> str:
    issue = datetime.strptime(row["issuance_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return (issue + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def _score_rows(rows: list[dict[str, str]], mode: str = "quality_gated") -> dict[str, Any]:
    metric_scores: dict[str, float | None] = {}
    for metric in METRICS:
        values = [
            value for row in rows
            if (value := _to_float(row.get(f"{mode}_{metric}_legacy_score_percent"))) is not None
        ]
        metric_scores[metric] = sum(values) / len(values) if values else None
    all_values = [metric_scores[metric] for metric in METRICS if metric_scores[metric] is not None]
    core_values = [metric_scores[metric] for metric in CORE_METRICS if metric_scores[metric] is not None]
    return {
        "taf_count": len(rows),
        "metrics": metric_scores,
        "all_elements": sum(all_values) / len(all_values) if all_values else None,
        "core": sum(core_values) / len(core_values) if core_values else None,
    }


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def _format_delta(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f} pp"


def _config_label(name: str) -> str:
    return name.replace("prior_regime_capped", "prior cap").replace("prior_regime", "prior regime").replace("prior_global", "prior global").replace("raw_asof", "raw as-of").replace("raw_equal", "raw equal")


def _load_analysis(root: Path) -> dict[str, Any]:
    replay_dir = root / "VERIFICATION_REPORTS" / "taf_replay_multiconfig_verification_2026"
    original_dir = root / "VERIFICATION_REPORTS" / "original_taf_structured_baseline_2026"
    replay_rows = _read_csv(replay_dir / "taf_replay_multiconfig_issuance.csv")
    original_rows_raw = _read_csv(original_dir / "historical_preconfigured_taf_issuance.csv")
    replay_summary = json.loads((replay_dir / "taf_replay_multiconfig_summary.json").read_text(encoding="utf-8"))
    original_summary = json.loads((original_dir / "historical_preconfigured_taf_monthly.json").read_text(encoding="utf-8"))

    original_by_start: dict[str, dict[str, str]] = {}
    for row in original_rows_raw:
        original_by_start.setdefault(row["taf_valid_start_utc"], row)
    replay_by_config: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in replay_rows:
        row["valid_start_utc"] = _valid_start_from_replay(row)
        replay_by_config[row["configuration"]].append(row)
    replay_starts = {row["valid_start_utc"] for row in replay_rows}
    common_starts = set(original_by_start) & replay_starts
    original_common_rows = [original_by_start[key] for key in sorted(common_starts)]

    configs = sorted(replay_by_config)
    all_available = {config: _score_rows(rows) for config, rows in replay_by_config.items()}
    common = {
        config: _score_rows([row for row in rows if row["valid_start_utc"] in common_starts])
        for config, rows in replay_by_config.items()
    }
    original_all = _score_rows(list(original_by_start.values()))
    original_common = _score_rows(original_common_rows)
    monthly: dict[str, dict[str, dict[str, Any]]] = {}
    for period in PERIODS:
        original_month_rows = [row for row in original_common_rows if row["period"] == period]
        monthly[period] = {"original": _score_rows(original_month_rows)}
        for config, rows in replay_by_config.items():
            monthly[period][config] = _score_rows(
                [row for row in rows if row["period"] == period and row["valid_start_utc"] in common_starts]
            )

    return {
        "configs": configs,
        "config_info": replay_summary["configuration_definitions"],
        "replay_quality": replay_summary["data_quality"],
        "original_quality": {
            month["period"]: {
                **month["quality"],
                "repaired_validity_rows": month.get("repaired_validity_rows", []),
            }
            for month in original_summary["months"]
        },
        "all_available": all_available,
        "common": common,
        "original_all": original_all,
        "original_common": original_common,
        "monthly": monthly,
        "common_start_count": len(common_starts),
        "original_source_taf_count": len(original_rows_raw),
        "original_unique_taf_count": len(original_by_start),
        "replay_taf_count_per_config": len(replay_by_config[configs[0]]),
        "replay_summary_path": replay_dir / "taf_replay_multiconfig_summary.json",
        "original_summary_path": original_dir / "historical_preconfigured_taf_monthly.json",
    }


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("Title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=colors.HexColor("#17324D"), spaceAfter=12),
        "subtitle": ParagraphStyle("Subtitle", parent=base["Normal"], fontName="Helvetica", fontSize=10, leading=14, textColor=colors.HexColor("#52667D"), spaceAfter=15),
        "h1": ParagraphStyle("H1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=colors.HexColor("#17324D"), spaceBefore=8, spaceAfter=7),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=colors.HexColor("#245A7A"), spaceBefore=8, spaceAfter=5),
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontName="Helvetica", fontSize=8.7, leading=12, spaceAfter=6),
        "small": ParagraphStyle("Small", parent=base["BodyText"], fontName="Helvetica", fontSize=7.2, leading=9),
        "table": ParagraphStyle("Table", parent=base["BodyText"], fontName="Helvetica", fontSize=6.7, leading=8),
        "table_head": ParagraphStyle("TableHead", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=6.7, leading=8, textColor=colors.white, alignment=TA_CENTER),
        "note": ParagraphStyle("Note", parent=base["BodyText"], fontName="Helvetica-Oblique", fontSize=7.5, leading=10, textColor=colors.HexColor("#52667D")),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(str(text), style)


def _table(data: list[list[Any]], widths: list[float], styles: dict[str, ParagraphStyle], *, repeat: int = 1) -> Table:
    rendered = []
    for row_index, row in enumerate(data):
        style = styles["table_head"] if row_index == 0 else styles["table"]
        rendered.append([cell if isinstance(cell, Flowable) else _p(cell, style) for cell in row])
    table = Table(rendered, colWidths=widths, repeatRows=repeat, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#245A7A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BFD0DB")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FBFD")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F8FBFD"), colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


class RankingBars(Flowable):
    def __init__(self, rows: list[tuple[str, float, bool]], width: float = 16.5 * cm, height: float = 7.2 * cm):
        super().__init__()
        self.rows, self.width, self.height = rows, width, height

    def draw(self) -> None:
        canvas = self.canv
        label_width = 4.1 * cm
        bar_width = self.width - label_width - 1.7 * cm
        row_height = self.height / max(len(self.rows), 1)
        canvas.setFont("Helvetica", 7)
        for index, (label, score, is_original) in enumerate(self.rows):
            y = self.height - (index + 1) * row_height + 2
            canvas.setFillColor(colors.HexColor("#17324D"))
            canvas.drawRightString(label_width - 4, y + 3, label)
            canvas.setFillColor(colors.HexColor("#E3EDF2"))
            canvas.roundRect(label_width, y, bar_width, row_height - 5, 2, stroke=0, fill=1)
            canvas.setFillColor(colors.HexColor("#E07A3F") if is_original else colors.HexColor("#2F8FA3"))
            canvas.roundRect(label_width, y, bar_width * max(0, min(score, 100)) / 100, row_height - 5, 2, stroke=0, fill=1)
            canvas.setFillColor(colors.HexColor("#17324D"))
            canvas.drawString(label_width + bar_width + 5, y + 3, f"{score:.2f}%")


def _header_footer(canvas, doc) -> None:
    canvas.saveState()
    page_width, page_height = doc.pagesize
    canvas.setStrokeColor(colors.HexColor("#BFD0DB"))
    canvas.line(doc.leftMargin, page_height - 1.25 * cm, page_width - doc.rightMargin, page_height - 1.25 * cm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#52667D"))
    canvas.drawString(doc.leftMargin, page_height - 1.05 * cm, "WAWP historical TAF replay evaluation")
    canvas.drawRightString(page_width - doc.rightMargin, 0.75 * cm, f"Page {doc.page}")
    canvas.restoreState()


def _build_main_pdf(path: Path, analysis: dict[str, Any]) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(A4),
        leftMargin=1.25 * cm,
        rightMargin=1.25 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.35 * cm,
    )
    story: list[Flowable] = []
    original = analysis["original_common"]
    ranking = sorted(analysis["common"].items(), key=lambda item: item[1]["all_elements"] or -1, reverse=True)
    best_name, best = ranking[0]

    story.extend([
        _p("WAWP Historical TAF Replay", styles["title"]),
        _p("Multi-configuration verification against canonical METAR evidence, January-May 2026", styles["subtitle"]),
        _p("Decision Summary", styles["h1"]),
        _p(
            f"The fair like-for-like comparison uses {analysis['common_start_count']} shared TAF validity starts. "
            f"The original TAF baseline scores {_format_percent(original['all_elements'])} across all available elements and "
            f"{_format_percent(original['core'])} on the core wind/rain categories. The strongest replay configuration is "
            f"<b>{best_name}</b> at {_format_percent(best['all_elements'])} all-elements and {_format_percent(best['core'])} core. "
            "The generated replays do not yet outperform the original baseline under this verification method.",
            styles["body"],
        ),
        _p(
            "Among the generated alternatives, raw as-of weighting currently outperforms the historical-prior QM variants. "
            "The prior variants issue more rain and thunderstorm guidance, which is consistent with a more aggressive event response but currently produces weaker categorical skill.",
            styles["body"],
        ),
        Spacer(1, 5),
        _p("Like-for-Like All-Elements Score", styles["h2"]),
        RankingBars([("Original TAF baseline", original["all_elements"], True)] + [(_config_label(name), values["all_elements"], False) for name, values in ranking]),
        _p("This graphic compares only shared valid-start windows. It is a configuration ranking, not proof of lead-aware QM skill.", styles["note"]),
        PageBreak(),
        _p("Method and Coverage", styles["h1"]),
        _p(
            "Original baseline: the active TAF!C values in the five January-May verification workbooks. Machine candidates: the eight generated configurations in artifacts/taf_replay_2026_h1/replay_metadata.csv. "
            "Both sides use the same structured TAF evaluator: completed BECMG groups persist and an active TEMPO overlays the prevailing state.",
            styles["body"],
        ),
        _table([
            ["Evidence", "Original baseline", "Each replay configuration", "Comparison treatment"],
            ["TAF availability", str(analysis["original_source_taf_count"]), str(analysis["replay_taf_count_per_config"]), f"{analysis['common_start_count']} common validity starts"],
            ["METAR evidence", "Canonical Jan-May archive", "Same archive", "Same timestamp, visibility, cloud, wind, and rain parsing"],
            ["Rain provenance", "March/May dates normalized only when hour matched", "Same rule", "May non-hourly record excluded from hourly quality view"],
            ["Validity repair", "3 original-source strings repaired to 24 h", "0 replay TAFs repaired", "Original text and repair flags retained in CSV"],
        ], [3.0*cm, 3.5*cm, 3.6*cm, 7.0*cm], styles),
        Spacer(1, 7),
        _p("Scoring", styles["h2"]),
        _p(
            "Wind direction, wind speed, gust, visibility, rain occurrence, cloud amount, and cloud base are scored per issuance against hourly METAR states. "
            "The current category thresholds are intentionally retained for consistent experiment comparison. They are broad, and rain uses a strict greater-than-4-mm occurrence gate; therefore the rain result is not a full event-window verification result.",
            styles["body"],
        ),
        PageBreak(),
        _p("Overall Comparison", styles["h1"]),
    ])
    table_rows = [["Rank", "Configuration", "All elements", "Delta vs original", "Core", "Core delta"]]
    for index, (name, values) in enumerate(ranking, start=1):
        table_rows.append([
            str(index), _config_label(name), _format_percent(values["all_elements"]),
            _format_delta(values["all_elements"] - original["all_elements"]),
            _format_percent(values["core"]), _format_delta(values["core"] - original["core"]),
        ])
    table_rows.append(["-", "Original baseline", _format_percent(original["all_elements"]), "reference", _format_percent(original["core"]), "reference"])
    story.append(_table(table_rows, [1.0*cm, 4.2*cm, 2.4*cm, 2.8*cm, 2.4*cm, 2.8*cm], styles))
    story.extend([
        Spacer(1, 8),
        _p("Interpretation", styles["h2"]),
        _p(
            f"{best_name} is the best generated candidate, but it is {_format_delta(best['all_elements'] - original['all_elements'])} below the original baseline on all elements. "
            "This does not invalidate the continuous historical archive. It shows that the current transformation from consensus values to TAF groups, especially visibility/cloud and rain/TS triggers, needs calibration before historical-prior QM is promoted operationally.",
            styles["body"],
        ),
        PageBreak(),
        _p("Element-Level Comparison on Shared Windows", styles["h1"]),
    ])
    metric_rows = [["Element", "Original", *[_config_label(name) for name, _ in ranking]]]
    for metric in METRICS:
        metric_rows.append([metric.replace("_", " ").title(), _format_percent(original["metrics"][metric]), *[_format_percent(values["metrics"][metric]) for _, values in ranking]])
    story.append(_table(metric_rows, [2.8*cm] + [2.0*cm] * 9, styles))
    story.extend([
        Spacer(1, 8),
        _p("What the Element Scores Say", styles["h2"]),
        _p(
            "The original baseline remains materially stronger in the combined view. The raw configurations lead the replay set largely because their visibility and cloud behavior is less aggressive. "
            "Historical-prior configurations are useful candidates for further tuning rather than promotion: conservative caps improve them relative to uncapped prior variants, but not enough to beat the raw as-of configurations.",
            styles["body"],
        ),
        PageBreak(),
        _p("Monthly Like-for-Like Results", styles["h1"]),
    ])
    monthly_rows = [["Month", "Original", *[_config_label(name) for name, _ in ranking]]]
    for period in PERIODS:
        monthly_rows.append([period, _format_percent(analysis["monthly"][period]["original"]["all_elements"]), *[_format_percent(analysis["monthly"][period][name]["all_elements"]) for name, _ in ranking]])
    story.append(_table(monthly_rows, [1.5*cm] + [2.0*cm] * 9, styles))
    story.extend([
        Spacer(1, 8),
        _p("Monthly Pattern", styles["h2"]),
        _p(
            "No replay configuration consistently beats the original baseline across the five months. April is generally the strongest replay month, while the gap widens in May. "
            "This is consistent with the present low-cloud/visibility proxy and convective-group logic needing separate seasonal calibration.",
            styles["body"],
        ),
        PageBreak(),
        _p("Rain and Thunderstorm Behavior", styles["h1"]),
    ])
    rain_rows = [["Configuration", "Rain TAFs", "TS TAFs", "TEMPO TAFs", "BECMG TAFs", "Rain occurrence score"]]
    for name, values in ranking:
        source = analysis["all_available"][name]
        counts = next(row for row in json.loads(analysis["replay_summary_path"].read_text(encoding="utf-8"))["overall"].items() if row[0] == name)[1]["quality_gated"]
        rain_rows.append([
            _config_label(name), str(counts["rain_taf_count"]), str(counts["thunderstorm_taf_count"]), str(counts["tempo_taf_count"]), str(counts["becmg_taf_count"]),
            _format_percent(values["metrics"]["rain_occurrence"]),
        ])
    story.append(_table(rain_rows, [4.1*cm, 2.0*cm, 1.8*cm, 2.0*cm, 2.0*cm, 3.0*cm], styles))
    story.extend([
        Spacer(1, 8),
        _p("Rain/TS Conclusion", styles["h2"]),
        _p(
            "The prior variants generate 490-501 rain TAFs and 172-209 TS TAFs, compared with 454-461 rain TAFs and 130-164 TS TAFs for raw configurations. "
            "Because the current score treats rain as a strict categorical match rather than a timing-tolerant event, this report identifies an over-aggressive signal but does not yet establish the best convective configuration.",
            styles["body"],
        ),
        PageBreak(),
        _p("Recommendation and Next Experiment", styles["h1"]),
        _p("Do not promote a historical-prior QM TAF configuration yet.", styles["h2"]),
        _p(
            "Use raw_asof60 as the current machine-control candidate for the next controlled study, because it is the strongest replay configuration under the all-elements score. Retain raw_asof30 as a secondary candidate because it has the highest core score. "
            "Keep prior_regime_capped_asof60 as the QM candidate for targeted tuning, not as a production replacement.",
            styles["body"],
        ),
        _p("Required next verification", styles["h2"]),
        _p(
            "1. Replace strict rain category matching with event-window POD, FAR, CSI, and heavy-rain hit rate. 2. Score TSRA separately from plain RA. 3. Measure timing displacement at plus/minus 1 h and plus/minus 2 h. 4. Split visibility/cloud verification by dry, rainy, and dawn fog regimes. 5. Compare against the official original TAF archive using a frozen issuance schedule where available.",
            styles["body"],
        ),
        _p("Audit references", styles["h2"]),
        _p(
            "Replay issuance and hourly evidence: VERIFICATION_REPORTS/taf_replay_multiconfig_verification_2026. Original baseline evidence: VERIFICATION_REPORTS/original_taf_structured_baseline_2026. All repaired or normalized rows remain explicitly labelled in those CSV exports.",
            styles["note"],
        ),
    ])
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)


def _build_appendix_pdf(path: Path, analysis: dict[str, Any]) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=1.2*cm, rightMargin=1.2*cm, topMargin=1.5*cm, bottomMargin=1.2*cm)
    story: list[Flowable] = [
        _p("WAWP TAF Replay Verification - Technical Appendix", styles["title"]),
        _p("Structured-Taf comparison evidence and configuration details", styles["subtitle"]),
        _p("Configuration setup", styles["h1"]),
    ]
    config_rows = [["Configuration", "Weights", "QM", "Caps", "All available", "Common-window all", "Common core"]]
    ranking = sorted(analysis["common"].items(), key=lambda item: item[1]["all_elements"] or -1, reverse=True)
    for name, common in ranking:
        info = analysis["config_info"][name]
        config_rows.append([
            name,
            "equal" if info["weight_mode"] == "equal" else f"as-of {info['lookback_days']} d",
            info["qm_mode"],
            "yes" if info["conservative_caps"] else "no",
            _format_percent(analysis["all_available"][name]["all_elements"]),
            _format_percent(common["all_elements"]),
            _format_percent(common["core"]),
        ])
    story.append(_table(config_rows, [4.2*cm, 3*cm, 2.8*cm, 1.6*cm, 3*cm, 3.4*cm, 2.8*cm], styles))
    story.extend([PageBreak(), _p("Monthly Element Scores", styles["h1"])])
    for period in PERIODS:
        rows = [["Configuration", *[metric.replace("_", " ") for metric in METRICS], "All", "Core"]]
        for name, values in ranking:
            summary = analysis["monthly"][period][name]
            rows.append([_config_label(name), *[_format_percent(summary["metrics"][metric]) for metric in METRICS], _format_percent(summary["all_elements"]), _format_percent(summary["core"])])
        original = analysis["monthly"][period]["original"]
        rows.append(["Original baseline", *[_format_percent(original["metrics"][metric]) for metric in METRICS], _format_percent(original["all_elements"]), _format_percent(original["core"])])
        story.extend([_p(period, styles["h2"]), _table(rows, [3.1*cm] + [2.2*cm]*9, styles), Spacer(1, 6)])
    story.extend([PageBreak(), _p("Data Quality and Comparability", styles["h1"])])
    quality_rows = [["Period", "Rain normalized", "Rain unaligned", "Invalid calendar", "Non-hourly", "Original validity repairs"]]
    for period in PERIODS:
        replay_q = analysis["replay_quality"][period]
        original_q = analysis["original_quality"][period]
        quality_rows.append([
            period,
            str(replay_q["normalized_rain_timestamp_rows"]),
            str(replay_q["unaligned_rain_timestamp_rows"]),
            str(replay_q["invalid_calendar_rows"]),
            str(replay_q["non_hourly_rows"]),
            ", ".join(str(value) for value in original_q["repaired_validity_rows"]) or "none",
        ])
    story.append(_table(quality_rows, [3.0*cm, 3.2*cm, 3.0*cm, 3.0*cm, 2.7*cm, 5.0*cm], styles))
    story.extend([
        Spacer(1, 8),
        _p(
            "The appendix reports fair shared-window scores where the original and all replay configurations have the same valid start. It does not resolve the different issuance cadence, amendments, or human forecaster context present in the original source TAFs. These limits are why no configuration is recommended as a production replacement solely from this experiment.",
            styles["body"],
        ),
    ])
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL"))
    parser.add_argument("--output-dir", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\pdf"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    analysis = _load_analysis(args.root)
    main_pdf = args.output_dir / "WAWP_TAF_REPLAY_CONFIGURATION_COMPARISON.pdf"
    appendix_pdf = args.output_dir / "WAWP_TAF_REPLAY_CONFIGURATION_APPENDIX.pdf"
    _build_main_pdf(main_pdf, analysis)
    _build_appendix_pdf(appendix_pdf, analysis)
    manifest = {
        "main_pdf": str(main_pdf),
        "appendix_pdf": str(appendix_pdf),
        "common_valid_start_count": analysis["common_start_count"],
        "original_unique_taf_count": analysis["original_unique_taf_count"],
        "replay_taf_count_per_configuration": analysis["replay_taf_count_per_config"],
    }
    (args.output_dir / "WAWP_TAF_REPLAY_PDF_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
