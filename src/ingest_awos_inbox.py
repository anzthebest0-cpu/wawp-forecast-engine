"""Ingest checksum-tracked AWOS release assets into the operational database.

Hourly observations are retained in the rolling database. One-minute rows are
loaded only long enough to derive hourly maximum gusts; the operational
compactor removes the raw minute rows after this script succeeds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.awos_hourly_parser import read_hourly_awos
from src.awos_quality import build_awos_quality_state
from src.ingest_awos_1min import LOCATION_NAME, parse_1min_file


log = logging.getLogger("awos_inbox")
MANIFEST_TABLE = "awos_source_ingest_manifest"
HOURLY_PATTERN = "000HLY*.dat"
MINUTE_PATTERN = "000OneMinute*.dat"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS awos_observations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            location        TEXT    NOT NULL,
            obs_time        TEXT    NOT NULL,
            temperature     REAL,
            dewpoint        REAL,
            humidity        REAL,
            pressure        REAL,
            rain_1h         REAL DEFAULT 0.0,
            wind_speed      REAL DEFAULT 0.0,
            wind_gust_max   REAL,
            wind_dir        REAL,
            visibility      REAL,
            UNIQUE(location, obs_time)
        );
        CREATE INDEX IF NOT EXISTS idx_awos_time
            ON awos_observations(obs_time);
        CREATE INDEX IF NOT EXISTS idx_awos_location_time
            ON awos_observations(location, obs_time);

        CREATE TABLE IF NOT EXISTS awos_observations_1min (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            location        TEXT    NOT NULL,
            obs_time        TEXT    NOT NULL,
            wind_speed      REAL,
            wind_dir        REAL,
            wind_gust       REAL,
            wind_gust_dir   REAL,
            temperature     REAL,
            dewpoint        REAL,
            humidity        REAL,
            pressure_qnh    REAL,
            rain_1min       REAL,
            solar_rad       REAL,
            UNIQUE(location, obs_time)
        );
        CREATE INDEX IF NOT EXISTS idx_1min_time
            ON awos_observations_1min(obs_time);
        CREATE INDEX IF NOT EXISTS idx_1min_date
            ON awos_observations_1min(date(obs_time));

        CREATE TABLE IF NOT EXISTS {MANIFEST_TABLE} (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_tag        TEXT NOT NULL,
            asset_name        TEXT NOT NULL,
            sha256            TEXT NOT NULL,
            asset_type        TEXT NOT NULL,
            file_size_bytes   INTEGER NOT NULL,
            parsed_rows       INTEGER NOT NULL,
            applied_rows      INTEGER NOT NULL,
            first_obs_time    TEXT,
            last_obs_time     TEXT,
            processed_at_utc  TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'applied',
            UNIQUE(source_tag, asset_name, sha256)
        );
        CREATE INDEX IF NOT EXISTS idx_awos_manifest_asset
            ON {MANIFEST_TABLE}(source_tag, asset_name, processed_at_utc);
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(awos_observations)")}
    if "wind_gust_max" not in columns:
        conn.execute("ALTER TABLE awos_observations ADD COLUMN wind_gust_max REAL")


def _validate_asset_period(path: Path, frame: pd.DataFrame, asset_type: str) -> None:
    if frame.empty:
        raise ValueError(f"{path.name} contains no valid observation timestamps")
    if frame["UTC"].duplicated().any():
        raise ValueError(f"{path.name} contains duplicate observation timestamps")

    if asset_type == "minute":
        match = re.fullmatch(r"000OneMinute\.(\d{8})\.dat", path.name)
        if not match:
            raise ValueError(f"Unsupported one-minute filename: {path.name}")
        expected = match.group(1)
        actual = set(frame["UTC"].dt.strftime("%Y%m%d"))
    else:
        match = re.fullmatch(r"000HLY\.(\d{6})\.dat", path.name)
        if not match:
            raise ValueError(f"Unsupported hourly filename: {path.name}")
        expected = match.group(1)
        actual = set(frame["UTC"].dt.strftime("%Y%m"))

    if actual != {expected}:
        raise ValueError(
            f"{path.name} timestamp period mismatch: expected {expected}, found {sorted(actual)}"
        )


def _load_assets(directory: Path) -> tuple[list[dict[str, Any]], list[str]]:
    all_dat = sorted(directory.glob("*.dat"))
    hourly_paths = sorted(directory.glob(HOURLY_PATTERN))
    minute_paths = sorted(directory.glob(MINUTE_PATTERN))
    recognized = set(hourly_paths + minute_paths)
    unknown = [path.name for path in all_dat if path not in recognized]
    if unknown:
        raise ValueError(f"Unsupported AWOS assets: {', '.join(unknown)}")
    if not recognized:
        raise FileNotFoundError(f"No supported AWOS .dat assets found in {directory}")

    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for asset_type, paths in (("hourly", hourly_paths), ("minute", minute_paths)):
        for path in paths:
            frame = read_hourly_awos(str(path)) if asset_type == "hourly" else parse_1min_file(str(path))
            _validate_asset_period(path, frame, asset_type)
            for timestamp in frame["UTC"]:
                key = (asset_type, timestamp.strftime("%Y-%m-%d %H:%M:%S"))
                if key in seen:
                    raise ValueError(
                        f"Overlapping {asset_type} timestamp {key[1]} in {seen[key]} and {path.name}"
                    )
                seen[key] = path.name
            if asset_type == "minute" and len(frame) != 1440:
                warnings.append(f"{path.name}: expected 1440 minute rows, parsed {len(frame)}")
            assets.append(
                {
                    "path": path,
                    "type": asset_type,
                    "sha256": _sha256(path),
                    "frame": frame,
                }
            )
    return assets, warnings


def _already_applied(conn: sqlite3.Connection, source_tag: str, asset: dict[str, Any]) -> bool:
    return conn.execute(
        f"""
        SELECT 1 FROM {MANIFEST_TABLE}
        WHERE source_tag = ? AND asset_name = ? AND sha256 = ? AND status = 'applied'
        """,
        (source_tag, asset["path"].name, asset["sha256"]),
    ).fetchone() is not None


def _ingest_hourly(conn: sqlite3.Connection, frame: pd.DataFrame) -> int:
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            (
                LOCATION_NAME,
                row.UTC.strftime("%Y-%m-%d %H:%M:%S"),
                _clean_float(row.Temp),
                _clean_float(row.Dewp),
                _clean_float(row.RH),
                _clean_float(row.QFF),
                _clean_float(row.Rain),
                _clean_float(row.WS),
                _clean_float(row.WD),
                None,
            )
        )
    conn.executemany(
        """
        INSERT INTO awos_observations
            (location, obs_time, temperature, dewpoint, humidity, pressure,
             rain_1h, wind_speed, wind_dir, wind_gust_max)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(location, obs_time) DO UPDATE SET
            temperature=excluded.temperature,
            dewpoint=excluded.dewpoint,
            humidity=excluded.humidity,
            pressure=excluded.pressure,
            rain_1h=excluded.rain_1h,
            wind_speed=excluded.wind_speed,
            wind_dir=excluded.wind_dir
        """,
        rows,
    )
    return len(rows)


def _ingest_minute(conn: sqlite3.Connection, frame: pd.DataFrame) -> int:
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            (
                LOCATION_NAME,
                row.UTC.strftime("%Y-%m-%d %H:%M:00"),
                _clean_float(row.WS),
                _clean_float(row.WD),
                _clean_float(row.WGS),
                _clean_float(row.WGD),
                _clean_float(row.Temp),
                _clean_float(row.Dewp),
                _clean_float(row.RH),
                _clean_float(row.QNH),
                _clean_float(row.Rain),
                _clean_float(row.SOL),
            )
        )
    conn.executemany(
        """
        INSERT INTO awos_observations_1min
            (location, obs_time, wind_speed, wind_dir, wind_gust, wind_gust_dir,
             temperature, dewpoint, humidity, pressure_qnh, rain_1min, solar_rad)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(location, obs_time) DO UPDATE SET
            wind_speed=excluded.wind_speed,
            wind_dir=excluded.wind_dir,
            wind_gust=excluded.wind_gust,
            wind_gust_dir=excluded.wind_gust_dir,
            temperature=excluded.temperature,
            dewpoint=excluded.dewpoint,
            humidity=excluded.humidity,
            pressure_qnh=excluded.pressure_qnh,
            rain_1min=excluded.rain_1min,
            solar_rad=excluded.solar_rad
        """,
        rows,
    )
    return len(rows)


def _aggregate_hourly_gust(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        """
        UPDATE awos_observations
        SET wind_gust_max = (
            SELECT MAX(m.wind_gust)
            FROM awos_observations_1min m
            WHERE m.location = awos_observations.location
              AND m.obs_time > datetime(awos_observations.obs_time, '-1 hour')
              AND m.obs_time <= awos_observations.obs_time
              AND m.wind_gust IS NOT NULL
        )
        WHERE EXISTS (
            SELECT 1
            FROM awos_observations_1min m
            WHERE m.location = awos_observations.location
              AND m.obs_time > datetime(awos_observations.obs_time, '-1 hour')
              AND m.obs_time <= awos_observations.obs_time
              AND m.wind_gust IS NOT NULL
        )
        """
    )
    return max(cursor.rowcount, 0)


def _null_hourly_gust_without_minute_source(
    conn: sqlite3.Connection,
    minute_assets: list[dict[str, Any]],
) -> int:
    """Keep unavailable WGS as NULL instead of treating it as a calm gust."""
    timestamps = pd.concat(
        [asset["frame"][["UTC"]] for asset in minute_assets], ignore_index=True
    )["UTC"]
    if timestamps.empty:
        return 0
    first_hour = timestamps.min().floor("h").strftime("%Y-%m-%d %H:%M:%S")
    last_hour = timestamps.max().ceil("h").strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """
        UPDATE awos_observations
        SET wind_gust_max = NULL
        WHERE location = ?
          AND obs_time BETWEEN ? AND ?
          AND NOT EXISTS (
              SELECT 1
              FROM awos_observations_1min m
              WHERE m.location = awos_observations.location
                AND m.obs_time > datetime(awos_observations.obs_time, '-1 hour')
                AND m.obs_time <= awos_observations.obs_time
                AND m.wind_gust IS NOT NULL
          )
        """,
        (LOCATION_NAME, first_hour, last_hour),
    )
    return max(cursor.rowcount, 0)


def _record_manifest(
    conn: sqlite3.Connection,
    source_tag: str,
    asset: dict[str, Any],
    applied_rows: int,
) -> None:
    frame = asset["frame"]
    conn.execute(
        f"""
        INSERT INTO {MANIFEST_TABLE}
            (source_tag, asset_name, sha256, asset_type, file_size_bytes,
             parsed_rows, applied_rows, first_obs_time, last_obs_time,
             processed_at_utc, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied')
        """,
        (
            source_tag,
            asset["path"].name,
            asset["sha256"],
            asset["type"],
            asset["path"].stat().st_size,
            len(frame),
            applied_rows,
            frame["UTC"].min().strftime("%Y-%m-%d %H:%M:%S"),
            frame["UTC"].max().strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )


def ingest_awos_inbox(directory: Path, db_path: Path, source_tag: str = "awos-inbox") -> dict[str, Any]:
    directory = directory.resolve()
    db_path = db_path.resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"AWOS inbox directory not found: {directory}")

    assets, warnings = _load_assets(directory)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        _ensure_schema(conn)
        pending = [asset for asset in assets if not _already_applied(conn, source_tag, asset)]
        skipped = len(assets) - len(pending)
        applied: list[dict[str, Any]] = []
        gust_rows = 0
        gust_missing_rows_cleared = 0
        try:
            conn.execute("BEGIN IMMEDIATE")
            for asset in [item for item in pending if item["type"] == "hourly"]:
                count = _ingest_hourly(conn, asset["frame"])
                _record_manifest(conn, source_tag, asset, count)
                applied.append({"asset": asset["path"].name, "type": "hourly", "rows": count})
            minute_applied: list[tuple[dict[str, Any], int]] = []
            for asset in [item for item in pending if item["type"] == "minute"]:
                count = _ingest_minute(conn, asset["frame"])
                minute_applied.append((asset, count))
                applied.append({"asset": asset["path"].name, "type": "minute", "rows": count})
            if minute_applied:
                gust_rows = _aggregate_hourly_gust(conn)
                gust_missing_rows_cleared = _null_hourly_gust_without_minute_source(
                    conn, [asset for asset, _ in minute_applied]
                )
                for asset, count in minute_applied:
                    _record_manifest(conn, source_tag, asset, count)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        hourly_summary = conn.execute(
            """
            SELECT COUNT(*), MIN(obs_time), MAX(obs_time),
                   SUM(CASE WHEN wind_gust_max > 0 THEN 1 ELSE 0 END)
            FROM awos_observations
            """
        ).fetchone()
        minute_rows, minute_gust_values = conn.execute(
            "SELECT COUNT(*), COUNT(wind_gust) FROM awos_observations_1min"
        ).fetchone()
        if any(item["type"] == "minute" for item in pending) and minute_gust_values == 0:
            warnings.append(
                "One-minute assets contain no populated WGS values; no hourly gust maxima were derived."
            )
        manifest_rows = conn.execute(
            f"SELECT COUNT(*) FROM {MANIFEST_TABLE} WHERE source_tag = ? AND status = 'applied'",
            (source_tag,),
        ).fetchone()[0]
        quality_state = build_awos_quality_state(conn, location=LOCATION_NAME)

    return {
        "source_tag": source_tag,
        "directory": str(directory),
        "database": str(db_path),
        "discovered_assets": len(assets),
        "applied_assets": len(applied),
        "skipped_assets": skipped,
        "applied": applied,
        "warnings": warnings,
        "hourly_gust_rows_updated": gust_rows,
        "hourly_gust_rows_cleared_missing_source": gust_missing_rows_cleared,
        "hourly_database_rows": hourly_summary[0],
        "hourly_first_obs": hourly_summary[1],
        "hourly_last_obs": hourly_summary[2],
        "hourly_rows_with_gust": hourly_summary[3],
        "raw_minute_rows_before_compaction": minute_rows,
        "minute_wind_gust_values": minute_gust_values,
        "manifest_rows": manifest_rows,
        "quality_state": quality_state,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=root / "wawp_forecasts.db")
    parser.add_argument("--source-tag", default="awos-inbox")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    report = ingest_awos_inbox(args.directory, args.db, args.source_tag)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info(
        "AWOS inbox: discovered=%s applied=%s skipped=%s minute_rows=%s gust_hours=%s",
        report["discovered_assets"],
        report["applied_assets"],
        report["skipped_assets"],
        report["raw_minute_rows_before_compaction"],
        report["hourly_gust_rows_updated"],
    )
    for warning in report["warnings"]:
        log.warning(warning)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
