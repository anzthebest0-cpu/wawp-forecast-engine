"""Canonical valid-time provenance for Open-Meteo forecast records.

``forecast_time`` is retained as the compatibility/display timestamp:

- continuous historical rows store UTC there;
- operational Forecast API rows store WITA there.

All verification and residual pairing must instead use ``valid_time_utc`` or
the fallback SQL expression in this module. The fallback avoids rewriting the
large historical archive while still making old operational rows physically
pairable.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd


TIME_CONTRACT_VERSION = "openmeteo-valid-time-utc-v1"
HISTORICAL_RUN_LABEL = "historical_forecast_api"
WITA_OFFSET_HOURS = 8
TIME_BASIS_HISTORICAL_UTC = "historical_api_utc"
TIME_BASIS_OPERATIONAL_WITA = "forecast_api_wita"


def valid_time_utc_sql(alias: str = "f", *, has_explicit_column: bool = True) -> str:
    """SQLite expression yielding a UTC valid timestamp for old and new rows."""
    fallback = (
        f"CASE WHEN {alias}.run_init_utc = '{HISTORICAL_RUN_LABEL}' "
        f"THEN {alias}.forecast_time ELSE datetime({alias}.forecast_time, '-8 hours') END"
    )
    return f"COALESCE({alias}.valid_time_utc, {fallback})" if has_explicit_column else fallback


def local_valid_time_sql(alias: str = "f") -> str:
    """SQLite expression yielding local WITA valid time for regime labels."""
    return (
        f"CASE WHEN {alias}.run_init_utc = '{HISTORICAL_RUN_LABEL}' "
        f"THEN datetime({alias}.forecast_time, '+8 hours') ELSE {alias}.forecast_time END"
    )


def hard_valid_forecast_sql(alias: str = "f") -> str:
    """Reject known shifted-field and impossible-physical-value signatures."""
    valid_codes = "0,1,2,3,45,48,51,53,55,56,57,61,63,65,66,67,71,73,75,77,80,81,82,85,86,95,96,99"
    cloud_checks = " AND ".join(
        f"({alias}.{column} IS NULL OR {alias}.{column} BETWEEN 0 AND 100)"
        for column in ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high")
    )
    return (
        f"({alias}.rain IS NULL OR {alias}.rain >= 0) AND "
        f"({alias}.precipitation IS NULL OR {alias}.precipitation >= 0) AND "
        f"({alias}.showers IS NULL OR {alias}.showers >= 0) AND "
        f"({alias}.snowfall IS NULL OR {alias}.snowfall >= 0) AND "
        f"{cloud_checks} AND "
        f"({alias}.visibility IS NULL OR {alias}.visibility >= 0) AND "
        f"({alias}.weather_code IS NULL OR {alias}.weather_code IN ({valid_codes})) AND "
        f"({alias}.lifted_index IS NULL OR ABS({alias}.lifted_index) <= 30)"
    )


def clean_operational_cycle_sql(alias: str = "f", candidate_alias: str = "q") -> str:
    """Require every row from an operational collection cycle to be hard-valid."""
    candidate_valid = hard_valid_forecast_sql(candidate_alias)
    return (
        "NOT EXISTS ("
        f"SELECT 1 FROM openmeteo_forecasts {candidate_alias} "
        f"WHERE {candidate_alias}.location={alias}.location "
        f"AND {candidate_alias}.model={alias}.model "
        f"AND {candidate_alias}.run_init_utc={alias}.run_init_utc "
        f"AND {candidate_alias}.scraped_at={alias}.scraped_at "
        f"AND NOT ({candidate_valid})"
        ")"
    )
def normalize_row_time_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy carrying canonical UTC valid-time provenance."""
    normalized = dict(row)
    if normalized.get("valid_time_utc") and normalized.get("forecast_time_basis"):
        return normalized
    forecast_time = pd.to_datetime(normalized.get("forecast_time"), errors="coerce")
    if pd.isna(forecast_time):
        normalized.setdefault("valid_time_utc", None)
        normalized.setdefault("forecast_time_basis", "unknown")
        return normalized
    if normalized.get("run_init_utc") == HISTORICAL_RUN_LABEL:
        valid_utc = forecast_time
        basis = TIME_BASIS_HISTORICAL_UTC
    else:
        valid_utc = forecast_time - pd.Timedelta(hours=WITA_OFFSET_HOURS)
        basis = TIME_BASIS_OPERATIONAL_WITA
    normalized["valid_time_utc"] = valid_utc.strftime("%Y-%m-%d %H:%M:%S")
    normalized["forecast_time_basis"] = basis
    return normalized


def ensure_time_provenance_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Add provenance columns and backfill only the smaller operational archive."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(openmeteo_forecasts)")}
    added: list[str] = []
    if "valid_time_utc" not in columns:
        conn.execute("ALTER TABLE openmeteo_forecasts ADD COLUMN valid_time_utc TEXT")
        added.append("valid_time_utc")
    if "forecast_time_basis" not in columns:
        conn.execute("ALTER TABLE openmeteo_forecasts ADD COLUMN forecast_time_basis TEXT")
        added.append("forecast_time_basis")

    cursor = conn.execute(
        """
        UPDATE openmeteo_forecasts
        SET valid_time_utc = datetime(forecast_time, '-8 hours'),
            forecast_time_basis = ?
        WHERE run_init_utc <> ?
          AND (valid_time_utc IS NULL OR forecast_time_basis IS NULL)
        """,
        (TIME_BASIS_OPERATIONAL_WITA, HISTORICAL_RUN_LABEL),
    )
    existing_index = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_om_valid_utc'"
    ).fetchone()
    partial_clause = "where valid_time_utc is not null"
    if existing_index and partial_clause not in str(existing_index[0] or "").lower():
        conn.execute("DROP INDEX idx_om_valid_utc")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_om_valid_utc "
        "ON openmeteo_forecasts(valid_time_utc) "
        "WHERE valid_time_utc IS NOT NULL"
    )
    conn.commit()
    explicit_operational = conn.execute(
        """
        SELECT COUNT(*) FROM openmeteo_forecasts
        WHERE run_init_utc <> ? AND valid_time_utc IS NOT NULL
        """,
        (HISTORICAL_RUN_LABEL,),
    ).fetchone()[0]
    historical_fallback = conn.execute(
        """
        SELECT COUNT(*) FROM openmeteo_forecasts
        WHERE run_init_utc = ? AND valid_time_utc IS NULL
        """,
        (HISTORICAL_RUN_LABEL,),
    ).fetchone()[0]
    return {
        "contract_version": TIME_CONTRACT_VERSION,
        "columns_added": added,
        "operational_rows_backfilled": max(0, int(cursor.rowcount or 0)),
        "operational_rows_with_explicit_utc": int(explicit_operational or 0),
        "historical_rows_using_utc_fallback": int(historical_fallback or 0),
        "historical_rewrite_policy": "not rewritten; historical forecast_time is already UTC",
        "index_policy": "partial index over explicit operational UTC valid times only",
    }
