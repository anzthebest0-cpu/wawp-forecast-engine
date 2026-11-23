"""Collect, verify private source evidence, and validate a disposable preview."""
from __future__ import annotations

import argparse
import json
import sqlite3
import hashlib
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from src.model_acquisition import CONTRACT, collect, ingest_immutable, parse_response
from src.model_preview import export_preview
from src.model_preview_gate import validate
from src.storage_incremental_archive import rclone_copy
from src.storage_snapshot import file_digest, integrity_check


def archive_responses(responses, outcomes, work: Path, remote: str):
    if not remote.startswith(("wawp:validation/fresh-acquisition/", "wawp:source-responses/")):
        raise ValueError("Unexpected source evidence destination")
    work.mkdir(parents=True, exist_ok=False)
    staged = work / "responses.zip"
    archive_meta = {"contract": CONTRACT, "outcomes": outcomes,
                    "collections": [metadata for metadata, _, _ in responses]}
    with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as blob:
        def add(name, content):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            blob.writestr(info, content, compresslevel=6)
        for meta, body, _ in responses:
            add(f"{meta['model']}.json", body)
        add("manifest.json", json.dumps(archive_meta, sort_keys=True, indent=2, allow_nan=False) + "\n")
    digest = file_digest(staged)
    name = f"source-responses-{digest}.zip"
    uri = remote.rstrip("/") + "/" + name
    rclone_copy(str(staged), uri)
    readback = work / "readback.zip"
    rclone_copy(uri, str(readback))
    if file_digest(readback) != digest:
        raise ValueError("Independent source-evidence readback mismatch")
    restored = []
    with zipfile.ZipFile(readback) as blob:
        if blob.testzip() is not None or json.loads(blob.read("manifest.json")) != archive_meta:
            raise ValueError("Source evidence manifest mismatch")
        for meta in archive_meta["collections"]:
            body = blob.read(f"{meta['model']}.json")
            if hashlib.sha256(body).hexdigest() != meta["payload_sha256"]:
                raise ValueError("Source payload mismatch")
            rows, _ = parse_response(body, meta["model"], datetime.fromisoformat(meta["retrieved_at_utc"]).replace(tzinfo=timezone.utc), meta["requested_fields"])
            restored.append((meta, body, rows))
    return restored, uri, digest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", required=True, type=Path, help="Disposable writable DB copy")
    p.add_argument("--directory", required=True, type=Path)
    p.add_argument("--work", required=True, type=Path)
    p.add_argument("--remote", required=True)
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--budget-seconds", type=int, default=240)
    args = p.parse_args()
    integrity_check(args.database)
    responses, outcomes = collect(budget_seconds=args.budget_seconds)
    archive_uri = archive_sha = None
    inserted = 0
    if responses:
        verified, archive_uri, archive_sha = archive_responses(responses, outcomes, args.work, args.remote)
        with closing(sqlite3.connect(args.database)) as conn:
            inserted = ingest_immutable(conn, verified, evidence_uri=archive_uri, evidence_sha256=archive_sha)
            if ingest_immutable(conn, verified, evidence_uri=archive_uri, evidence_sha256=archive_sha) != 0:
                raise ValueError("Collection retry is not idempotent")
        integrity_check(args.database)
    now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        manifest = export_preview(conn, args.directory, now=now)
    result = validate(args.database, args.directory, now=now)
    report = {"status": "passed" if result["passed"] else "failed", "outcomes": outcomes,
              "fresh_accepted_models": len(responses), "inserted_native_rows": inserted,
              "evidence_sha256": archive_sha, "evidence_uri": archive_uri,
              "preview": result, "taf_withheld": True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()
