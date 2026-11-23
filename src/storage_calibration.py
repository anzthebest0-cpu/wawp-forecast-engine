"""Train a versioned QM artifact from a preserved, detached training database."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

from src.qm_runtime_artifact import export_qm_runtime_artifact
from src.storage_ledger_split import row_digest
from src.storage_snapshot import file_digest, integrity_check
from src.train_qm_multiparam import train_all


def train_archive(source: Path, output: Path) -> dict:
    """Reuse the established trainer, preserving its full pairs and seed CDFs."""
    if output.exists():
        raise FileExistsError(output)
    integrity_check(source)
    source_sha = file_digest(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        working = Path(temporary) / "training.db"
        staged = Path(temporary) / "artifact"
        staged.mkdir()
        shutil.copyfile(source, working)
        with closing(sqlite3.connect(working)) as conn:
            pairs = row_digest(conn, "SELECT * FROM qm_training_pairs ORDER BY rowid")
            seed = row_digest(conn, "SELECT * FROM qm_cdfs ORDER BY id")
        if not pairs["rows"]:
            raise ValueError("No training pairs available")
        train_all(str(working), str(staged))
        summary = export_qm_runtime_artifact(working, staged)
        with closing(sqlite3.connect(working)) as conn:
            if pairs != row_digest(conn, "SELECT * FROM qm_training_pairs ORDER BY rowid"):
                raise ValueError("Training changed the archived sample")
        if source_sha != file_digest(source):
            raise ValueError("Source changed during calibration")
        artifact = staged / "qm_runtime.sqlite"
        integrity_check(artifact)
        # Content hashes identify the exact operator independently of mutable
        # SQLite integer IDs and document the approved input sample.
        manifest = {
            "source_sha256": source_sha,
            "training_pairs": pairs,
            "seed_cdfs": seed,
            "artifact_sha256": file_digest(artifact),
            "artifact_bytes": artifact.stat().st_size,
            "enabled_cdfs": summary["enabled_cdfs"],
            "total_cdfs": summary["total_cdfs"],
            "dependencies": {name: version(name) for name in ("numpy", "pandas")},
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "trainer_sha256": file_digest(Path(__file__).with_name("quantile_mapper.py")),
            "trainer_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True
            ).strip(),
        }
        (staged / "calibration_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staged.rename(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("training_database", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(train_archive(args.training_database, args.output), indent=2))


if __name__ == "__main__":
    main()
