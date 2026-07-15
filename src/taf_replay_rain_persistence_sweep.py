"""Second-stage historical rain wording sweep around the 50/20 gate.

This replays the same January-May 2026 archive and METAR verification used by
the original gate sweep. It tests probability agreement, rain persistence, and
BECMG qualification only; it does not modify operational TAF guidance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.taf_replay_gate_sweep import GatePolicy, run as run_gate_sweep
from src.taf_gate_sweep_holdout import run as run_holdout


POLICIES = (
    GatePolicy("control_current", "Current operational rain gate and two-hour bridge.", 40.0, 0.0, 0.0, 2, "broad_current"),
    GatePolicy("rain_50_15", "50% wet-model probability with 15% agreement; no bridge.", 50.0, 0.15, 0.0, 0, "broad_current"),
    GatePolicy("rain_50_20", "Existing balanced candidate: 50% wet-model probability with 20% agreement; no bridge.", 50.0, 0.20, 0.0, 0, "broad_current"),
    GatePolicy("rain_50_25", "50% wet-model probability with 25% agreement; no bridge.", 50.0, 0.25, 0.0, 0, "broad_current"),
    GatePolicy("rain_50_20_persist2", "50/20 gate; require two contiguous qualifying rain hours before wording rain.", 50.0, 0.20, 0.0, 0, "broad_current", min_rain_signal_hours=2),
    GatePolicy("rain_50_20_persist3", "50/20 gate; require three contiguous qualifying rain hours before wording rain.", 50.0, 0.20, 0.0, 0, "broad_current", min_rain_signal_hours=3),
    GatePolicy("rain_50_20_becmg2", "50/20 gate; BECMG rain needs two rainy hours and 20% agreement, otherwise temporary wording.", 50.0, 0.20, 0.0, 0, "broad_current", becmg_min_rain_hours=2, becmg_min_agreement=0.20),
    GatePolicy("rain_50_20_becmg3", "50/20 gate; BECMG rain needs three rainy hours and 25% agreement, otherwise temporary wording.", 50.0, 0.20, 0.0, 0, "broad_current", becmg_min_rain_hours=3, becmg_min_agreement=0.25),
    GatePolicy("rain_50_20_persist2_becmg2", "50/20 gate; require two-hour rain persistence and two-hour, 20%-agreement BECMG qualification.", 50.0, 0.20, 0.0, 0, "broad_current", min_rain_signal_hours=2, becmg_min_rain_hours=2, becmg_min_agreement=0.20),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL"))
    parser.add_argument("--db", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\meteologix-wawp-main\wawp_forecasts.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_rain_persistence_sweep_2026"),
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit official issuance times for a smoke check.")
    args = parser.parse_args()
    result = run_gate_sweep(args.root, args.db, args.output_dir, args.limit, policies=POLICIES)
    holdout_dir = args.output_dir / "holdout_validation"
    holdout = run_holdout(
        args.root,
        holdout_dir,
        sweep_dir=args.output_dir,
        candidates=tuple(policy.name for policy in POLICIES if policy.name != "control_current"),
    )
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "holdout_dir": str(holdout_dir),
        "policies": [policy.name for policy in POLICIES],
        "holdout_selection": holdout["holdout_selection"],
    }, indent=2))


if __name__ == "__main__":
    main()
