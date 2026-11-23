"""Create a read-only-source prototype separating the historical QM ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from src.storage_snapshot import file_digest, integrity_check

LEDGER = "qm_corrections_applied"
LEGACY = "meteologix_forecasts"
TRAINING = "qm_training_pairs"
CALIBRATION = "qm_cdfs"


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def row_digest(conn: sqlite3.Connection, query: str, parameters=()) -> dict:
    """Hash ordered typed rows, including explicit nulls and duplicate rows."""
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(query, parameters):
        # SQLite returns only int, float, str, bytes and None. repr preserves
        # these distinctions and every finite double's round-trip value.
        digest.update(repr(row).encode("utf-8") + b"\n")
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def legacy_query(prefix: str, archive: bool) -> str:
    # Match ForecastDB.get_latest_forecasts: one maximum run per location/model.
    table = prefix + quote(LEGACY)
    comparison = "<" if archive else "="
    return (
        f"SELECT m.* FROM {table} m JOIN ("
        f"SELECT location,model,MAX(run_init_utc) AS latest_run FROM {table} GROUP BY location,model"
        f") latest USING(location,model) WHERE m.run_init_utc {comparison} latest.latest_run ORDER BY m.id"
    )


def split(
    source: Path, destination: Path, *,
    archive_legacy: bool = False, archive_training: bool = False,
) -> dict:
    """Write disjoint ledger partitions and a full-data operational candidate.

    The latest ledger run remains in the operational candidate for retry
    deduplication. The remaining ledger rows retain their original IDs and
    schema in an independent immutable archive. Nothing is deleted from source.
    """
    source = source.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    integrity_check(source)
    before = file_digest(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = source.as_uri() + "?mode=ro&immutable=1"
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "split"
        staged.mkdir()
        candidate = staged / "operational.db"
        archived = staged / (
            "historical_tables.db" if archive_legacy or archive_training else "correction_history.db"
        )
        with closing(sqlite3.connect(source_uri, uri=True)) as original:
            schema = original.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
                "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 "
                "WHEN 'view' THEN 2 ELSE 3 END,name"
            ).fetchall()
            tables = [name for kind, name, _, _ in schema if kind == "table"]
            if LEDGER not in tables:
                raise ValueError("Missing correction ledger")
            if archive_legacy and LEGACY not in tables:
                raise ValueError("Missing legacy forecast table")
            archived_tables = {LEDGER, LEGACY} if archive_legacy else {LEDGER}
            if archive_training:
                if not {TRAINING, CALIBRATION}.issubset(tables):
                    raise ValueError("Training or calibration table missing")
                # The small CDF table stays in both stores. The offline worker
                # receives the exact same seed as the forecasting engine.
                archived_tables.update({TRAINING, CALIBRATION})
            current_run = original.execute(
                "SELECT MAX(scraped_at) FROM openmeteo_forecasts "
                "WHERE run_init_utc <> 'historical_forecast_api' AND lead_hours>=0"
            ).fetchone()[0]
            # Keep every row for the most recent operational run, even when that
            # run is retried. IS / IS NOT also partition NULL safely.
            current_sql = f"SELECT * FROM {LEDGER} WHERE pipeline_run_id IS ? ORDER BY id"
            history_sql = f"SELECT * FROM {LEDGER} WHERE pipeline_run_id IS NOT ? ORDER BY id"
            sequences = dict(original.execute(
                "SELECT name,MAX(seq) FROM sqlite_sequence GROUP BY name"
            ))
            for path, history in ((candidate, False), (archived, True)):
                with closing(sqlite3.connect(path, uri=True)) as target:
                    target.execute("ATTACH DATABASE ? AS original", (source_uri,))
                    for kind, name, table, ddl in schema:
                        if history and table not in archived_tables:
                            continue
                        if kind == "table":
                            target.execute(ddl)
                            if name == LEDGER:
                                relation = "IS NOT" if history else "IS"
                                target.execute(
                                    f"INSERT INTO {quote(name)} SELECT * FROM original.{quote(name)} "
                                    f"WHERE pipeline_run_id {relation} ? ORDER BY id",
                                    (current_run,),
                                )
                            elif name == LEGACY and archive_legacy:
                                target.execute(f"INSERT INTO {quote(name)} " + legacy_query("original.", history))
                            elif name == TRAINING and archive_training and not history:
                                continue
                            else:
                                target.execute(
                                    f"INSERT INTO {quote(name)} SELECT * FROM original.{quote(name)} ORDER BY rowid"
                                )
                        else:
                            target.execute(ddl)
                    # Preserve high-water marks even when the newest ID is archived.
                    target_tables = archived_tables if history else tables
                    for name in target_tables:
                        if name in sequences:
                            target.execute("DELETE FROM sqlite_sequence WHERE name=?", (name,))
                            target.execute(
                                "INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)",
                                (name, sequences[name]),
                            )
                    target.commit()
                    target.execute("DETACH DATABASE original")
            report = {
                "source_sha256": before,
                "source_bytes": source.stat().st_size,
                "retained_pipeline_run_id": current_run,
                "archived_tables": sorted(archived_tables),
                "tables": {},
            }
            with closing(sqlite3.connect(candidate)) as hot, closing(sqlite3.connect(archived)) as cold:
                for name in tables:
                    if name == LEDGER:
                        current = row_digest(original, current_sql, (current_run,))
                        history = row_digest(original, history_sql, (current_run,))
                        target_sql = f"SELECT * FROM {LEDGER} ORDER BY id"
                        if current != row_digest(hot, target_sql) or history != row_digest(cold, target_sql):
                            raise ValueError("Correction ledger partition mismatch")
                        report["ledger"] = {"operational": current, "archive": history}
                    elif name == LEGACY and archive_legacy:
                        current = row_digest(original, legacy_query("", False))
                        history = row_digest(original, legacy_query("", True))
                        target_sql = f"SELECT * FROM {quote(LEGACY)} ORDER BY id"
                        if current != row_digest(hot, target_sql) or history != row_digest(cold, target_sql):
                            raise ValueError("Legacy forecast partition mismatch")
                        report["legacy_forecasts"] = {"operational": current, "archive": history}
                    elif name == TRAINING and archive_training:
                        query = f"SELECT * FROM {quote(name)} ORDER BY rowid"
                        expected = row_digest(original, query)
                        if expected != row_digest(cold, query):
                            raise ValueError("Training archive mismatch")
                        retained = row_digest(hot, query)
                        if retained["rows"]:
                            raise ValueError("Operational training cache must be empty")
                        report["training_pairs"] = {"operational": retained, "archive": expected}
                    else:
                        query = f"SELECT * FROM {quote(name)} ORDER BY rowid"
                        expected = row_digest(original, query)
                        if expected != row_digest(hot, query):
                            raise ValueError(f"Retained data mismatch: {name}")
                        if name == CALIBRATION and archive_training and expected != row_digest(cold, query):
                            raise ValueError("Calibration seed mismatch")
                        report["tables"][name] = expected
        for path in (candidate, archived):
            integrity_check(path)
        if file_digest(source) != before:
            raise ValueError("Source database changed during split")
        report["operational_bytes"] = candidate.stat().st_size
        report["archive_bytes"] = archived.stat().st_size
        report["operational_sha256"] = file_digest(candidate)
        report["archive_sha256"] = file_digest(archived)
        (staged / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        staged.rename(destination)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--archive-legacy", action="store_true")
    parser.add_argument("--archive-training", action="store_true")
    args = parser.parse_args()
    print(json.dumps(split(
        args.source, args.destination,
        archive_legacy=args.archive_legacy, archive_training=args.archive_training,
    ), indent=2))


if __name__ == "__main__":
    main()
