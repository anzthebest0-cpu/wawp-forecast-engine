"""Experimental rain and TS gate sweep for the January-May 2026 TAF replay.

The runner holds the forecast archive, as-of 60-day weighting, and canonical
METAR verification constant.  Only the rain-signal and TS-proxy gates vary.
It is deliberately separate from ``run_pipeline.py`` and makes no operational
promotion decision.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.taf_core import RainConfig
from src.taf_replay_event_window_verification import run as run_event_window_verification
from src.taf_replay_experiment import (
    MODELS,
    AsOfWeightEngine,
    HistoricalPrior,
    ReplayConfig,
    _build_consensus,
    _flatten_taf,
    _historical_window,
    _issue_times,
    _load_historical_forecasts,
    _load_pairs,
)
from src.taf_replay_multiconfig_verification import run_verification
from src.tafor_generator import generate_tafor


@dataclass(frozen=True)
class GatePolicy:
    name: str
    description: str
    wet_model_probability_threshold: float
    min_probability_agreement: float
    min_amount_agreement: float
    max_bridge_gap_hours: int
    ts_policy: str
    min_rain_signal_hours: int = 1
    becmg_min_rain_hours: int = 1
    becmg_min_agreement: float = 0.10


POLICIES = (
    GatePolicy(
        "control_current",
        "Current operational thresholds: 40% wet-model probability, no minimum agreement gate, and a two-hour bridge.",
        40.0,
        0.0,
        0.0,
        2,
        "broad_current",
    ),
    GatePolicy(
        "rain_grid_45_10",
        "Rain probability requires 45% wet-model weight and 10% spread-adjusted agreement; bridge disabled; current TS proxy retained.",
        45.0,
        0.10,
        0.0,
        0,
        "broad_current",
    ),
    GatePolicy(
        "rain_moderate",
        "Rain probability requires 50% wet-model weight and 15% spread-adjusted agreement; bridge disabled; current TS proxy retained.",
        50.0,
        0.15,
        0.0,
        0,
        "broad_current",
    ),
    GatePolicy(
        "rain_grid_55_15",
        "Rain probability requires 55% wet-model weight and 15% spread-adjusted agreement; bridge disabled; current TS proxy retained.",
        55.0,
        0.15,
        0.0,
        0,
        "broad_current",
    ),
    GatePolicy(
        "rain_grid_50_20",
        "Rain probability requires 50% wet-model weight and 20% spread-adjusted agreement; bridge disabled; current TS proxy retained.",
        50.0,
        0.20,
        0.0,
        0,
        "broad_current",
    ),
    GatePolicy(
        "rain_moderate_becmg_guard",
        "Moderate rain gate plus BECMG rain only after at least two rainy hours and 20% spread-adjusted agreement; weaker events remain temporary/probability groups.",
        50.0,
        0.15,
        0.0,
        0,
        "broad_current",
        becmg_min_rain_hours=2,
        becmg_min_agreement=0.20,
    ),
    GatePolicy(
        "rain_strict",
        "Only the 1.0 mm consensus-amount route remains, with 15% spread-adjusted agreement; bridge disabled; current TS proxy retained.",
        101.0,
        1.0,
        0.15,
        0,
        "broad_current",
    ),
    GatePolicy(
        "ts_weather_code_only",
        "Current rain logic, but TS wording only when the weighted Open-Meteo weather code is 95, 96, or 99.",
        40.0,
        0.0,
        0.0,
        2,
        "direct_weather_code",
    ),
    GatePolicy(
        "combined_moderate_strict_ts",
        "Moderate rain gate plus TS weather code or strict environment: CAPE >=1000 J/kg, LI <=-4, CIN <=50 J/kg, rain >=1 mm, and 14-19 WITA.",
        50.0,
        0.15,
        0.0,
        0,
        "strict_environmental",
    ),
)


def _policy_config(policy: GatePolicy):
    """Create an isolated RainConfig subclass for a single replay policy."""
    return type(
        f"ReplayGate_{policy.name}",
        (RainConfig,),
        {
            "WET_MODEL_PROBABILITY_THR": policy.wet_model_probability_threshold,
            "MIN_PROBABILITY_AGREEMENT": policy.min_probability_agreement,
            "MIN_RAIN_SIGNAL_AGREEMENT": policy.min_amount_agreement,
            "MAX_BRIDGE_GAP": policy.max_bridge_gap_hours,
            "MIN_RAIN_SIGNAL_HOURS": policy.min_rain_signal_hours,
            "TS_POLICY": policy.ts_policy,
            "BECMG_MIN_RAIN_HOURS": policy.becmg_min_rain_hours,
            "BECMG_MIN_AGREEMENT": policy.becmg_min_agreement,
        },
    )


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _event_value(event_summary: dict[str, Any], policy: str, event: str, key: str) -> Any:
    return event_summary["event_metrics"][policy][event]["pm2h"][key]


def _summary_rows(
    verification: dict[str, Any], events: dict[str, Any], policies: tuple[GatePolicy, ...]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    control = "control_current"
    for policy in policies:
        name = policy.name
        quality = verification["overall"][name]["quality_gated"]
        rain = events["event_metrics"][name]["rain_any"]["pm2h"]
        ts = events["event_metrics"][name]["thunderstorm"]["pm2h"]
        rows.append({
            "policy": name,
            "description": policy.description,
            "quality_all_elements_percent": quality["all_elements_mean_percent"],
            "quality_core_percent": quality["core_mean_percent"],
            "rain_forecast_hours": rain["forecast_events"],
            "rain_hits": rain["hits"],
            "rain_pod": rain["POD"],
            "rain_far": rain["FAR"],
            "rain_csi": rain["CSI"],
            "rain_hss": rain["HSS"],
            "ts_forecast_hours": ts["forecast_events"],
            "ts_hits": ts["hits"],
            "ts_pod": ts["POD"],
            "ts_far": ts["FAR"],
            "ts_csi": ts["CSI"],
            "ts_hss": ts["HSS"],
            "rain_forecast_hour_change_vs_control": rain["forecast_events"] - _event_value(events, control, "rain_any", "forecast_events"),
            "ts_forecast_hour_change_vs_control": ts["forecast_events"] - _event_value(events, control, "thunderstorm", "forecast_events"),
            "rain_pod_change_vs_control": _delta(rain["POD"], _event_value(events, control, "rain_any", "POD")),
            "ts_pod_change_vs_control": _delta(ts["POD"], _event_value(events, control, "thunderstorm", "POD")),
        })
    return rows


def _delta(value: float | None, control: float | None) -> float | None:
    return round(value - control, 4) if value is not None and control is not None else None


def _write_markdown(path: Path, rows: list[dict[str, Any]], policies: tuple[GatePolicy, ...]) -> None:
    lines = [
        "# WAWP Experimental Rain and Thunderstorm Gate Sweep",
        "",
        "## Scope",
        "",
        "All policies use the same January-May 2026 continuous-history forecast archive, raw as-of 60-day ensemble weights, official issuance schedule, and canonical METAR verification. Only rain/TS gates vary. This is a historical experiment; no live pipeline setting is changed.",
        "",
        "Rain and TS scores are one-to-one plus/minus two-hour event-window checks. The forecast-hour totals are repeated TAF-validity checks rather than independent storm counts.",
        "",
        "## Results",
        "",
        "| Policy | Rain hours | Rain POD | Rain FAR | Rain CSI | TS hours | TS POD | TS FAR | TS CSI | Core quality |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        def pct(value: float | None) -> str:
            return "n/a" if value is None else f"{value * 100:.1f}%"
        quality = row["quality_core_percent"]
        quality_text = "n/a" if quality is None else f"{quality:.2f}%"
        lines.append(
            f"| `{row['policy']}` | {row['rain_forecast_hours']} | {pct(row['rain_pod'])} | {pct(row['rain_far'])} | {pct(row['rain_csi'])} | "
            f"{row['ts_forecast_hours']} | {pct(row['ts_pod'])} | {pct(row['ts_far'])} | {pct(row['ts_csi'])} | "
            f"{quality_text} |"
        )
    lines.extend([
        "",
        "## Policy Definitions",
        "",
    ])
    for policy in policies:
        lines.append(f"- `{policy.name}`: {policy.description}")
    lines.extend([
        "",
        "## Promotion Guardrail",
        "",
        "Do not promote a policy only because it issues fewer warnings. A candidate must materially reduce FAR while preserving a defensible POD and CSI relative to the control. This result should be reviewed before any shadow-mode or operational change.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    root: Path,
    db_path: Path,
    output_dir: Path,
    limit: int | None = None,
    policies: tuple[GatePolicy, ...] = POLICIES,
) -> dict[str, Any]:
    """Generate, verify, and compare the gate policies without production writes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-05-31")
    base_config = ReplayConfig("raw_asof60", "asof", 60, "none")
    policy_configs = {policy.name: _policy_config(policy) for policy in policies}
    taf_lines = {policy.name: [] for policy in policies}
    metadata_rows: list[dict[str, Any]] = []
    skipped: list[str] = []

    with sqlite3.connect(db_path) as conn:
        pairs = _load_pairs(conn)
        # The final scheduled 23Z issuance begins on the next UTC day and
        # needs its full 24-hour local window. Load one extra day before the
        # helper adds its own edge buffer, otherwise the final TAF is partial.
        forecasts = _load_historical_forecasts(conn, start, end + pd.Timedelta(days=1))
    prior = HistoricalPrior(pairs, start.normalize())
    weight_engine = AsOfWeightEngine(pairs)
    issues = _issue_times(start, end)
    if limit is not None:
        issues = issues[:limit]
    weights_cache: dict[pd.Timestamp, dict[str, dict[str, float]]] = {}

    for issue_utc in issues:
        rows = _historical_window(forecasts, issue_utc)
        if len(rows) < 24 * len(MODELS) * 0.55:
            skipped.append(issue_utc.strftime("%Y-%m-%d %H:%M:%S"))
            continue
        asof_cutoff = issue_utc + pd.Timedelta(hours=8)
        if asof_cutoff not in weights_cache:
            weights_cache[asof_cutoff] = weight_engine.weights(asof_cutoff, lookback_days=60, equal=False)
        weights = weights_cache[asof_cutoff]
        consensus, model_data, qm_rain = _build_consensus(rows, issue_utc, weights, prior, base_config)
        for policy in policies:
            taf = generate_tafor(
                consensus,
                model_data,
                qm_rain,
                weights,
                experimental_taf_config=policy_configs[policy.name],
            )
            if not taf or not taf.get("taf_text"):
                continue
            compact = _flatten_taf(taf["taf_text"])
            taf_lines[policy.name].append(compact)
            metadata_rows.append({
                "config": policy.name,
                "issuance_utc": issue_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "valid_start_wita": (issue_utc + pd.Timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
                "taf": compact,
                "weight_mode": base_config.weight_mode,
                "lookback_days": base_config.lookback_days,
                "qm_mode": base_config.qm_mode,
                "conservative_caps": base_config.conservative_caps,
                "gate_policy": json.dumps(asdict(policy), sort_keys=True),
                "source_label": "experimental_gate_sweep_not_operational",
            })

    for policy in policies:
        (output_dir / f"{policy.name}_tafs.txt").write_text(
            "\n".join(taf_lines[policy.name]) + ("\n" if taf_lines[policy.name] else ""),
            encoding="utf-8",
        )
    fields = list(metadata_rows[0]) if metadata_rows else ["config"]
    _write_csv(output_dir / "replay_metadata.csv", metadata_rows, fields)
    manifest = {
        "experiment": "historical_taf_rain_ts_gate_sweep",
        "scope": "experimental only; no operational pipeline changes",
        "start": str(start.date()),
        "end": str(end.date()),
        "base_replay_config": asdict(base_config),
        "configs": [
            {**asdict(base_config), "name": policy.name, "gate_policy": asdict(policy)}
            for policy in policies
        ],
        "issue_count_attempted": len(issues),
        "issue_count_completed": len(issues) - len(skipped),
        "skipped_issuances_utc": skipped,
        "taf_count_by_policy": {name: len(lines) for name, lines in taf_lines.items()},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    metar_dir = root / "VERIFICATION_REPORTS" / "metar_standalone" / "canonical"
    verification_dir = output_dir / "verification"
    verification = run_verification(output_dir / "replay_metadata.csv", metar_dir, verification_dir)
    event_output = output_dir / "event_window"
    events = run_event_window_verification(
        root,
        event_output,
        replay_dir=verification_dir,
        original_dir=root / "VERIFICATION_REPORTS" / "original_taf_structured_baseline_2026",
    )
    rows = _summary_rows(verification, events, policies)
    _write_csv(
        output_dir / "gate_sweep_summary.csv",
        rows,
        list(rows[0]) if rows else ["policy"],
    )
    _write_markdown(output_dir / "GATE_SWEEP_REPORT.md", rows, policies)
    result = {"manifest": manifest, "verification": verification, "event_window": events, "summary": rows}
    (output_dir / "gate_sweep_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL"))
    parser.add_argument("--db", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\meteologix-wawp-main\wawp_forecasts.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_gate_sweep_2026"),
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit official issuance times for a smoke check.")
    args = parser.parse_args()
    result = run(args.root, args.db, args.output_dir, args.limit)
    print(json.dumps({"output_dir": str(args.output_dir), "policies": [row["policy"] for row in result["summary"]]}, indent=2))


if __name__ == "__main__":
    main()
