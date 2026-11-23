"""Promote the one previously verified September checkpoint, without forecasting."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scripts.validate_remote_archive import verify_union
from src.storage_incremental_archive import replicate, rclone_copy
from src.storage_snapshot import file_digest, manifest_path, pack, restore

BASELINE_ASSET = "wawp_forecasts-34999537381-1.db"
BASELINE_SHA = "abcf50f0c254bc93ffed010b1ccc2415cd4e079d651f99ebed8418b93ce2a6ea"
BASELINE_BYTES = 2145767424
VALIDATION = "wawp:validation/run-45202071449-1"
SEGMENT_SHA = "08b036fc59e9f7805b758f40feda98164337b097ff6618356d2ffc85e469cb93"
SEGMENT_NAME = f"wawp-history-{SEGMENT_SHA}.db.zst"
CANDIDATE_SHA = "0d6e57d0fc0e0931c3b093501008ecfda29f409576ceba0a1936e49b175a31ab"
CANDIDATE_ARCHIVE_SHA = "552cf6bf445cb1057ff4cea10229063b1cb813c5311285de4c949f7c4a0d6a89"
PRODUCTION_HISTORY = "wawp:incremental"
PRODUCTION_CHECKPOINTS = "wawp:checkpoints"
EXPECTED_ROWS = {"qm_corrections_applied": 6150144, "meteologix_forecasts": 784128}


def require_pinned_release(pages: list[list[dict]]) -> None:
    from src.storage_snapshot import select_asset

    if select_asset(pages) != BASELINE_ASSET:
        raise ValueError("Rolling release changed or cutover already completed; stop for review")
    matching = [a for page in pages for a in page if a["name"] == BASELINE_ASSET and a["state"] == "uploaded"]
    if len(matching) != 1 or (matching[0]["size"], matching[0].get("digest")) != (
        BASELINE_BYTES, f"sha256:{BASELINE_SHA}"
    ):
        raise ValueError("Original release asset is not the rehearsed byte-exact baseline")


def download_pair(remote_file: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rclone_copy(remote_file, str(path))
    rclone_copy(remote_file + ".json", str(manifest_path(path)))


def require_receipt(conn: sqlite3.Connection, manifest: dict, old_remote: str) -> str:
    rows = conn.execute("SELECT archive_name,remote_path,manifest_json FROM wawp_archive_segments").fetchall()
    if len(rows) != 1:
        raise ValueError("Unexpected number of archive receipts")
    name, path, payload = rows[0]
    if name != manifest["archive_name"] or path != old_remote or json.loads(payload) != manifest:
        raise ValueError("Receipt is not the validated archive")
    return name


def promote_receipt(candidate: Path, manifest: dict, old_remote: str, new_remote: str) -> None:
    """Repoint only the exact existing receipt after verifying the new remote."""
    with closing(sqlite3.connect(candidate)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            name = require_receipt(conn, manifest, old_remote)
            changed = conn.execute(
                "UPDATE wawp_archive_segments SET remote_path=?,verified_at_utc=? WHERE archive_name=? AND remote_path=?",
                (new_remote, datetime.now(timezone.utc).isoformat(), name, old_remote),
            ).rowcount
            if changed != 1:
                raise ValueError("Receipt promotion did not update exactly one row")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def promote(source: Path, output: Path, work: Path) -> dict:
    if (source.stat().st_size, file_digest(source)) != (BASELINE_BYTES, BASELINE_SHA):
        raise ValueError("Source differs from the verified original release")
    work.mkdir(parents=True, exist_ok=False)
    archive = work / "validated" / SEGMENT_NAME
    download_pair(f"{VALIDATION}/{SEGMENT_NAME}", archive)
    if file_digest(archive) != SEGMENT_SHA:
        raise ValueError("Validated historical payload SHA mismatch")
    manifest = json.loads(manifest_path(archive).read_text())
    if (manifest["source_sha256"], manifest["archive_name"], manifest["archive_sha256"], manifest["archive_bytes"]) != (
        BASELINE_SHA, SEGMENT_NAME, SEGMENT_SHA, archive.stat().st_size
    ):
        raise ValueError("Archive source or name does not match the tested baseline")
    if {k: v["rows"] for k, v in manifest["retired"].items()} != EXPECTED_ROWS:
        raise ValueError("Archive row counts differ from the validated rehearsal")
    validated_checkpoint = work / "validated" / "operational.db.zst"
    download_pair(f"{VALIDATION}/operational.db.zst", validated_checkpoint)
    if file_digest(validated_checkpoint) != CANDIDATE_ARCHIVE_SHA:
        raise ValueError("Validated checkpoint compressed bytes differ")
    candidate = work / "operational.db"
    restore(validated_checkpoint, candidate)
    if file_digest(candidate) != CANDIDATE_SHA:
        raise ValueError("Validated checkpoint database bytes differ")
    old_remote, new_remote = f"{VALIDATION}/{SEGMENT_NAME}", f"{PRODUCTION_HISTORY}/{SEGMENT_NAME}"
    with closing(sqlite3.connect(candidate.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as original:
        require_receipt(original, manifest, old_remote)
    promotion_dir = work / "archive-promotion"
    promotion_dir.mkdir()
    # Both pinned inputs and their matching receipt are verified before writes.
    published = replicate(archive, PRODUCTION_HISTORY, promotion_dir)
    recovered_history = work / "history-recovered.db"
    if restore(published, recovered_history) != manifest:
        raise ValueError("Production archive manifest differs from verified input")
    print("PASS: immutable production history copied and independently restored from Drive", flush=True)
    promote_receipt(candidate, manifest, old_remote, new_remote)
    tables = verify_union(source, candidate, recovered_history)
    if file_digest(source) != BASELINE_SHA:
        raise ValueError("Original baseline changed")
    print("PASS: all original table hashes, schema, row union and sequences unchanged", flush=True)

    checkpoint = pack(candidate, output)
    checkpoint_dir = work / "checkpoint-promotion"
    checkpoint_dir.mkdir()
    readback = replicate(output, PRODUCTION_CHECKPOINTS, checkpoint_dir)
    recovered = work / "checkpoint-recovered.db"
    if restore(readback, recovered) != checkpoint or file_digest(recovered) != checkpoint["database_sha256"]:
        raise ValueError("Promoted checkpoint recovery differs")
    print("PASS: promoted rolling checkpoint restored byte-exactly from Drive", flush=True)
    return {"status": "passed", "source_sha256": BASELINE_SHA,
            "history": manifest, "checkpoint": checkpoint, "tables": tables,
            "production_history": f"{PRODUCTION_HISTORY}/{SEGMENT_NAME}",
            "production_checkpoint": f"{PRODUCTION_CHECKPOINTS}/{output.name}",
            "receipt_only_update": True, "rehearsal_run": 45202071449}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-assets", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--check-release-only", action="store_true")
    args = parser.parse_args()
    require_pinned_release(json.loads(args.release_assets.read_text()))
    if args.check_release_only:
        print("PASS: current release is the exact rehearsed source")
        return
    if not all((args.source, args.output, args.work, args.report)):
        parser.error("Source, output, work and report are required for promotion")
    report = promote(args.source, args.output, args.work)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
