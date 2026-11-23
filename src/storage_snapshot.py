"""Pack and verify complete, closed SQLite checkpoints without losing bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require_closed(path: Path) -> None:
    if not path.is_file() or not path.stat().st_size:
        raise ValueError(f"Missing or empty SQLite database: {path}")
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError(f"Checkpoint the database and close its writer first: {wal}")


def integrity_check(path: Path) -> None:
    require_closed(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError(f"SQLite integrity check failed: {path}")


def decoded_digest(archive: Path, destination=None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with subprocess.Popen(["zstd", "-q", "-dc", str(archive)], stdout=subprocess.PIPE) as process:
        assert process.stdout is not None
        for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
        if process.wait():
            raise ValueError(f"Invalid Zstandard stream: {archive}")
    return size, digest.hexdigest()


def manifest_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".json")


def select_asset(pages: list[list[dict]]) -> str:
    """Choose a completed snapshot; raw legacy assets remain restorable."""
    assets = {
        asset["name"]: asset
        for page in pages
        for asset in page
        if asset["state"] == "uploaded" and asset["size"] > 0
    }
    recoverable = []
    for name, asset in assets.items():
        if re.fullmatch(r"wawp_forecasts(?:-\d+-\d+)?\.db", name):
            recoverable.append(asset)
        elif re.fullmatch(r"wawp_forecasts(?:-\d+-\d+)?\.db\.zst", name) and name + ".json" in assets:
            recoverable.append(asset)
    if not recoverable:
        return ""
    return max(recoverable, key=lambda item: (item["created_at"], item["id"]))["name"]


def verify_asset(pages: list[list[dict]], asset: Path) -> None:
    metadata = next(
        item for page in pages for item in page
        if item["name"] == asset.name and item["state"] == "uploaded"
    )
    if metadata["size"] != asset.stat().st_size:
        raise ValueError(f"Release asset size mismatch: {asset}")
    digest = metadata.get("digest")
    if digest is not None and digest != "sha256:" + file_digest(asset):
        raise ValueError(f"Release asset digest mismatch: {asset}")


def pack(source: Path, archive: Path) -> dict:
    """Verify a closed database and write a compressed copy plus its manifest."""
    if archive.exists() or manifest_path(archive).exists():
        raise FileExistsError(archive)
    integrity_check(source)
    source_size = source.stat().st_size
    source_hash = file_digest(source)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        staged = Path(temporary) / "snapshot.zst"
        subprocess.run(["zstd", "-q", "-3", "-T2", "-o", str(staged), str(source)], check=True)
        if decoded_digest(staged) != (source_size, source_hash):
            raise ValueError("Decompressed database differs from the original")
        if file_digest(source) != source_hash:
            raise ValueError("Source changed during compression")
        require_closed(source)
        report = {
            "schema_version": 1,
            "database_bytes": source_size,
            "database_sha256": source_hash,
            "archive_bytes": staged.stat().st_size,
            "archive_sha256": file_digest(staged),
        }
        staged_manifest = Path(temporary) / "manifest.json"
        staged_manifest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        os.replace(staged, archive)
        os.replace(staged_manifest, manifest_path(archive))
    return report


def restore(archive: Path, destination: Path) -> dict:
    """Restore only a complete, independently validated snapshot."""
    if destination.exists():
        raise FileExistsError(destination)
    report = json.loads(manifest_path(archive).read_text(encoding="utf-8"))
    if report["schema_version"] != 1:
        raise ValueError("Unsupported snapshot manifest")
    if (archive.stat().st_size, file_digest(archive)) != (
        report["archive_bytes"], report["archive_sha256"]
    ):
        raise ValueError("Compressed checkpoint does not match its manifest")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "restored.db"
        with staged.open("wb") as stream:
            actual = decoded_digest(archive, stream)
        if actual != (report["database_bytes"], report["database_sha256"]):
            raise ValueError("Restored checkpoint does not match its manifest")
        integrity_check(staged)
        os.replace(staged, destination)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack_parser = commands.add_parser("pack")
    pack_parser.add_argument("database", type=Path)
    pack_parser.add_argument("archive", type=Path)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("archive", type=Path)
    restore_parser.add_argument("database", type=Path)
    select_parser = commands.add_parser("select")
    select_parser.add_argument("assets", type=Path)
    verify_parser = commands.add_parser("verify-asset")
    verify_parser.add_argument("assets", type=Path)
    verify_parser.add_argument("asset", type=Path)
    args = parser.parse_args()
    if args.command == "select":
        print(select_asset(json.loads(args.assets.read_text(encoding="utf-8"))))
        return
    if args.command == "verify-asset":
        verify_asset(json.loads(args.assets.read_text(encoding="utf-8")), args.asset)
        return
    report = pack(args.database, args.archive) if args.command == "pack" else restore(args.archive, args.database)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
