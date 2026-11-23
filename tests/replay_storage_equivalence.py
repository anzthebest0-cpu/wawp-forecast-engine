"""Run the same dashboard exporter against two disposable database copies."""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from src.db_manager import ForecastDB
from src.export_dashboard_data import export_all


def replay(database: Path, seed: Path, output: Path, timestamp: datetime) -> None:
    class ReplayClock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = timestamp.astimezone(tz or timezone.utc)
            return instant if tz is not None else instant.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return timestamp.astimezone(timezone.utc).replace(tzinfo=None)

    for name in (
        "src.export_dashboard_data", "src.awos_quality", "src.operational_residuals",
        "src.diurnal_analysis", "src.quantile_mapper", "src.db_manager",
    ):
        module = importlib.import_module(name)
        if hasattr(module, "datetime"):
            module.datetime = ReplayClock
    shutil.copytree(seed, output)
    engine = ForecastDB(str(database))
    try:
        export_all(engine, str(output))
    finally:
        engine.close()


def compare(baseline: Path, candidate: Path) -> dict:
    result = {"matching": [], "differences": []}
    files = {path.name for path in baseline.iterdir() if path.is_file()}
    files.update(path.name for path in candidate.iterdir() if path.is_file())
    for name in sorted(files):
        first, second = baseline / name, candidate / name
        if not first.exists() or not second.exists():
            result["differences"].append(name)
            continue
        if name == "db_health.json":
            old = json.loads(first.read_text())
            new = json.loads(second.read_text())
            # Physical file size is expected to change; all other health
            # and scientific fields must agree exactly.
            old.pop("size_mb", None)
            new.pop("size_mb", None)
            equal = old == new
        else:
            equal = first.read_bytes() == second.read_bytes()
        result["matching" if equal else "differences"].append(name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--timestamp", required=True, type=datetime.fromisoformat)
    args = parser.parse_args()
    if args.baseline.resolve() == args.candidate.resolve() or args.timestamp.tzinfo is None:
        parser.error("Provide distinct disposable databases and a timezone-aware timestamp")
    seed = Path(__file__).resolve().parents[1] / "docs" / "data"
    replay(args.baseline, seed, args.output / "baseline", args.timestamp)
    replay(args.candidate, seed, args.output / "candidate", args.timestamp)
    report = compare(args.output / "baseline", args.output / "candidate")
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(bool(report["differences"]))


if __name__ == "__main__":
    main()
