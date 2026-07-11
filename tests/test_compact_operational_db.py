import sqlite3

from src.build_compact_operational_db import (
    RAW_MINUTE_TABLE,
    RETENTION_TABLE,
    build_compact_operational_db,
)


def _make_source(path):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE awos_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                obs_time TEXT NOT NULL,
                rain_1h REAL,
                wind_gust_max REAL
            );
            CREATE INDEX idx_awos_time ON awos_observations(obs_time);
            CREATE TABLE awos_observations_1min (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                obs_time TEXT NOT NULL,
                wind_gust REAL
            );
            CREATE INDEX idx_1min_time ON awos_observations_1min(obs_time);
            CREATE TABLE openmeteo_forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                run_init_utc TEXT,
                forecast_time TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO awos_observations(obs_time, rain_1h, wind_gust_max) VALUES (?, ?, ?)",
            [("2026-07-01 00:00:00", 1.2, 18.0), ("2026-07-01 01:00:00", 0.0, 9.0)],
        )
        conn.executemany(
            "INSERT INTO awos_observations_1min(obs_time, wind_gust) VALUES (?, ?)",
            [("2026-07-01 00:01:00", 14.0), ("2026-07-01 00:02:00", 18.0)],
        )
        conn.execute(
            "INSERT INTO openmeteo_forecasts(model, run_init_utc, forecast_time) VALUES (?, ?, ?)",
            ("ECMWF_HRES", "2026-07-01 00:00:00", "2026-07-01 01:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def test_compact_candidate_preserves_operational_rows_and_empties_raw_minutes(tmp_path):
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    _make_source(source)

    report = build_compact_operational_db(source, candidate)

    assert report["valid"] is True
    assert report["source_table_counts"][RAW_MINUTE_TABLE] == 2
    assert report["candidate_table_counts"][RAW_MINUTE_TABLE] == 0
    assert report["source_table_counts"]["awos_observations"] == 2
    assert report["candidate_table_counts"]["awos_observations"] == 2

    conn = sqlite3.connect(source)
    try:
        assert conn.execute(f"SELECT COUNT(*) FROM {RAW_MINUTE_TABLE}").fetchone()[0] == 2
    finally:
        conn.close()
    conn = sqlite3.connect(candidate)
    try:
        assert conn.execute(f"SELECT COUNT(*) FROM {RAW_MINUTE_TABLE}").fetchone()[0] == 0
        assert conn.execute(f"SELECT COUNT(*) FROM {RETENTION_TABLE}").fetchone()[0] == 1
        assert conn.execute("SELECT MAX(wind_gust_max) FROM awos_observations").fetchone()[0] == 18.0
    finally:
        conn.close()
