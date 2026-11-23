"""Isolated recovery and equivalence exercise against a pinned historical DB.

Only the disposable copies below are mutated. No release or Pages writes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from src.storage_incremental_archive import RETIRED_TABLES, archive_incrementally, replicate, rclone_copy
from src.storage_ledger_split import quote, row_digest
from src.storage_snapshot import file_digest, manifest_path, pack, restore


def verify_union(source: Path, candidate: Path, segment: Path) -> dict:
    """Prove lossless reconstruction by exact typed row hashes and sequences."""
    uri = lambda path: path.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri(source), uri=True)) as original, closing(
        sqlite3.connect(uri(candidate), uri=True)
    ) as retained:
        retained.execute("ATTACH DATABASE ? AS archive", (uri(segment),))
        schema_query = "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY type,name"
        before_schema = original.execute(schema_query).fetchall()
        after_schema = [r for r in retained.execute(schema_query) if r[2] != "wawp_archive_segments"]
        if before_schema != after_schema:
            raise ValueError("Original schema or indexes changed")
        sequences = "SELECT name,seq FROM sqlite_sequence ORDER BY name,seq"
        if original.execute(sequences).fetchall() != retained.execute(sequences).fetchall():
            raise ValueError("AUTOINCREMENT high-water marks changed")
        result = {}
        for kind, name, _, _ in before_schema:
            if kind != "table" or name.startswith("sqlite_"):
                continue
            table = quote(name)
            order = "id" if name in RETIRED_TABLES else "rowid"
            before = row_digest(original, f"SELECT * FROM {table} ORDER BY {order}")
            hot = row_digest(retained, f"SELECT * FROM main.{table} ORDER BY {order}")
            if name in RETIRED_TABLES:
                cold = row_digest(retained, f"SELECT * FROM archive.{table} ORDER BY id")
                combined = row_digest(retained, f"SELECT * FROM main.{table} UNION ALL SELECT * FROM archive.{table} ORDER BY id")
                if before != combined or before["rows"] != hot["rows"] + cold["rows"]:
                    raise ValueError(f"Reconstruction differs: {name}")
                result[name] = {"original": before, "retained": hot, "archived": cold, "reconstructed": combined}
            else:
                if before != hot:
                    raise ValueError(f"Protected rows changed: {name}")
                result[name] = {"original": before, "retained": hot}
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--remote", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.remote.startswith("wawp:validation/"):
        parser.error("Only the isolated Drive validation prefix is permitted")
    args.work.mkdir(parents=True, exist_ok=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    source_hash = file_digest(args.source)
    candidate = args.work / "candidate.db"
    shutil.copyfile(args.source, candidate)
    archive = archive_incrementally(candidate, args.remote, args.work / "staging")
    if archive["status"] != "archived":
        raise ValueError("The historical baseline should contain archiveable rows")
    print("PASS: actual historical archive uploaded, read back and verified before eviction", flush=True)
    # Recover the historical object independently of the worker's staging directory.
    payload = args.work / archive["archive_name"]
    rclone_copy(archive["remote_path"], str(payload))
    rclone_copy(archive["remote_path"] + ".json", str(manifest_path(payload)))
    historical = args.work / "history-recovered.db"
    restored_manifest = restore(payload, historical)
    if restored_manifest != {k: v for k, v in archive.items() if k not in (
        "status", "remote_path", "operational_bytes_before", "operational_bytes_after"
    )}:
        raise ValueError("Re-downloaded archive manifest differs")
    tables = verify_union(args.source, candidate, historical)
    print("PASS: every original table reconstructed or retained with exact row hashes", flush=True)
    candidate_hash = file_digest(candidate)
    repeated = archive_incrementally(candidate, args.remote, args.work / "retry")
    if repeated["status"] != "no_new_history" or file_digest(candidate) != candidate_hash:
        raise ValueError("Retry changed the candidate")
    print("PASS: retry is a byte-exact no-op", flush=True)
    checkpoint = args.work / "operational.db.zst"
    checkpoint_manifest = pack(candidate, checkpoint)
    downloaded = replicate(checkpoint, args.remote, args.work)
    recovered = args.work / "operational-recovered.db"
    if restore(downloaded, recovered) != checkpoint_manifest or file_digest(recovered) != candidate_hash:
        raise ValueError("Operational checkpoint recovery differs")
    if file_digest(args.source) != source_hash:
        raise ValueError("Input source was modified")
    print("PASS: operational checkpoint restored byte-exactly from Drive", flush=True)
    report = {"status": "passed", "source_sha256": source_hash,
              "archive": archive, "checkpoint": checkpoint_manifest, "tables": tables,
              "retry_noop": True, "source_unchanged": True}
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": report["status"], "rows_archived": archive["retired"],
                      "before_bytes": archive["operational_bytes_before"],
                      "after_bytes": archive["operational_bytes_after"],
                      "checkpoint_bytes": checkpoint_manifest["archive_bytes"]}, indent=2))


if __name__ == "__main__":
    main()
