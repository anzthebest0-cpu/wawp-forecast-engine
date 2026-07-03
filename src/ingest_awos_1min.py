"""
Ingest 1-minute AWOS files into awos_observations_1min.

Usage:
    python src/ingest_awos_1min.py --directory data/raw_obs/oneminute
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sqlite3

import pandas as pd

LOCATION_NAME = "Bandara_Sangia_Ni_Bandera"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("awos_1min")


def _root_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_1min_time ON awos_observations_1min(obs_time);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_1min_date ON awos_observations_1min(date(obs_time));")
    try:
        conn.execute("ALTER TABLE awos_observations ADD COLUMN wind_gust_max REAL;")
    except sqlite3.OperationalError:
        pass


def _clean_float(value):
    if pd.isna(value):
        return None
    return float(value)


def parse_1min_file(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(
        file_path,
        sep=r"\s+",
        skiprows=4,
        header=None,
        usecols=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14],
        names=["Date", "Hour", "Minute", "WS", "WD", "WGS", "WGD", "Temp",
               "Dewp", "RH", "QNH", "Rain", "SOL"],
        na_values=["///"],
        encoding="utf-8",
    )
    for col in ["WS", "WD", "WGS", "WGD", "RH", "SOL"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["Temp", "Dewp", "QNH", "Rain"]:
        df[col] = pd.to_numeric(df[col], errors="coerce") / 10.0

    df["UTC"] = pd.to_datetime(
        df["Date"].astype(str)
        + df["Hour"].astype(str).str.zfill(2)
        + df["Minute"].astype(str).str.zfill(2),
        format="%Y%m%d%H%M",
        errors="coerce",
    )
    return df.dropna(subset=["UTC"])


def ingest_1min_file(file_path: str, db_path: str) -> int:
    if not os.path.exists(file_path):
        return 0
    try:
        df = parse_1min_file(file_path)
    except Exception as e:
        log.error(f"Failed to parse {file_path}: {e}")
        return 0
    if df.empty:
        return 0

    rows = []
    for row in df.itertuples(index=False):
        rows.append((
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
        ))

    try:
        with sqlite3.connect(db_path) as conn:
            _ensure_schema(conn)
            before = conn.total_changes
            conn.executemany("""
                INSERT OR IGNORE INTO awos_observations_1min
                (location, obs_time, wind_speed, wind_dir, wind_gust, wind_gust_dir,
                 temperature, dewpoint, humidity, pressure_qnh, rain_1min, solar_rad)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.commit()
            return conn.total_changes - before
    except Exception as e:
        log.error(f"DB operation failed for {file_path}: {e}")
        return 0


def aggregate_1min_to_hourly_gust(db_path: str) -> int:
    try:
        with sqlite3.connect(db_path) as conn:
            _ensure_schema(conn)
            before = conn.total_changes
            conn.execute("""
                UPDATE awos_observations
                SET wind_gust_max = (
                    SELECT MAX(m.wind_gust)
                    FROM awos_observations_1min m
                    WHERE m.location = awos_observations.location
                      AND m.obs_time >= awos_observations.obs_time
                      AND m.obs_time < datetime(awos_observations.obs_time, '+1 hour')
                      AND m.wind_gust IS NOT NULL
                )
                WHERE EXISTS (
                    SELECT 1
                    FROM awos_observations_1min m
                    WHERE m.location = awos_observations.location
                      AND m.obs_time >= awos_observations.obs_time
                      AND m.obs_time < datetime(awos_observations.obs_time, '+1 hour')
                      AND m.wind_gust IS NOT NULL
                )
            """)
            conn.commit()
            return conn.total_changes - before
    except Exception as e:
        log.error(f"Aggregation failed: {e}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    root = _root_dir()
    db_path = args.db or os.path.join(root, "wawp_forecasts.db")
    awos_dir = args.directory or os.path.join(root, "data", "raw_obs", "oneminute")
    if not os.path.exists(awos_dir):
        log.warning(f"1-min AWOS directory not found: {awos_dir}")
        return

    files = sorted(glob.glob(os.path.join(awos_dir, "**", "000OneMinute.*.dat"), recursive=True))
    log.info(f"Found {len(files)} 1-minute AWOS files")
    total = 0
    for i, file_path in enumerate(files, 1):
        total += ingest_1min_file(file_path, db_path)
        if i % 50 == 0:
            log.info(f"Progress: {i}/{len(files)} files, {total} new rows")

    log.info(f"Total 1-min rows ingested: {total}")
    updated = aggregate_1min_to_hourly_gust(db_path)
    log.info(f"Updated hourly wind_gust_max rows: {updated}")


if __name__ == "__main__":
    main()
