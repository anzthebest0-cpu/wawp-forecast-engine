"""Deterministic as-of selection for operational Open-Meteo collections.

The Forecast API does not expose confirmed provider initialization timestamps.
WAWP therefore selects by the time it actually collected a model response.
Every archived cycle remains available for residual verification; this module
only chooses the cycle eligible for a live or historical issuance snapshot.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Iterable

import pandas as pd

from src.forecast_time_provenance import hard_valid_forecast_sql


SELECTION_CONTRACT_VERSION = "collection-asof-clean-cycle-v1"
ASOF_SELECTION_MODE_ENV = "WAWP_ASOF_SELECTION_MODE"
SELECTION_MODES = {"observe_only", "enabled"}
MIN_SELECTION_CYCLE_ROWS = 24


def asof_selection_mode() -> str:
    mode = os.environ.get(ASOF_SELECTION_MODE_ENV, "observe_only").strip().lower()
    return mode if mode in SELECTION_MODES else "observe_only"


def asof_selection_enabled() -> bool:
    return asof_selection_mode() == "enabled"


def _cycle_stats(
    conn: sqlite3.Connection,
    models: tuple[str, ...],
    as_of_utc: str | None,
) -> pd.DataFrame:
    if not models:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in models)
    hard_valid = hard_valid_forecast_sql("f")
    cutoff_sql = "" if as_of_utc is None else "AND f.scraped_at <= ?"
    params: tuple[Any, ...] = (*models, *((as_of_utc,) if as_of_utc is not None else ()))
    return pd.read_sql_query(
        f"""
        SELECT f.model, f.run_init_utc, f.scraped_at,
               COUNT(*) AS row_count,
               SUM(CASE WHEN {hard_valid} THEN 0 ELSE 1 END) AS hard_anomaly_rows,
               MIN(f.forecast_time) AS first_forecast_time,
               MAX(f.forecast_time) AS last_forecast_time,
               MIN(f.lead_hours) AS min_lead_hours,
               MAX(f.lead_hours) AS max_lead_hours
        FROM openmeteo_forecasts f
        WHERE f.run_init_utc <> 'historical_forecast_api'
          AND f.lead_hours >= 0
          AND f.model IN ({placeholders})
          {cutoff_sql}
        GROUP BY f.model, f.run_init_utc, f.scraped_at
        ORDER BY f.model, f.scraped_at DESC, f.run_init_utc DESC
        """,
        conn,
        params=params,
    )


def select_latest_clean_collections(
    conn: sqlite3.Connection,
    models: Iterable[str],
    *,
    as_of_utc: str | None = None,
    minimum_cycle_rows: int = MIN_SELECTION_CYCLE_ROWS,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return one complete, clean collection cycle per model at a cutoff.

    Ranking is deterministic: newest ``scraped_at`` first, followed by the
    collection-cycle proxy and row id as tie-breakers. A cycle containing even
    one hard-invalid row is excluded as a unit because shifted-field incidents
    make the whole response mapping suspect.
    """
    selected_models = tuple(dict.fromkeys(str(model) for model in models if model))
    cycles = _cycle_stats(conn, selected_models, as_of_utc)
    selected: list[dict[str, Any]] = []
    model_audit: list[dict[str, Any]] = []

    for model in selected_models:
        model_cycles = cycles.loc[cycles["model"] == model].copy() if not cycles.empty else pd.DataFrame()
        clean = model_cycles.loc[
            model_cycles["hard_anomaly_rows"].fillna(0).eq(0)
            & model_cycles["row_count"].fillna(0).ge(minimum_cycle_rows)
        ] if not model_cycles.empty else pd.DataFrame()
        latest = model_cycles.iloc[0] if not model_cycles.empty else None
        chosen = clean.iloc[0] if not clean.empty else None
        if chosen is not None:
            selected.append({
                "model": model,
                "run_init_utc": str(chosen["run_init_utc"]),
                "scraped_at": str(chosen["scraped_at"]),
            })
        changed = bool(
            latest is not None
            and chosen is not None
            and (
                str(latest["scraped_at"]) != str(chosen["scraped_at"])
                or str(latest["run_init_utc"]) != str(chosen["run_init_utc"])
            )
        )
        model_audit.append({
            "model": model,
            "candidate_cycle_count": int(len(model_cycles)),
            "rejected_hard_anomaly_cycle_count": int(
                model_cycles["hard_anomaly_rows"].fillna(0).gt(0).sum()
            ) if not model_cycles.empty else 0,
            "rejected_incomplete_cycle_count": int(
                model_cycles["row_count"].fillna(0).lt(minimum_cycle_rows).sum()
            ) if not model_cycles.empty else 0,
            "latest_available_scraped_at": None if latest is None else str(latest["scraped_at"]),
            "selected_scraped_at": None if chosen is None else str(chosen["scraped_at"]),
            "selected_collection_cycle_utc": None if chosen is None else str(chosen["run_init_utc"]),
            "selected_row_count": 0 if chosen is None else int(chosen["row_count"]),
            "selection_changed_from_latest": changed,
            "status": "selected" if chosen is not None else "no_clean_cycle",
        })

    if not selected:
        rows = pd.DataFrame()
    else:
        predicates = []
        params: list[str] = []
        for item in selected:
            predicates.append("(f.model=? AND f.run_init_utc=? AND f.scraped_at=?)")
            params.extend([item["model"], item["run_init_utc"], item["scraped_at"]])
        rows = pd.read_sql_query(
            f"""
            SELECT f.*
            FROM openmeteo_forecasts f
            WHERE {' OR '.join(predicates)}
            ORDER BY f.model, f.forecast_time, f.id
            """,
            conn,
            params=tuple(params),
        )

    changed_models = [row["model"] for row in model_audit if row["selection_changed_from_latest"]]
    missing_models = [row["model"] for row in model_audit if row["status"] == "no_clean_cycle"]
    audit = {
        "selection_contract_version": SELECTION_CONTRACT_VERSION,
        "mode": asof_selection_mode(),
        "as_of_utc": as_of_utc,
        "provider_initialization_available": False,
        "selection_basis": "latest clean WAWP collection at or before cutoff",
        "minimum_cycle_rows": minimum_cycle_rows,
        "promotion_eligible": False,
        "selected_model_count": len(selected),
        "selected_forecast_row_count": int(len(rows)),
        "selection_changed_model_count": len(changed_models),
        "selection_changed_models": changed_models,
        "models_without_clean_cycle": missing_models,
        "models": model_audit,
    }
    return rows, audit
