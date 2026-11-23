"""Read-only preview rehearsal; output contains model guidance but never TAF text."""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from src.model_preview import export_preview
from src.model_preview_gate import validate
from src.storage_snapshot import file_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument("--expect", choices=("model_only", "unavailable"))
    args = parser.parse_args()
    now = args.as_of or datetime.now(timezone.utc)
    source_hash = file_digest(args.database)
    with closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        result = export_preview(conn, args.directory, now=now)
    verified = validate(args.database, args.directory, now=now)
    if not verified["passed"] or file_digest(args.database) != source_hash:
        raise ValueError(f"Preview validation failed: {verified}")
    if args.expect and result["status"] != args.expect:
        raise ValueError(f"Unexpected preview status: {result['status']}")
    print(f"PASS: {result['status']}, {len(result['models'])} models, {result['row_count']} source rows, TAF withheld and source database unchanged")


if __name__ == "__main__":
    main()
