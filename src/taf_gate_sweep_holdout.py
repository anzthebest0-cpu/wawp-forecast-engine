"""Independent monthly holdout and uncertainty checks for rain-gate candidates.

This consumes existing historical gate-sweep outputs. It does not regenerate
TAFs or touch operational guidance. Candidate choice is made on four months and
then assessed on the omitted month, preventing a threshold from winning solely
because it fitted the full January-May sample.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from src.taf_replay_event_window_verification import (
    EVENTS,
    PERIODS,
    _aggregate_event_rows,
    _event_scores,
    _forecast_event,
    _matching_counts,
    _metar_by_time,
    _observed_event,
    _parse_utc,
    _read_csv,
    _replay_rows,
)


CONTROL = "control_current"
CANDIDATES = ("rain_grid_45_10", "rain_grid_55_15", "rain_grid_50_20")
RAIN_EVENT = "rain_any"
WINDOW_HOURS = 2
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20260715


@dataclass(frozen=True)
class EventBlock:
    start: datetime
    end: datetime


def _common_valid_starts(root: Path, replay_dir: Path) -> set[str]:
    replay_issuance = _read_csv(replay_dir / "taf_replay_multiconfig_issuance.csv")
    original_issuance = _read_csv(
        root / "VERIFICATION_REPORTS" / "original_taf_structured_baseline_2026" / "historical_preconfigured_taf_issuance.csv"
    )
    replay_starts = {
        (_parse_utc(row["issuance_utc"]) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        for row in replay_issuance
    }
    return replay_starts & {row["taf_valid_start_utc"] for row in original_issuance}


def _replay_by_policy(root: Path, replay_dir: Path) -> dict[str, list[dict[str, Any]]]:
    metar = _metar_by_time(root / "VERIFICATION_REPORTS" / "metar_standalone" / "canonical")
    common_starts = _common_valid_starts(root, replay_dir)
    return _replay_rows(replay_dir / "taf_replay_multiconfig_hourly.csv", metar, common_starts)


def _issue_period(row: dict[str, Any]) -> str:
    return (_parse_utc(row["issuance_utc"]) + timedelta(hours=1)).strftime("%Y-%m")


def _contiguous_blocks(rows: list[dict[str, Any]], event: str, source: str) -> list[EventBlock]:
    if source not in {"forecast", "observed"}:
        raise ValueError(f"Unknown block source: {source}")
    key = "forecast_events" if source == "forecast" else "observed_events"
    blocks: list[EventBlock] = []
    start: datetime | None = None
    last: datetime | None = None
    for row in sorted(rows, key=lambda value: value["valid_time"]):
        active = bool(row[key][event])
        current = row["valid_time"]
        contiguous = last is not None and current - last == timedelta(hours=1)
        if active and start is None:
            start = current
        elif active and start is not None and not contiguous:
            blocks.append(EventBlock(start, last or start))
            start = current
        elif not active and start is not None:
            blocks.append(EventBlock(start, last or start))
            start = None
        last = current
    if start is not None:
        blocks.append(EventBlock(start, last or start))
    return blocks


def _block_distance_hours(left: EventBlock, right: EventBlock) -> float:
    if left.end < right.start:
        return (right.start - left.end).total_seconds() / 3600.0
    if right.end < left.start:
        return (left.start - right.end).total_seconds() / 3600.0
    return 0.0


def _group_event_counts(rows: list[dict[str, Any]], event: str, window_hours: int = WINDOW_HOURS) -> dict[str, int]:
    """One-to-one matching for contiguous event groups within each TAF."""
    totals = defaultdict(int)
    by_issuance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_issuance[row["issuance_key"]].append(row)
    for issuance_rows in by_issuance.values():
        forecast_blocks = _contiguous_blocks(issuance_rows, event, "forecast")
        observed_blocks = _contiguous_blocks(issuance_rows, event, "observed")
        used_forecasts: set[int] = set()
        hits = 0
        for observed in observed_blocks:
            candidates = [
                index for index, forecast in enumerate(forecast_blocks)
                if index not in used_forecasts and _block_distance_hours(forecast, observed) <= window_hours
            ]
            if not candidates:
                continue
            selected = min(candidates, key=lambda index: (_block_distance_hours(forecast_blocks[index], observed), forecast_blocks[index].start))
            used_forecasts.add(selected)
            hits += 1
        totals["hits"] += hits
        totals["misses"] += len(observed_blocks) - hits
        totals["false_alarms"] += len(forecast_blocks) - hits
        totals["observed_events"] += len(observed_blocks)
        totals["forecast_events"] += len(forecast_blocks)
    return dict(totals)


def _group_event_scores(rows: list[dict[str, Any]], event: str) -> dict[str, float | int | None]:
    counts = _group_event_counts(rows, event)
    hits = counts["hits"]
    misses = counts["misses"]
    false_alarms = counts["false_alarms"]
    pod = hits / (hits + misses) if hits + misses else None
    far = false_alarms / (hits + false_alarms) if hits + false_alarms else None
    csi = hits / (hits + misses + false_alarms) if hits + misses + false_alarms else None
    return {
        **counts,
        "POD": round(pod, 4) if pod is not None else None,
        "FAR": round(far, 4) if far is not None else None,
        "CSI": round(csi, 4) if csi is not None else None,
    }


def _candidate_rank(metrics: dict[str, Any], control: dict[str, Any]) -> tuple[float, float, int] | None:
    """Pre-registered selection rule used within each four-month training set."""
    pod = metrics["POD"]
    control_pod = control["POD"]
    far = metrics["FAR"]
    control_far = control["FAR"]
    if pod is None or control_pod is None or far is None or control_far is None:
        return None
    # Do not select a candidate that loses more than two POD percentage points
    # or has a worse FAR than the current control.
    if pod < control_pod - 0.02 or far > control_far:
        return None
    return (float(metrics["CSI"] or -1.0), float(metrics["HSS"] or -1.0), -int(metrics["forecast_events"]))


def _monthly_metrics(rows_by_policy: dict[str, list[dict[str, Any]]], policies: Iterable[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for period in PERIODS:
        for policy in policies:
            relevant = [row for row in rows_by_policy.get(policy, []) if _issue_period(row) == period]
            hourly = _aggregate_event_rows(relevant, RAIN_EVENT, WINDOW_HOURS)
            groups = _group_event_scores(relevant, RAIN_EVENT)
            output.append({"period": period, "policy": policy, **{f"hourly_{key}": value for key, value in hourly.items()}, **{f"group_{key}": value for key, value in groups.items()}})
    return output


def _holdout_rows(rows_by_policy: dict[str, list[dict[str, Any]]], policies: Iterable[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for holdout in PERIODS:
        training = [period for period in PERIODS if period != holdout]
        training_metrics = {
            policy: _aggregate_event_rows(
                [row for row in rows_by_policy.get(policy, []) if _issue_period(row) in training],
                RAIN_EVENT,
                WINDOW_HOURS,
            )
            for policy in policies
        }
        control = training_metrics[CONTROL]
        ranked = [
            (rank, policy) for policy, metrics in training_metrics.items()
            if (rank := _candidate_rank(metrics, control)) is not None
        ]
        selected = max(ranked)[1] if ranked else CONTROL
        holdout_selected = _aggregate_event_rows(
            [row for row in rows_by_policy[selected] if _issue_period(row) == holdout], RAIN_EVENT, WINDOW_HOURS
        )
        holdout_control = _aggregate_event_rows(
            [row for row in rows_by_policy[CONTROL] if _issue_period(row) == holdout], RAIN_EVENT, WINDOW_HOURS
        )
        output.append({
            "holdout_period": holdout,
            "training_periods": ",".join(training),
            "selected_policy": selected,
            "training_control_csi": control["CSI"],
            "training_selected_csi": training_metrics[selected]["CSI"],
            "training_control_pod": control["POD"],
            "training_selected_pod": training_metrics[selected]["POD"],
            "holdout_control_csi": holdout_control["CSI"],
            "holdout_selected_csi": holdout_selected["CSI"],
            "holdout_control_pod": holdout_control["POD"],
            "holdout_selected_pod": holdout_selected["POD"],
            "holdout_control_far": holdout_control["FAR"],
            "holdout_selected_far": holdout_selected["FAR"],
            "holdout_control_forecast_hours": holdout_control["forecast_events"],
            "holdout_selected_forecast_hours": holdout_selected["forecast_events"],
        })
    return output


def _issuance_count_vectors(rows: list[dict[str, Any]]) -> tuple[list[str], np.ndarray]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["issuance_key"]].append(row)
    keys = sorted(grouped)
    values = []
    for key in keys:
        counts, _ = _matching_counts(grouped[key], RAIN_EVENT, WINDOW_HOURS)
        values.append([counts[field] for field in ("hits", "misses", "false_alarms", "correct_negatives")])
    return keys, np.asarray(values, dtype=np.int64)


def _bootstrap_metric_deltas(
    rows_by_policy: dict[str, list[dict[str, Any]]], candidates: tuple[str, ...]
) -> list[dict[str, Any]]:
    common_keys = set.intersection(*[{row["issuance_key"].split("|", 1)[1] for row in rows_by_policy[policy]} for policy in (CONTROL, *candidates)])
    vectors: dict[str, np.ndarray] = {}
    ordered_keys = sorted(common_keys)
    for policy in (CONTROL, *candidates):
        by_issue = defaultdict(list)
        for row in rows_by_policy[policy]:
            issue = row["issuance_key"].split("|", 1)[1]
            if issue in common_keys:
                by_issue[issue].append(row)
        vectors[policy] = np.asarray([
            [_matching_counts(by_issue[issue], RAIN_EVENT, WINDOW_HOURS)[0][field] for field in ("hits", "misses", "false_alarms", "correct_negatives")]
            for issue in ordered_keys
        ], dtype=np.int64)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    sampled = rng.integers(0, len(ordered_keys), size=(BOOTSTRAP_REPLICATES, len(ordered_keys)))

    def metrics(vector: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        totals = vector[sampled].sum(axis=1)
        hits, misses, false_alarms, _ = (totals[:, index].astype(float) for index in range(4))
        pod = np.divide(hits, hits + misses, out=np.full_like(hits, np.nan), where=(hits + misses) > 0)
        far = np.divide(false_alarms, hits + false_alarms, out=np.full_like(hits, np.nan), where=(hits + false_alarms) > 0)
        csi = np.divide(hits, hits + misses + false_alarms, out=np.full_like(hits, np.nan), where=(hits + misses + false_alarms) > 0)
        return pod, far, csi

    control_metrics = metrics(vectors[CONTROL])
    results = []
    for policy in candidates:
        candidate_metrics = metrics(vectors[policy])
        for label, candidate, control in zip(("POD", "FAR", "CSI"), candidate_metrics, control_metrics):
            delta = candidate - control
            results.append({
                "policy": policy,
                "metric": label,
                "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                "delta_mean": round(float(np.nanmean(delta)), 5),
                "ci95_low": round(float(np.nanquantile(delta, 0.025)), 5),
                "ci95_high": round(float(np.nanquantile(delta, 0.975)), 5),
                "common_issuance_count": len(ordered_keys),
            })
    return results


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, holdouts: list[dict[str, Any]], bootstrap: list[dict[str, Any]], monthly: list[dict[str, Any]]) -> None:
    lines = [
        "# WAWP Rain Gate Holdout and Uncertainty Validation",
        "",
        "## Method",
        "",
        "For each month, candidate selection uses the other four months only. A candidate is eligible only when its training POD is no more than two percentage points below the control and its FAR is no worse. Among eligible policies, selection maximizes CSI, then HSS, then fewer forecast-event hours. The selected policy is then assessed on the omitted month.",
        "",
        "Event-window results use one-to-one rain matching within plus/minus two hours. Group results collapse continuous forecast rain wording into one forecast group per TAF, avoiding repeated hourly credit or penalty from a persistent BECMG group.",
        "",
        "## Leave-One-Month-Out Selection",
        "",
        "| Held-out month | Selected from other months | Holdout CSI: control -> selected | Holdout POD: control -> selected | Holdout FAR: control -> selected | Rain hours: control -> selected |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in holdouts:
        def pct(value: float | None) -> str:
            return "n/a" if value is None else f"{value * 100:.1f}%"
        lines.append(
            f"| {row['holdout_period']} | `{row['selected_policy']}` | {pct(row['holdout_control_csi'])} -> {pct(row['holdout_selected_csi'])} | "
            f"{pct(row['holdout_control_pod'])} -> {pct(row['holdout_selected_pod'])} | {pct(row['holdout_control_far'])} -> {pct(row['holdout_selected_far'])} | "
            f"{row['holdout_control_forecast_hours']} -> {row['holdout_selected_forecast_hours']} |"
        )
    lines.extend([
        "",
        "## Paired Bootstrap Confidence Intervals",
        "",
        "Deltas are candidate minus control across 2,000 paired resamples of the same TAF issuances. A confidence interval fully above zero is evidence of improvement for POD/CSI; fully below zero is evidence of a lower FAR.",
        "",
        "| Candidate | Metric | Mean delta | 95% interval |",
        "| --- | --- | ---: | ---: |",
    ])
    for row in bootstrap:
        lines.append(f"| `{row['policy']}` | {row['metric']} | {row['delta_mean']:+.4f} | [{row['ci95_low']:+.4f}, {row['ci95_high']:+.4f}] |")
    lines.extend([
        "",
        "## Guardrail",
        "",
        "This is still one five-month historical sample with repeated issuance windows. Holdout consistency and bootstrap intervals improve the evidence, but they do not replace future live shadow verification or forecaster review.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    root: Path,
    output_dir: Path,
    sweep_dir: Path | None = None,
    candidates: tuple[str, ...] = CANDIDATES,
) -> dict[str, Any]:
    sweep_dir = sweep_dir or root / "VERIFICATION_REPORTS" / "taf_gate_sweep_2026"
    replay_dir = sweep_dir / "verification"
    rows_by_policy = _replay_by_policy(root, replay_dir)
    policies = (CONTROL, *candidates)
    missing = [policy for policy in policies if policy not in rows_by_policy]
    if missing:
        raise ValueError(f"Missing gate-sweep policy output: {', '.join(missing)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    monthly = _monthly_metrics(rows_by_policy, policies)
    holdouts = _holdout_rows(rows_by_policy, policies)
    bootstrap = _bootstrap_metric_deltas(rows_by_policy, candidates)
    summary = {
        "scope": "historical gate-sweep holdout validation; no operational pipeline changes",
        "control": CONTROL,
        "sweep_dir": str(sweep_dir),
        "candidates": list(candidates),
        "rain_event": "quality-eligible measurable rain, one-to-one +/-2 hour matching",
        "selection_rule": "POD no more than 0.02 below control; FAR no worse; then maximize CSI, HSS, fewer forecast hours",
        "monthly_metrics": monthly,
        "holdout_selection": holdouts,
        "bootstrap": bootstrap,
    }
    _write_csv(output_dir / "monthly_rain_metrics.csv", monthly)
    _write_csv(output_dir / "holdout_selection.csv", holdouts)
    _write_csv(output_dir / "bootstrap_confidence_intervals.csv", bootstrap)
    (output_dir / "holdout_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_markdown(output_dir / "HOLDOUT_VALIDATION_REPORT.md", holdouts, bootstrap, monthly)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_gate_sweep_2026\holdout_validation"),
    )
    parser.add_argument(
        "--sweep-dir",
        type=Path,
        default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_gate_sweep_2026"),
        help="Existing sweep output containing the verification directory.",
    )
    parser.add_argument(
        "--candidates",
        nargs="+",
        default=list(CANDIDATES),
        help="Policy names to compare with control_current.",
    )
    args = parser.parse_args()
    result = run(args.root, args.output_dir, args.sweep_dir, tuple(args.candidates))
    print(json.dumps({"output_dir": str(args.output_dir), "holdout_selection": result["holdout_selection"]}, indent=2))


if __name__ == "__main__":
    main()
