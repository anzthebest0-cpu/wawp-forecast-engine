import sqlite3
from datetime import datetime, timezone

from src.awos_quality import AWOS_QUALITY_CONTRACT_VERSION, build_awos_quality_state
from src.ingest_awos_inbox import _aggregate_hourly_gust, _ensure_schema


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    _ensure_schema(conn)
    return conn


def test_awos_quality_reconciles_interval_end_gust_and_rain_boundary():
    conn = _connection()
    conn.executemany(
        """
        INSERT INTO awos_observations
            (location, obs_time, temperature, dewpoint, humidity, pressure,
             rain_1h, wind_speed, wind_dir)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("Bandara_Sangia_Ni_Bandera", "2026-07-01 00:00:00", 27, 24, 80, 1009, 0.0, 4, 320),
            ("Bandara_Sangia_Ni_Bandera", "2026-07-01 01:00:00", 27, 24, 80, 1009, 1.2, 5, 315),
        ],
    )
    conn.executemany(
        """
        INSERT INTO awos_observations_1min
            (location, obs_time, wind_gust, rain_1min, humidity)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("Bandara_Sangia_Ni_Bandera", "2026-07-01 00:00:00", 8, 0.0, 80),
            ("Bandara_Sangia_Ni_Bandera", "2026-07-01 00:01:00", 14, 1.2, 80),
            ("Bandara_Sangia_Ni_Bandera", "2026-07-01 01:00:00", 10, 1.2, 80),
        ],
    )
    _aggregate_hourly_gust(conn)

    state = build_awos_quality_state(
        conn,
        now_utc=datetime(2026, 7, 1, 2, tzinfo=timezone.utc),
    )

    assert state["metadata"]["quality_contract_version"] == AWOS_QUALITY_CONTRACT_VERSION
    assert state["cross_grain"]["gust_comparable_hours"] == 2
    assert state["cross_grain"]["gust_match_hours"] == 2
    assert state["cross_grain"]["rain_boundary_comparable_hours"] == 2
    assert state["cross_grain"]["rain_boundary_match_hours"] == 2
    assert "never sum" in state["minute"]["safe_rain_aggregation"]


def test_awos_quality_blocks_negative_hourly_rain():
    conn = _connection()
    conn.execute(
        """
        INSERT INTO awos_observations
            (location, obs_time, rain_1h)
        VALUES ('Bandara_Sangia_Ni_Bandera', '2026-07-01 00:00:00', -1.0)
        """
    )

    state = build_awos_quality_state(
        conn,
        now_utc=datetime(2026, 7, 1, 1, tzinfo=timezone.utc),
    )

    assert state["status"] == "blocked"
    assert state["verification_eligible"] is False
    assert state["hourly"]["negative_rain_count"] == 1


def test_zero_default_gust_is_not_verification_evidence():
    conn = _connection()
    conn.execute(
        """
        INSERT INTO awos_observations
            (location, obs_time, temperature, dewpoint, humidity, pressure,
             rain_1h, wind_speed, wind_dir, wind_gust_max)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Bandara_Sangia_Ni_Bandera", "2026-07-01 00:00:00",
            27, 23, 80, 1010, 0, 4, 100, 0,
        ),
    )

    state = build_awos_quality_state(
        conn,
        now_utc=datetime(2026, 7, 1, 1, tzinfo=timezone.utc),
    )

    assert state["verification_eligible"] is True
    assert state["parameter_eligibility"]["Wind Gust"]["eligible"] is False
    assert state["hourly"]["recent_positive_derived_gust_count"] == 0
