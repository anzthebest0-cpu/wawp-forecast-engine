"""Build a reversible compact candidate for the rolling operational database.

The rolling GitHub Release database must be small enough to restore, validate,
and upload reliably.  Raw one-minute AWOS observations are valuable source
data, but they are not required by the runtime pipeline: the operational
hourly table already retains its derived maximum gust value.  This tool copies
every table and index to a new SQLite database, except that it keeps
``awos_observations_1min`` as an empty compatible table.

It never modifies the source database and does not publish anything.  A report
is emitted only after row-count parity for every retained table is verified.

Example:
    python src/build_compact_operational_db.py --source wawp_forecasts.db
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any


RAW_MINUTE_TABLE = "awos_observations_1min"
RETENTION_TABLE = "operational_data_retention"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {
        name: int(conn.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0])
        for (name,) in tables
    }


def _schema_rows(conn: sqlite3.Connection, object_type: str) -> list[tuple[str, str, str]]:
    return conn.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type = ? AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY name",
        (object_type,),
    ).fetchall()


def _summary_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Small domain checks alongside full table row-count parity."""
    metrics: dict[str, Any] = {}
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='awos_observations'"
    ).fetchone():
        metrics["hourly_awos"] = dict(
            zip(
                ("rows", "first_obs", "last_obs", "rain_total", "gust_rows", "max_gust"),
                conn.execute(
                    """
                    SELECT COUNT(*), MIN(obs_time), MAX(obs_time),
                           COALESCE(SUM(rain_1h), 0),
                           COUNT(wind_gust_max), MAX(wind_gust_max)
                    FROM awos_observations
                    """
                ).fetchone(),
            )
        )
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='openmeteo_forecasts'"
    ).fetchone():
        metrics["openmeteo_forecasts"] = dict(
            zip(
                ("rows", "first_valid", "last_valid", "models", "historical_rows"),
                conn.execute(
                    """
                    SELECT COUNT(*), MIN(forecast_time), MAX(forecast_time), COUNT(DISTINCT model),
                           SUM(CASE WHEN run_init_utc = 'historical_forecast_api' THEN 1 ELSE 0 END)
                    FROM openmeteo_forecasts
                    """
                ).fetchone(),
            )
        )
    return metrics


def _validate(source: Path, candidate: Path) -> dict[str, Any]:
    source_conn = _connect_readonly(source)
    candidate_conn = _connect_readonly(candidate)
    try:
        source_counts = _table_counts(source_conn)
        candidate_counts = _table_counts(candidate_conn)
        retained_source_counts = {
            name: count for name, count in source_counts.items() if name != RAW_MINUTE_TABLE
        }
        retained_candidate_counts = {
            name: count
            for name, count in candidate_counts.items()
            if name not in {RAW_MINUTE_TABLE, RETENTION_TABLE}
        }
        raw_source_rows = source_counts.get(RAW_MINUTE_TABLE, 0)
        raw_candidate_rows = candidate_counts.get(RAW_MINUTE_TABLE)
        parity_ok = retained_source_counts == retained_candidate_counts
        raw_table_ok = raw_candidate_rows == 0
        retention_row = candidate_conn.execute(
            f"SELECT source_raw_minute_rows, retained_raw_minute_rows "
            f"FROM {_quote(RETENTION_TABLE)} WHERE id = 1"
        ).fetchone()
        manifest_ok = retention_row == (raw_source_rows, 0)

        return {
            "valid": parity_ok and raw_table_ok and manifest_ok,
            "source": str(source),
            "candidate": str(candidate),
            "source_size_bytes": source.stat().st_size,
            "candidate_size_bytes": candidate.stat().st_size,
            "source_table_counts": source_counts,
            "candidate_table_counts": candidate_counts,
            "retained_table_parity": parity_ok,
            "raw_minute_source_rows": raw_source_rows,
            "raw_minute_candidate_rows": raw_candidate_rows,
            "retention_manifest_ok": manifest_ok,
            "source_metrics": _summary_metrics(source_conn),
            "candidate_metrics": _summary_metrics(candidate_conn),
        }
    finally:
        candidate_conn.close()
        source_conn.close()


def build_compact_operational_db(source: Path, destination: Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Create and validate a compact candidate without modifying ``source``."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source database was not found: {source}")
    if source == destination:
        raise ValueError("Source and candidate database paths must be different.")
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"Candidate already exists: {destination}. Use --overwrite only for a disposable candidate."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".building")
    if staging.exists():
        if not overwrite:
            raise FileExistsError(f"Incomplete candidate already exists: {staging}")
        staging.unlink()

    source_conn = _connect_readonly(source)
    try:
        table_rows = _schema_rows(source_conn, "table")
        index_rows = _schema_rows(source_conn, "index")
        view_rows = _schema_rows(source_conn, "view")
        trigger_rows = _schema_rows(source_conn, "trigger")
        source_counts = _table_counts(source_conn)
    finally:
        source_conn.close()

    with sqlite3.connect(staging) as candidate_conn:
        candidate_conn.execute("PRAGMA foreign_keys = OFF")
        candidate_conn.execute("PRAGMA journal_mode = DELETE")
        candidate_conn.execute("PRAGMA synchronous = NORMAL")
        candidate_conn.execute("ATTACH DATABASE ? AS source_db", (str(source),))

        for _name, _table, ddl in table_rows:
            candidate_conn.execute(ddl)

        for table_name, _table, _ddl in table_rows:
            if table_name == RAW_MINUTE_TABLE:
                continue
            candidate_conn.execute(
                f"INSERT INTO main.{_quote(table_name)} SELECT * FROM source_db.{_quote(table_name)}"
            )

        sequence_rows = candidate_conn.execute(
            "SELECT name, seq FROM source_db.sqlite_sequence WHERE name <> ?", (RAW_MINUTE_TABLE,)
        ).fetchall()
        if sequence_rows:
            candidate_conn.executemany(
                "INSERT OR REPLACE INTO sqlite_sequence(name, seq) VALUES (?, ?)", sequence_rows
            )

        for _name, table_name, ddl in index_rows:
            candidate_conn.execute(ddl)
        for _name, _table, ddl in view_rows:
            candidate_conn.execute(ddl)
        for _name, _table, ddl in trigger_rows:
            candidate_conn.execute(ddl)

        candidate_conn.execute(
            f"""
            CREATE TABLE {_quote(RETENTION_TABLE)} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                built_at_utc TEXT NOT NULL,
                source_file_name TEXT NOT NULL,
                source_size_bytes INTEGER NOT NULL,
                raw_minute_table TEXT NOT NULL,
                source_raw_minute_rows INTEGER NOT NULL,
                retained_raw_minute_rows INTEGER NOT NULL,
                policy TEXT NOT NULL
            )
            """
        )
        candidate_conn.execute(
            f"INSERT INTO {_quote(RETENTION_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                source.name,
                source.stat().st_size,
                RAW_MINUTE_TABLE,
                source_counts.get(RAW_MINUTE_TABLE, 0),
                0,
                "Raw minute AWOS remains outside the rolling operational database; hourly derived gusts remain in awos_observations.",
            ),
        )
        candidate_conn.commit()
        # SQLite cannot detach an attached database while this copy transaction
        # is still open (notably on Windows). Commit before detaching so the
        # candidate remains self-contained before VACUUM compacts it.
        candidate_conn.execute("DETACH DATABASE source_db")
        candidate_conn.execute("VACUUM")
    # sqlite3's connection context manager commits but does not close the file.
    # Close it before atomically renaming the staging database on Windows.
    candidate_conn.close()

    report = _validate(source, staging)
    if not report["valid"]:
        raise RuntimeError(f"Candidate validation failed: {json.dumps(report, sort_keys=True)}")

    os.replace(staging, destination)
    report = _validate(source, destination)
    if not report["valid"]:
        raise RuntimeError(f"Candidate validation failed after finalization: {json.dumps(report, sort_keys=True)}")
    return report


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "wawp_forecasts.db")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "artifacts" / "operational" / "wawp_operational_candidate.db",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    report = build_compact_operational_db(args.source, args.output, overwrite=args.overwrite)
    report["built_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    report_path = args.report or args.output.with_suffix(args.output.suffix + ".report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reduction = 100 * (1 - report["candidate_size_bytes"] / report["source_size_bytes"])
    print(f"Compact candidate built: {args.output}")
    print(f"Validation: {'passed' if report['valid'] else 'failed'}")
    print(f"Size: {report['source_size_bytes']:,} -> {report['candidate_size_bytes']:,} bytes ({reduction:.1f}% smaller)")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
