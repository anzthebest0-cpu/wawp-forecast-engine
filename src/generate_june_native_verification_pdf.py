"""Generate a concise PDF of the TAF-native June 2026 verification result."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PAGE_SIZE = landscape(A4)
NAVY = colors.HexColor("#18344F")
PALE = colors.HexColor("#EAF3F8")
GRAY = colors.HexColor("#F4F6F7")
RED = colors.HexColor("#F9E2E0")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=NAVY, alignment=TA_LEFT, spaceAfter=8),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontName="Helvetica", fontSize=10, leading=14, textColor=colors.HexColor("#4C6171"), spaceAfter=12),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=NAVY, spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=8.7, leading=12, textColor=colors.HexColor("#24313D"), spaceAfter=6),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName="Helvetica", fontSize=7.2, leading=9, textColor=colors.HexColor("#334553")),
        "header": ParagraphStyle("header", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7, leading=8, textColor=colors.white),
    }


def _p(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(str(value).replace("&", "&amp;"), style)


def _footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#C7D3DC"))
    canvas.line(15 * mm, 12 * mm, PAGE_SIZE[0] - 15 * mm, 12 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#526978"))
    canvas.drawString(15 * mm, 7.5 * mm, "WAWP June 2026 - TAF-native experimental verification")
    canvas.drawRightString(PAGE_SIZE[0] - 15 * mm, 7.5 * mm, f"Page {document.page}")
    canvas.restoreState()


def _table(rows: list[list[Any]], widths: list[float], styles: dict[str, ParagraphStyle], *, first_row_header: bool = True) -> Table:
    converted = []
    for index, row in enumerate(rows):
        converted.append([_p(value, styles["header"] if first_row_header and index == 0 else styles["small"]) for value in row])
    table = Table(converted, colWidths=widths, repeatRows=1 if first_row_header else 0)
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5DD")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if first_row_header:
        commands += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRAY])]
    table.setStyle(TableStyle(commands))
    return table


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_pdf(source_dir: Path, output_path: Path) -> None:
    summary = json.loads((source_dir / "june_native_summary.json").read_text(encoding="utf-8"))
    episodes = _read_csv(source_dir / "june_native_event_episodes.csv")
    styles = _styles()
    document = SimpleDocTemplate(
        str(output_path), pagesize=PAGE_SIZE,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=17 * mm,
        title="WAWP June 2026 Original TAF: TAF-Native Verification",
        author="WAWP Forecast Engine",
    )
    story: list[Any] = [
        Paragraph("WAWP June 2026 Original TAF: TAF-Native Verification", styles["title"]),
        Paragraph("Recalculated from the original human TAF and METAR archive using change-group-aware, strict event-window logic", styles["subtitle"]),
        Paragraph("Decision Boundary", styles["h1"]),
        Paragraph("This is experimental historical verification evidence. It does not modify the source Excel workbook, operational database, live forecast calculation, or official TAF issuance process.", styles["body"]),
        Paragraph("What Changed", styles["h1"]),
        Paragraph("The earlier June comparison was built for machine-replay diagnostics: it used a reduced shared issuance set, top-of-hour METARs, one active state per hour, and a plus/minus two-hour event match. This renewed calculation uses both available half-hourly METARs, separates prevailing conditions from change groups, and requires exact overlap of forecast and observed event episodes. Three final-month TAFs that extend into July are excluded from scoring because the supplied METAR archive ends at June 30 23:30Z.", styles["body"]),
        Paragraph("Method", styles["h1"]),
        _table([
            ["TAF construct", "Treatment in this verification", "Reason"],
            ["Prevailing / FM", "State applies continuously from its effective time.", "Represents the expected prevailing condition."],
            ["BECMG", "Both before/after states are possible in its transition window; changed state persists at completion.", "Avoids treating a gradual change as an instantaneous hourly switch."],
            ["TEMPO", "A deterministic temporary alert interval.", "It warns of a condition within the stated window, not every hour outside it."],
            ["PROB30/40", "Kept probabilistic; reported with Brier components, excluded from deterministic primary event scores.", "A 40% probability is not a categorical certainty."],
            ["Rain / TS timing", "No plus/minus timing grace in primary result; one forecast episode matches at most one observed episode.", "Prevents an extended alert receiving multiple credits."],
        ], [47 * mm, 101 * mm, 102 * mm], styles),
        Spacer(1, 4 * mm),
        Paragraph("Sources", styles["h1"]),
        Paragraph("The method is synthesized from Mahringer (2008), <i>Terminal aerodrome forecast verification in Austro Control using time windows and ranges of forecast conditions</i>; Boucouvala and McCooey (2025), <i>A Verification Procedure for Terminal Aerodrome Forecasts</i>, DOI 10.3390/eesp2025035030; and the UK Met Office CAA TAF verification approach for categorical visibility/cloud-base skill.", styles["body"]),
        PageBreak(),
        Paragraph("Primary Results", styles["h1"]),
        Paragraph(f"Scope: {summary['scored_taf_count']} complete-coverage standard original TAFs out of {summary['official_taf_count']} supplied; {summary['excluded_nonstandard_taf_count']} nonstandard 05:30Z issuance excluded; {summary['excluded_incomplete_taf_count']} June 30 TAFs excluded because their validity extends beyond the available METAR archive; {summary['metar_count']} June half-hourly METARs; {summary['sample_count']} scored TAF-valid METAR samples.", styles["body"]),
        _table([
            ["Event", "Fcast episodes", "Observed episodes", "Hits", "Misses", "False alarms", "POD", "FAR", "CSI", "Bias"],
            *[
                [
                    name.title(), values["forecast_episodes"], values["observed_episodes"], values["hits"], values["misses"], values["false_alarms"],
                    _pct(values["POD"]), _pct(values["FAR"]), _pct(values["CSI"]), values["frequency_bias"],
                ]
                for name, values in (("rain", summary["events"]["rain"]["episode"]), ("thunderstorm", summary["events"]["thunderstorm"]["episode"]))
            ],
        ], [28 * mm, 25 * mm, 28 * mm, 16 * mm, 17 * mm, 24 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm], styles),
        Spacer(1, 5 * mm),
        Paragraph("How To Read This", styles["h1"]),
        Paragraph("POD asks whether observed events were warned. FAR asks how many warnings did not verify. CSI combines hits, misses, and false alarms without allowing dry observations to dominate. Frequency bias below 1 means the original TAFs warned for fewer event episodes than were observed.", styles["body"]),
        Paragraph("Half-Hourly Diagnostic", styles["h1"]),
        _table([
            ["Event", "Samples", "Accuracy", "POD", "FAR", "CSI", "HSS"],
            *[
                [name.title(), values["sample_size"], _pct(values["accuracy"]), _pct(values["POD"]), _pct(values["FAR"]), _pct(values["CSI"]), values["HSS"]]
                for name, values in (("rain", summary["events"]["rain"]["sample"]), ("thunderstorm", summary["events"]["thunderstorm"]["sample"]))
            ],
        ], [35 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm], styles),
        Spacer(1, 4 * mm),
        Paragraph("Accuracy remains high because dry samples dominate. HSS, CSI, POD, and FAR are the relevant measures for rare rain and thunderstorm warnings.", styles["body"]),
        PageBreak(),
        Paragraph("Probability and Visibility", styles["h1"]),
        _table([
            ["Probability event", "PROB groups", "Mean Brier score", "Interpretation"],
            *[
                [event.title(), values["group_count"], values["mean_brier_score"] if values["mean_brier_score"] is not None else "n/a", "Small-sample descriptive result only; not a promoted probability skill score."]
                for event, values in summary["probability_groups"].items()
            ],
        ], [40 * mm, 30 * mm, 35 * mm, 145 * mm], styles),
        Spacer(1, 5 * mm),
        Paragraph("Visibility Category Range", styles["h1"]),
        _table([
            ["Element", "Eligible hours", "Minimum category accuracy", "Maximum category accuracy", "Note"],
            *[
                [element.title(), values["eligible_hours"], _pct(values["minimum_category_accuracy"]), _pct(values["maximum_category_accuracy"]), "Categories use all TAF states and all available METARs in each hour."]
                for element, values in summary["ranges"].items()
            ],
        ], [35 * mm, 35 * mm, 48 * mm, 48 * mm, 84 * mm], styles),
        Spacer(1, 5 * mm),
        Paragraph("Limitations", styles["h1"]),
        Paragraph("Cloud-base verification is not claimed here because the compact parser retains only the first cloud layer, which can conceal a later BKN/OVC ceiling. Gust verification is not promoted from twice-hourly METARs because those observations do not provide a trustworthy continuous maximum-gust record. One month is insufficient for permanent threshold decisions.", styles["body"]),
        Paragraph("Audit Files", styles["h1"]),
        Paragraph("The accompanying CSV ledgers contain every scored half-hourly event sample, every issuance event-episode match, every PROB group, and every visibility category-range comparison.", styles["body"]),
    ]
    for event in ("rain", "thunderstorm"):
        event_rows = [row for row in episodes if row["event"] == event]
        story.extend([PageBreak(), Paragraph(f"Issuance Episode Ledger: {event.title()}", styles["h1"]), Paragraph("Each row represents one original TAF issuance. Interval details are preserved in the companion CSV; this ledger shows the counts used by the primary metric.", styles["body"]), _table(
            [["Issuance", "Forecast episodes", "Observed episodes", "Hits", "Misses", "False alarms"]] + [
                [row["issuance_utc"].replace("T", " ").replace("Z", ""), row["forecast_episodes"], row["observed_episodes"], row["hits"], row["misses"], row["false_alarms"]]
                for row in event_rows
            ], [55 * mm, 33 * mm, 35 * mm, 24 * mm, 24 * mm, 30 * mm], styles
        )])
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_june_native_verification_2026"))
    parser.add_argument("--output", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\meteologix-wawp-main\output\pdf\WAWP_June_2026_Original_TAF_Native_Verification.pdf"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_pdf(args.source_dir, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
