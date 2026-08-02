"""Compact, observe-only AWOS quality state for pipeline and dashboard audit."""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any

import pandas as pd

from src.meteorological_contract import (
    AWOS_GUST_TIMESTAMP_LABEL,
    AWOS_HOURLY_RAIN_TIMESTAMP_LABEL,
    AWOS_MINUTE_RAIN_SEMANTICS,
    AWOS_OPERATIONAL_WIND_SOURCE,
)


AWOS_QUALITY_CONTRACT_VERSION = "awos-cross-grain-quality-v1-shadow"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _hours_inclusive(first: str | None, last: str | None) -> int:
    start = pd.to_datetime(first, errors="coerce")
    end = pd.to_datetime(last, errors="coerce")
    if pd.isna(start) or pd.isna(end) or end < start:
        return 0
    return int((end - start).total_seconds() // 3600) + 1


def _age_hours(timestamp: str | None, now_utc: datetime) -> float | None:
    value = pd.to_datetime(timestamp, errors="coerce", utc=True)
    if pd.isna(value):
        return None
    return max(0.0, (pd.Timestamp(now_utc) - value).total_seconds() / 3600.0)


def build_awos_quality_state(
    conn: sqlite3.Connection,
    *,
    location: str = "Bandara_Sangia_Ni_Bandera",
    minute_lookback_days: int = 45,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    hourly = conn.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT obs_time), MIN(obs_time), MAX(obs_time),
               SUM(CASE WHEN rain_1h < 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN humidity IS NOT NULL AND (humidity < 0 OR humidity > 100) THEN 1 ELSE 0 END),
               SUM(CASE WHEN pressure IS NOT NULL AND (pressure < 800 OR pressure > 1100) THEN 1 ELSE 0 END),
               SUM(CASE WHEN wind_speed IS NOT NULL AND (wind_speed < 0 OR wind_speed > 150) THEN 1 ELSE 0 END),
               SUM(CASE WHEN wind_dir IS NOT NULL AND (wind_dir < 0 OR wind_dir > 360) THEN 1 ELSE 0 END),
               SUM(CASE WHEN temperature IS NOT NULL AND dewpoint IS NOT NULL AND dewpoint > temperature + 2 THEN 1 ELSE 0 END),
               COUNT(wind_gust_max),
               SUM(CASE WHEN wind_gust_max > 0 THEN 1 ELSE 0 END)
        FROM awos_observations
        WHERE location=?
        """,
        (location,),
    ).fetchone()
    row_count = int(hourly[0] or 0)
    distinct_count = int(hourly[1] or 0)
    expected_hours = _hours_inclusive(hourly[2], hourly[3])
    hourly_state = {
        "row_count": row_count,
        "distinct_timestamp_count": distinct_count,
        "duplicate_timestamp_count": max(0, row_count - distinct_count),
        "first_obs_utc": hourly[2],
        "last_obs_utc": hourly[3],
        "expected_hour_count": expected_hours,
        "missing_hour_count": max(0, expected_hours - distinct_count),
        "negative_rain_count": int(hourly[4] or 0),
        "humidity_out_of_range_count": int(hourly[5] or 0),
        "pressure_out_of_range_count": int(hourly[6] or 0),
        "wind_speed_out_of_range_count": int(hourly[7] or 0),
        "wind_direction_out_of_range_count": int(hourly[8] or 0),
        "dewpoint_above_temperature_count": int(hourly[9] or 0),
        "rows_with_derived_gust_value": int(hourly[10] or 0),
        "rows_with_positive_derived_gust": int(hourly[11] or 0),
        "age_hours": None,
        "rain_timestamp_label": AWOS_HOURLY_RAIN_TIMESTAMP_LABEL,
        "operational_wind_source": AWOS_OPERATIONAL_WIND_SOURCE,
    }
    hourly_state["age_hours"] = (
        None if (age := _age_hours(hourly[3], now)) is None else round(age, 2)
    )
    recent_gust_start = (
        (pd.to_datetime(hourly[3]) - pd.Timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        if hourly[3] else None
    )
    hourly_state["recent_gust_lookback_start_utc"] = recent_gust_start
    hourly_state["recent_positive_derived_gust_count"] = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM awos_observations
            WHERE location=? AND obs_time>=? AND wind_gust_max>0
            """,
            (location, recent_gust_start),
        ).fetchone()[0] or 0
    ) if recent_gust_start else 0

    minute_state: dict[str, Any] = {
        "retention": "temporary raw evidence; compact operational DB may contain zero rows",
        "rain_semantics": AWOS_MINUTE_RAIN_SEMANTICS,
        "safe_rain_aggregation": "boundary/max comparison only; never sum minute rain snapshots",
        "row_count": 0,
        "recent_row_count": 0,
        "incomplete_recent_day_count": 0,
    }
    cross_grain = {
        "hourly_rows_with_minute_coverage": 0,
        "gust_comparable_hours": 0,
        "gust_match_hours": 0,
        "gust_mismatch_hours": 0,
        "rain_boundary_comparable_hours": 0,
        "rain_boundary_match_hours": 0,
        "rain_boundary_mismatch_hours": 0,
        "gust_interval": AWOS_GUST_TIMESTAMP_LABEL,
    }

    if _table_exists(conn, "awos_observations_1min"):
        minute = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT obs_time), MIN(obs_time), MAX(obs_time),
                   SUM(CASE WHEN rain_1min < 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN humidity IS NOT NULL AND (humidity < 0 OR humidity > 100) THEN 1 ELSE 0 END),
                   SUM(CASE WHEN wind_gust IS NOT NULL AND (wind_gust < 0 OR wind_gust > 200) THEN 1 ELSE 0 END),
                   COUNT(wind_gust)
            FROM awos_observations_1min WHERE location=?
            """,
            (location,),
        ).fetchone()
        minute_state.update({
            "row_count": int(minute[0] or 0),
            "distinct_timestamp_count": int(minute[1] or 0),
            "duplicate_timestamp_count": max(0, int(minute[0] or 0) - int(minute[1] or 0)),
            "first_obs_utc": minute[2],
            "last_obs_utc": minute[3],
            "negative_rain_count": int(minute[4] or 0),
            "humidity_out_of_range_count": int(minute[5] or 0),
            "gust_out_of_range_count": int(minute[6] or 0),
            "rows_with_gust_value": int(minute[7] or 0),
        })
        if minute[3]:
            lookback_start = (
                pd.to_datetime(minute[3]) - pd.Timedelta(days=max(1, int(minute_lookback_days)))
            ).strftime("%Y-%m-%d %H:%M:%S")
            minute_state["lookback_start_utc"] = lookback_start
            recent = conn.execute(
                """
                SELECT COUNT(*), SUM(CASE WHEN day_rows <> 1440 THEN 1 ELSE 0 END)
                FROM (
                    SELECT date(obs_time) AS day, COUNT(*) AS day_rows
                    FROM awos_observations_1min
                    WHERE location=? AND obs_time>=?
                    GROUP BY date(obs_time)
                )
                """,
                (location, lookback_start),
            ).fetchone()
            minute_state["recent_day_count"] = int(recent[0] or 0)
            minute_state["incomplete_recent_day_count"] = int(recent[1] or 0)
            minute_state["recent_row_count"] = int(conn.execute(
                "SELECT COUNT(*) FROM awos_observations_1min WHERE location=? AND obs_time>=?",
                (location, lookback_start),
            ).fetchone()[0] or 0)

            minute_hour = """
                CASE WHEN strftime('%M', obs_time)='00'
                     THEN strftime('%Y-%m-%d %H:00:00', obs_time)
                     ELSE datetime(strftime('%Y-%m-%d %H:00:00', obs_time), '+1 hour')
                END
            """
            gust = conn.execute(
                f"""
                WITH minute_by_hour AS (
                    SELECT {minute_hour} AS hour_end, COUNT(*) AS minute_count,
                           MAX(wind_gust) AS minute_gust_max
                    FROM awos_observations_1min
                    WHERE location=? AND obs_time>=?
                    GROUP BY hour_end
                )
                SELECT COUNT(*),
                       SUM(CASE WHEN h.wind_gust_max IS NOT NULL AND m.minute_gust_max IS NOT NULL THEN 1 ELSE 0 END),
                       SUM(CASE WHEN h.wind_gust_max IS NOT NULL AND m.minute_gust_max IS NOT NULL
                                     AND ABS(h.wind_gust_max-m.minute_gust_max)<=0.01 THEN 1 ELSE 0 END)
                FROM awos_observations h
                JOIN minute_by_hour m ON m.hour_end=h.obs_time
                WHERE h.location=?
                """,
                (location, lookback_start, location),
            ).fetchone()
            comparable_gust = int(gust[1] or 0)
            matching_gust = int(gust[2] or 0)
            rain = conn.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN ABS(h.rain_1h-m.rain_1min)<=0.05 THEN 1 ELSE 0 END)
                FROM awos_observations h
                JOIN awos_observations_1min m
                  ON m.location=h.location AND m.obs_time=h.obs_time
                WHERE h.location=? AND m.obs_time>=?
                  AND h.rain_1h IS NOT NULL AND m.rain_1min IS NOT NULL
                """,
                (location, lookback_start),
            ).fetchone()
            comparable_rain = int(rain[0] or 0)
            matching_rain = int(rain[1] or 0)
            cross_grain.update({
                "hourly_rows_with_minute_coverage": int(gust[0] or 0),
                "gust_comparable_hours": comparable_gust,
                "gust_match_hours": matching_gust,
                "gust_mismatch_hours": max(0, comparable_gust - matching_gust),
                "rain_boundary_comparable_hours": comparable_rain,
                "rain_boundary_match_hours": matching_rain,
                "rain_boundary_mismatch_hours": max(0, comparable_rain - matching_rain),
            })

    hard_issue_count = sum(
        int(hourly_state[key])
        for key in (
            "negative_rain_count", "humidity_out_of_range_count",
            "pressure_out_of_range_count", "wind_speed_out_of_range_count",
            "wind_direction_out_of_range_count",
        )
    ) + sum(
        int(minute_state.get(key) or 0)
        for key in ("negative_rain_count", "humidity_out_of_range_count", "gust_out_of_range_count")
    )
    warning_count = (
        int(hourly_state["missing_hour_count"] > 0)
        + int(minute_state.get("incomplete_recent_day_count", 0) > 0)
        + int(cross_grain["gust_mismatch_hours"] > 0)
        + int(cross_grain["rain_boundary_mismatch_hours"] > 0)
        + int(hourly_state["recent_positive_derived_gust_count"] < 20)
    )
    status = "blocked" if hard_issue_count else ("warning" if warning_count else "ok")
    fresh = hourly_state["age_hours"] is not None and hourly_state["age_hours"] <= 48
    base_eligible = bool(fresh and status != "blocked")
    gust_eligible = bool(
        base_eligible and hourly_state["recent_positive_derived_gust_count"] >= 20
    )
    return {
        "metadata": {
            "quality_contract_version": AWOS_QUALITY_CONTRACT_VERSION,
            "generated_at_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "observe_only",
            "promotion_eligible": False,
        },
        "status": status,
        "hard_issue_count": hard_issue_count,
        "warning_count": warning_count,
        "verification_eligible": base_eligible,
        "parameter_eligibility": {
            "Temperature": {"eligible": base_eligible, "reason": "hourly AWOS quality"},
            "Dewpoint": {"eligible": base_eligible, "reason": "hourly AWOS quality"},
            "Pressure": {"eligible": base_eligible, "reason": "hourly AWOS quality"},
            "Rainfall": {"eligible": base_eligible, "reason": "hourly RA36 quality"},
            "Wind Speed": {"eligible": base_eligible, "reason": "hourly 10-minute aviation wind quality"},
            "Wind Dir.": {"eligible": base_eligible, "reason": "hourly 10-minute aviation wind quality"},
            "Wind Gust": {
                "eligible": gust_eligible,
                "reason": (
                    "at least 20 positive minute-derived gust hours in the latest 60 days"
                    if gust_eligible else
                    "insufficient populated minute WGS evidence in the latest 60 days"
                ),
            },
        },
        "hourly": hourly_state,
        "minute": minute_state,
        "cross_grain": cross_grain,
    }
