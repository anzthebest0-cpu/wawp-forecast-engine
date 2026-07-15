"""Create a PDF comparison of replay event-window verification and original TAFs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


EVENTS = ("rain_any", "rain_heavy", "thunderstorm")
WINDOWS = (0, 1, 2)
REGIMES = ("dry", "rain", "dawn_fog_proxy")
NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#286083")
ORANGE = colors.HexColor("#E07A3F")
PALE = colors.HexColor("#EEF4F7")
GRID = colors.HexColor("#BFD0DB")
MUTED = colors.HexColor("#52667D")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("Title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=24, leading=28, textColor=NAVY, spaceAfter=10),
        "subtitle": ParagraphStyle("Subtitle", parent=base["BodyText"], fontName="Helvetica", fontSize=12, leading=16, textColor=MUTED, spaceAfter=16),
        "h1": ParagraphStyle("H1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=NAVY, spaceBefore=6, spaceAfter=8),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=BLUE, spaceBefore=5, spaceAfter=6),
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=colors.black, spaceAfter=7),
        "note": ParagraphStyle("Note", parent=base["BodyText"], fontName="Helvetica-Oblique", fontSize=8.3, leading=11, textColor=MUTED, spaceAfter=5),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def _header_footer(canvas, doc) -> None:
    canvas.saveState()
    width, height = doc.pagesize
    canvas.setStrokeColor(GRID)
    canvas.line(doc.leftMargin, height - 1.15 * cm, width - doc.rightMargin, height - 1.15 * cm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, height - 0.95 * cm, "WAWP historical TAF replay - event-window verification")
    canvas.drawRightString(width - doc.rightMargin, 0.72 * cm, f"Page {doc.page}")
    canvas.restoreState()


def _table(rows: list[list[Any]], widths: list[float], styles: dict[str, ParagraphStyle]) -> Table:
    normalized = []
    for row_index, row in enumerate(rows):
        normalized.append([
            value if isinstance(value, Paragraph) else _p(str(value), styles["body"] if row_index else styles["note"])
            for value in row
        ])
    table = Table(normalized, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, GRID),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _label(source: str) -> str:
    if source == "original_baseline":
        return "Original TAF baseline"
    return source.replace("prior_regime_capped", "prior cap").replace("prior_regime", "prior regime").replace("prior_global", "prior global").replace("raw_asof", "raw as-of").replace("raw_equal", "raw equal")


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100.0:.1f}%"


def _hours(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f} h"


def _event_rows(summary: dict[str, Any], event: str, window: int) -> list[list[str]]:
    rows = [["Source", "Observed", "Forecast", "POD", "FAR", "CSI", "HSS", "Timing MAE"]]
    order = ["original_baseline", *sorted(key for key in summary["event_metrics"] if key != "original_baseline")]
    for source in order:
        value = summary["event_metrics"][source][event][f"pm{window}h"]
        rows.append([
            _label(source),
            str(value["observed_events"]),
            str(value["forecast_events"]),
            _pct(value["POD"]),
            _pct(value["FAR"]),
            _pct(value["CSI"]),
            _pct(value["HSS"]),
            _hours(value["mean_abs_timing_error_h"]),
        ])
    return rows


def _regime_rows(summary: dict[str, Any], regime: str) -> list[list[str]]:
    rows = [["Source", "Visibility", "Cloud amount", "Cloud base"]]
    order = ["original_baseline", *sorted(key for key in summary["regime_metrics"] if key != "original_baseline")]
    for source in order:
        values = summary["regime_metrics"][source][regime]
        cells = []
        for metric in ("visibility", "cloud_amount", "cloud_base"):
            value = values[metric]
            cells.append("n/a" if value["score_percent"] is None else f"{value['score_percent']:.2f}% ({value['eligible_hours']} h)")
        rows.append([_label(source), *cells])
    return rows


def build_pdf(summary: dict[str, Any], output_path: Path) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=1.25 * cm,
        rightMargin=1.25 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.3 * cm,
    )
    story = [
        _p("WAWP Replay Event-Window Verification", styles["title"]),
        _p("Rain, thunderstorm, visibility, and cloud comparison against the original TAF baseline", styles["subtitle"]),
        _p("Decision Summary", styles["h1"]),
        _p(
            f"The evaluation covers {summary['common_valid_start_count']} shared TAF validity starts and {summary['quality_metar_hours']} quality-eligible canonical METAR hours. It is an experimental backtest; it does not modify live WAWP guidance.",
            styles["body"],
        ),
        _p("No replay configuration is ready for operational rain or TS triggering. The machine configurations gain detection by issuing substantially more event guidance, but their false-alarm rates remain near 99%. Historical-prior QM does not solve this in the current TAF transformation.", styles["body"]),
        _p("Comparison Method", styles["h1"]),
        _table([
            ["Component", "Treatment"],
            ["TAF state", "Structured state: completed BECMG persists; active TEMPO overlays the prevailing state."],
            ["Event match", "One-to-one within a 24-hour TAF. A forecast event cannot receive credit for more than one observed event."],
            ["Timing windows", "Exact hour, plus/minus 1 hour, and plus/minus 2 hours."],
            ["Rain", "Any measurable quality-eligible HUJAN (>=0.1 mm) and heavy rain (>4.0 mm)."],
            ["Thunderstorm", "TS token in the METAR or evaluated TAF weather group."],
        ], [4.0 * cm, 21.0 * cm], styles),
        Spacer(1, 8),
        _p("Interpretation", styles["h2"]),
        _p("POD measures observed events caught; FAR measures forecast event warnings that did not match; CSI and HSS balance hits, misses, and false alarms. A higher POD is not useful by itself when FAR is excessive.", styles["body"]),
        PageBreak(),
        _p("Rain Event Results", styles["h1"]),
        _p("Rain-any uses a 0.1 mm observed threshold. The original baseline is displayed as the reference source; every machine row is evaluated against the same METAR archive and shared issuance windows.", styles["body"]),
    ]
    for window in WINDOWS:
        story.append(KeepTogether([
            _p(f"Rain-any: plus/minus {window} h", styles["h2"]),
            _table(_event_rows(summary, "rain_any", window), [5.3*cm, 2.4*cm, 2.4*cm, 2.1*cm, 2.1*cm, 2.1*cm, 2.1*cm, 2.8*cm], styles),
            Spacer(1, 6),
        ]))
    story.extend([
        _p("Rain Finding", styles["h2"]),
        _p("At plus/minus 2 h, raw as-of 60 catches 66.4% of repeated observed rain events, but it creates 6,313 false alarms from 6,384 rain forecast events. Its CSI is only 1.1% and HSS is 0.7%. The original baseline has lower POD (16.8%) but higher CSI (4.1%) and HSS (6.8%).", styles["body"]),
        PageBreak(),
        _p("Heavy Rain and Thunderstorm Results", styles["h1"]),
        _p("Heavy-rain results are included for audit completeness, but no quality-eligible observation exceeded 4.0 mm in the assessed January-May archive. The maximum was 3.5 mm, so heavy-rain skill is not assessable.", styles["body"]),
        _p("Heavy rain: plus/minus 2 h", styles["h2"]),
        _table(_event_rows(summary, "rain_heavy", 2), [5.3*cm, 2.4*cm, 2.4*cm, 2.1*cm, 2.1*cm, 2.1*cm, 2.1*cm, 2.8*cm], styles),
        Spacer(1, 9),
    ])
    for window in WINDOWS:
        story.append(KeepTogether([
            _p(f"Thunderstorm: plus/minus {window} h", styles["h2"]),
            _table(_event_rows(summary, "thunderstorm", window), [5.3*cm, 2.4*cm, 2.4*cm, 2.1*cm, 2.1*cm, 2.1*cm, 2.1*cm, 2.8*cm], styles),
            Spacer(1, 6),
        ]))
    story.extend([
        _p("TS Finding", styles["h2"]),
        _p("The best replay TS detection is raw as-of 30 at plus/minus 2 h (POD 21.3%), but it has FAR 99.1%, CSI 0.9%, and HSS 0.6%. The original baseline has lower POD (10.0%) but much stronger CSI (5.7%) and HSS (10.3%).", styles["body"]),
        PageBreak(),
        _p("Visibility and Cloud Regime Diagnostics", styles["h1"]),
        _p("Dry means no measurable rain. Rain means quality-eligible measurable rain. Dawn fog proxy is 04-08 WITA with observed visibility at or below 5 km or BR/FG. It is a diagnostic proxy, not a confirmed fog class.", styles["body"]),
    ])
    for regime in REGIMES:
        story.append(KeepTogether([
            _p(regime.replace("_", " ").title(), styles["h2"]),
            _table(_regime_rows(summary, regime), [5.5*cm, 6.0*cm, 6.0*cm, 6.0*cm], styles),
            Spacer(1, 8),
        ]))
    story.extend([
        _p("Regime Finding", styles["h2"]),
        _p("The dry sample is large enough to diagnose: raw as-of 60 visibility is 83.7% and cloud base 32.2%, compared with 98.9% and 94.9% for the original baseline. Rain and dawn-fog proxy samples are too small for a promotion decision (32 and 4 eligible repeated TAF-hours respectively).", styles["body"]),
        PageBreak(),
        _p("Verdict and Required Next Study", styles["h1"]),
        _p("Do not promote any historical-prior QM replay configuration into automatic rain/TS TAF guidance. Keep raw as-of 60 as the machine-control candidate only; it is the strongest broad configuration, but its event false-alarm rate fails this verification. Keep prior-regime-capped as a research candidate, not a production replacement.", styles["body"]),
        _p("Required next study", styles["h2"]),
        _table([
            ["Priority", "Study", "Why"],
            ["1", "Calibrate separate RA and TS gates with probability/convective thresholds.", "Current categorical conversion over-issues both event types."],
            ["2", "Extend evidence through seasons containing heavy rain and dawn low-visibility events.", "This archive has no heavy-rain cases and only four dawn proxy cases."],
            ["3", "Score event occurrence by issuance-level warning windows and false-alarm burden.", "This retains timing tolerance while preventing broad TEMPO credit."],
            ["4", "Retest raw control and QM candidates after gate tuning.", "QM must improve CSI/HSS without materially raising FAR."],
        ], [2.0*cm, 10.5*cm, 11.0*cm], styles),
        Spacer(1, 9),
        _p("Audit inputs: VERIFICATION_REPORTS/taf_replay_event_window_verification_2026 plus the prior replay and original structured-baseline CSV evidence. All results are experimental guidance diagnostics, not official TAF verification scores.", styles["note"]),
    ])
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_replay_event_window_verification_2026\taf_replay_event_window_summary.json"))
    parser.add_argument("--output", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\pdf\WAWP_TAF_REPLAY_EVENT_WINDOW_COMPARISON.pdf"))
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_pdf(summary, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
