"""Archive closed forecast records, verify remote bytes, then evict locally."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from src.storage_ledger_split import CALIBRATION, LEDGER, LEGACY, legacy_query, quote, row_digest
from src.storage_snapshot import file_digest, manifest_path, pack, require_closed, restore
from src.advanced_ensemble_weighter import MODELS

RETIRED_TABLES = (LEDGER, LEGACY)


def guard_retired_schema(conn: sqlite3.Connection) -> None:
    """Avoid accidental cascading mutations if future schemas add dependencies."""
    trigger = conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='trigger' "
        "AND tbl_name IN (?, ?, 'wawp_archive_segments')", RETIRED_TABLES
    ).fetchone()
    if trigger:
        raise ValueError("Retirement requires review of archive-table triggers")
    for (table,) in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        if any(row[2] in RETIRED_TABLES for row in conn.execute(f"PRAGMA foreign_key_list({quote(table)})")):
            raise ValueError("Retirement requires review of foreign-key dependencies")


def selection_queries(conn: sqlite3.Connection, prefix: str = "") -> tuple[str | None, dict]:
    """Keep the latest successfully recorded ledger run and legacy model cycles."""
    ledger = prefix + quote(LEDGER)
    last_recorded = conn.execute(f"SELECT MAX(pipeline_run_id) FROM {ledger}").fetchone()[0]
    placeholders = ",".join("?" for _ in MODELS)
    latest_native = conn.execute(
        f"SELECT MAX(scraped_at) FROM {prefix}openmeteo_forecasts "
        f"WHERE run_init_utc <> 'historical_forecast_api' AND lead_hours >= 0 "
        f"AND model IN ({placeholders})", tuple(MODELS),
    ).fetchone()[0]
    # Keep both the latest recorded run and a potentially older current
    # model cohort. A fallback export can legitimately reuse old run IDs.
    current_run = min(last_recorded, latest_native) if last_recorded and latest_native else None
    return current_run, {
        LEDGER: (f"SELECT * FROM {ledger} WHERE pipeline_run_id < ? ORDER BY id", (current_run,)),
        LEGACY: (legacy_query(prefix, True), ()),
    }


def stage_delta(source: Path, work: Path) -> tuple[Path, dict] | None:
    """Prepare a self-contained immutable archive while leaving source unchanged."""
    require_closed(source)
    source_hash = file_digest(source)
    source_uri = source.resolve().as_uri() + "?mode=ro&immutable=1"
    raw = work / "history.db"

    with closing(sqlite3.connect(source_uri, uri=True)) as src:
        guard_retired_schema(src)
        current_run, queries = selection_queries(src)
        retired = {table: row_digest(src, sql, params) for table, (sql, params) in queries.items()}
        if not any(item["rows"] for item in retired.values()):
            return None
        calibration = row_digest(src, f"SELECT * FROM {quote(CALIBRATION)} ORDER BY id")
        tables = (*RETIRED_TABLES, CALIBRATION)
        schema = src.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE sql IS NOT NULL AND tbl_name IN (?, ?, ?) AND type IN ('table','index') "
            "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name", tables,
        ).fetchall()

    with closing(sqlite3.connect(raw, uri=True)) as target:
        target.execute("ATTACH DATABASE ? AS original", (source_uri,))
        _, archive_queries = selection_queries(target, "original.")
        for kind, name, _, ddl in schema:
            target.execute(ddl)
            if kind != "table":
                continue
            if name in archive_queries:
                sql, params = archive_queries[name]
            else:
                sql, params = f"SELECT * FROM original.{quote(name)} ORDER BY id", ()
            target.execute(f"INSERT INTO {quote(name)} " + sql, params)
        for table, expected in {**retired, CALIBRATION: calibration}.items():
            if expected != row_digest(target, f"SELECT * FROM {quote(table)} ORDER BY id"):
                raise ValueError(f"Archive row mismatch: {table}")
        metadata = {
            "source_sha256": source_hash,
            "retained_ledger_run": current_run,
            "retired": retired,
            "calibration_snapshot": calibration,
        }
        target.execute("CREATE TABLE archive_metadata (metadata_json TEXT NOT NULL)")
        target.execute("INSERT INTO archive_metadata VALUES (?)", (json.dumps(metadata, sort_keys=True),))
        target.commit()
        target.execute("DETACH DATABASE original")
    require_closed(source)
    if file_digest(source) != source_hash:
        raise ValueError("Source changed while archival delta was staged")

    temporary = work / "payload.db.zst"
    snapshot = pack(raw, temporary)
    archive = work / f"wawp-history-{snapshot['archive_sha256']}.db.zst"
    temporary.rename(archive)
    manifest = {**snapshot, "incremental_archive_version": 1, "archive_name": archive.name, **metadata}
    manifest_path(archive).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    # The staging directory is disposable; remove only the temporary manifest
    # emitted by pack after the complete extended manifest exists.
    manifest_path(temporary).unlink()
    return archive, manifest


def rclone_copy(source: str, destination: str) -> None:
    subprocess.run(
        ["rclone", "copyto", "--immutable", "--checksum", "--retries", "3", "--low-level-retries", "5",
         "--contimeout", "30s", "--timeout", "2m", source, destination],
        check=True,
    )


def replicate(archive: Path, remote: str, work: Path) -> Path:
    """Read back both objects from the destination before allowing eviction."""
    destination = remote.rstrip("/") + "/" + archive.name
    readback = work / "readback"
    readback.mkdir()
    downloaded = readback / archive.name

    rclone_copy(str(archive), destination)
    rclone_copy(destination, str(downloaded))
    if file_digest(downloaded) != file_digest(archive):
        raise ValueError("Remote archive payload differs from staged archive")

    # A manifest is the commit marker. Publish it only after the data object
    # has been independently fetched and its exact bytes checked.
    rclone_copy(str(manifest_path(archive)), destination + ".json")
    rclone_copy(destination + ".json", str(manifest_path(downloaded)))
    if file_digest(manifest_path(downloaded)) != file_digest(manifest_path(archive)):
        raise ValueError("Remote archive manifest differs from staged manifest")
    return downloaded


def evict_verified(source: Path, verified: Path, manifest: dict, remote: str) -> dict:
    """Evict only rows proven equal to the remotely restored snapshot.

    The verified segment is immutable. The runner's checkpoint and its segment
    receipt are changed in the same SQLite transaction. No native forecast,
    observation, training pair, or runtime CDF row is removed here.
    """
    require_closed(source)
    archive_uri = verified.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(source, uri=True)) as conn:
        conn.execute("ATTACH DATABASE ? AS history", (archive_uri,))
        conn.execute("BEGIN IMMEDIATE")
        try:
            if file_digest(source) != manifest["source_sha256"]:
                raise ValueError("Source changed before archival eviction")
            guard_retired_schema(conn)
            embedded = json.loads(conn.execute("SELECT metadata_json FROM history.archive_metadata").fetchone()[0])
            for key in ("source_sha256", "retained_ledger_run", "retired", "calibration_snapshot"):
                if embedded[key] != manifest[key]:
                    raise ValueError("Archive metadata disagrees with manifest")
            if manifest["calibration_snapshot"] != row_digest(
                conn, f"SELECT * FROM history.{quote(CALIBRATION)} ORDER BY id"
            ):
                raise ValueError("Calibration snapshot mismatch")
            current_run, queries = selection_queries(conn)
            if current_run != manifest["retained_ledger_run"]:
                raise ValueError("Current ledger run changed before eviction")
            for table, (query, params) in queries.items():
                expected = manifest["retired"][table]
                if row_digest(conn, query, params) != expected:
                    raise ValueError(f"Retirement selection changed: {table}")
                if row_digest(conn, f"SELECT * FROM history.{quote(table)} ORDER BY id") != expected:
                    raise ValueError(f"Verified archive contents changed: {table}")

            # Record the committed remote location in the rolling DB itself.
            # The AUTOINCREMENT high-water marks survive the DELETE statements.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS wawp_archive_segments (
                    archive_name TEXT PRIMARY KEY,
                    remote_path TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    verified_at_utc TEXT NOT NULL
                )"""
            )
            conn.execute(
                "INSERT INTO wawp_archive_segments VALUES (?, ?, ?, ?)",
                (manifest["archive_name"], remote.rstrip("/") + "/" + manifest["archive_name"],
                 json.dumps(manifest, sort_keys=True), datetime.now(timezone.utc).isoformat()),
            )
            for table in RETIRED_TABLES:
                deleted = conn.execute(
                    f"DELETE FROM main.{quote(table)} WHERE id IN "
                    f"(SELECT id FROM history.{quote(table)})"
                ).rowcount
                if deleted != manifest["retired"][table]["rows"]:
                    raise ValueError(f"Unexpected number of evicted rows: {table}")
            if conn.execute("PRAGMA main.quick_check").fetchall() != [("ok",)]:
                raise ValueError("Operational database integrity check failed")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.execute("DETACH DATABASE history")

    return {"status": "archived", "remote_path": remote.rstrip("/") + "/" + manifest["archive_name"], **manifest}


def archive_incrementally(source: Path, remote: str, work: Path) -> dict:
    if not remote.strip():
        raise ValueError("Archive destination is required")
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as directory:
        staging = Path(directory)
        staged = stage_delta(source, staging)
        if staged is None:
            return {"status": "no_new_history", "retired": {}}
        archive, manifest = staged
        readback = replicate(archive, remote, staging)
        verified = staging / "verified.db"
        if restore(readback, verified) != manifest:
            raise ValueError("Remotely restored manifest differs from staged manifest")
        before = source.stat().st_size
        report = evict_verified(source, verified, manifest, remote)
        # Compaction follows the transactional receipt. If the disk is full
        # during VACUUM, the verified archive and the committed DB remain valid.
        with closing(sqlite3.connect(source)) as conn:
            conn.execute("VACUUM")
        report["operational_bytes_before"] = before
        report["operational_bytes_after"] = source.stat().st_size
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--remote", required=True, help="Configured rclone destination")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = archive_incrementally(args.database, args.remote, args.work)
    except Exception as exc:
        args.report.write_text(json.dumps({"status": "failed", "error_type": type(exc).__name__}) + "\n")
        raise
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "retired": report["retired"]}, indent=2))


if __name__ == "__main__":
    main()
