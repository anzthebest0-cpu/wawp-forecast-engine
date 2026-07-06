import argparse
import json
from pathlib import Path

from src.qm_runtime_artifact import export_qm_runtime_artifact


def main() -> int:
    project_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Export compact QM runtime artifact from the full WAWP SQLite archive."
    )
    parser.add_argument(
        "--db",
        default=str(project_root / "wawp_forecasts.db"),
        help="Source full archive SQLite database.",
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(project_root / "artifacts" / "qm"),
        help="Output directory for qm_runtime.sqlite and qm_state_summary.json.",
    )
    parser.add_argument(
        "--dashboard-data-dir",
        default=str(project_root / "docs" / "data"),
        help="Optional dashboard data directory where qm_state_summary.json is copied.",
    )
    args = parser.parse_args()

    summary = export_qm_runtime_artifact(
        args.db,
        artifact_dir=args.artifact_dir,
        dashboard_data_dir=args.dashboard_data_dir,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("enabled_cdfs", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
