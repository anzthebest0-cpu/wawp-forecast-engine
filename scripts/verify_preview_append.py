"""Verify append-only acquisition against the independently restored baseline."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.storage_ledger_split import quote, row_digest
from src.storage_snapshot import integrity_check


def verify(baseline, candidate, expected):
    for path in (baseline, candidate):
        integrity_check(path)
    uri = lambda path: path.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri(baseline), uri=True)) as before, closing(sqlite3.connect(uri(candidate), uri=True)) as after:
        schema_sql = "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY type,name"
        original_schema = before.execute(schema_sql).fetchall()
        retained_schema = [r for r in after.execute(schema_sql) if r[2] != 'openmeteo_collection_receipts']
        if original_schema != retained_schema:
            # A previous cycle may already have receipts; compare without it
            # and separately prove every pre-existing receipt is preserved.
            if [r for r in original_schema if r[2] != 'openmeteo_collection_receipts'] != retained_schema:
                raise ValueError("Original schemas differ")
        report = {}
        for kind, name, _, _ in original_schema:
            if kind != 'table' or name.startswith('sqlite_'):
                continue
            table = quote(name)
            if name == 'openmeteo_collection_receipts':
                # Receipts have a composite natural key; join the prior keyset
                # via ATTACH rather than comparing only a prefix of rowids.
                after.execute("ATTACH DATABASE ? AS original", (uri(baseline),))
                rows = row_digest(before, f"SELECT * FROM {table} ORDER BY model,retrieved_at_utc")
                preserved = row_digest(after, f"SELECT n.* FROM main.{table} n JOIN original.{table} o USING(model,retrieved_at_utc) ORDER BY n.model,n.retrieved_at_utc")
            elif name == 'openmeteo_forecasts':
                maximum = before.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0]
                rows = row_digest(before, f"SELECT * FROM {table} ORDER BY id")
                preserved = row_digest(after, f"SELECT * FROM {table} WHERE id<=? ORDER BY id", (maximum,))
                total = after.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if total != rows['rows'] + expected:
                    raise ValueError("Unexpected native row count after acquisition")
            else:
                rows = row_digest(before, f"SELECT * FROM {table} ORDER BY rowid")
                preserved = row_digest(after, f"SELECT * FROM {table} ORDER BY rowid")
            if rows != preserved:
                raise ValueError(f"Original records changed: {name}")
            report[name] = rows
        return {"status": "passed", "preserved_tables": report, "new_native_rows": expected}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--acquisition-report', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = verify(args.baseline, args.candidate, json.loads(args.acquisition_report.read_text())['inserted_native_rows'])
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(f"PASS: all {len(report['preserved_tables'])} original table row hashes preserved; {report['new_native_rows']} newly inserted native rows")


if __name__ == '__main__':
    main()
