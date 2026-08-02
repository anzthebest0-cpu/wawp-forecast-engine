"""Read-only comparison of forecast selection and QM correction candidates.

The report is diagnostic only. It never changes enabled CDFs, model weights,
forecast selection mode, dashboard products, or TAF guidance.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.forecast_selection import select_latest_clean_collections
from src.forecast_time_provenance import (
    TIME_CONTRACT_VERSION,
    clean_operational_cycle_sql,
    hard_valid_forecast_sql,
    valid_time_utc_sql,
)
from src.model_registry import MODEL_REGISTRY
from src.operational_residuals import PARAMETER_COLUMNS, lead_bucket
from src.quantile_mapper import apply_qm_value


SHADOW_VERSION = "selection-qm-shadow-v1"
PARAMETER_KEYS = {
    "Temperature": "temperature",
    "Dewpoint": "dewpoint",
    "Pressure": "pressure",
    "Rainfall": "rain",
    "Wind Speed": "wind_speed",
    "Wind Dir.": "wind_dir",
    "Wind Gust": "wind_gust",
}
SNAPSHOT_COLUMNS = {
    "Temperature": "temperature",
    "Dewpoint": "dewpoint",
    "Pressure": "pressure_msl",
    "Rainfall": "rain",
    "Wind Speed": "wind_speed",
    "Wind Dir.": "wind_dir",
    "Wind Gust": "wind_gust",
}


def _safe(value: Any, digits: int = 4):
    if value is None or pd.isna(value) or np.isinf(value):
        return None
    return round(float(value), digits)


def _binary_scores(obs_event: pd.Series, fc_event: pd.Series) -> dict[str, Any]:
    obs = obs_event.astype(bool)
    fc = fc_event.astype(bool)
    hits = int((obs & fc).sum())
    misses = int((obs & ~fc).sum())
    false_alarms = int((~obs & fc).sum())
    correct_negatives = int((~obs & ~fc).sum())
    total = hits + misses + false_alarms + correct_negatives
    pod = hits / (hits + misses) if hits + misses else None
    far = false_alarms / (hits + false_alarms) if hits + false_alarms else None
    csi = hits / (hits + misses + false_alarms) if hits + misses + false_alarms else None
    expected = (
        ((hits + misses) * (hits + false_alarms))
        + ((correct_negatives + misses) * (correct_negatives + false_alarms))
    ) / total if total else 0.0
    hss = (hits + correct_negatives - expected) / (total - expected) if total > expected else None
    return {
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_negatives": correct_negatives,
        "POD": _safe(pod),
        "FAR": _safe(far),
        "CSI": _safe(csi),
        "HSS": _safe(hss),
    }


def _load_enabled_qm(conn: sqlite3.Connection) -> tuple[dict[tuple[str, str, str, str], dict], dict[str, int]]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(qm_cdfs)")}
    if not columns:
        return {}, {"historical_prior": 0, "operational_residual": 0, "rejected_stale_operational": 0}
    deprecated = "AND COALESCE(deprecated,0)=0" if "deprecated" in columns else ""
    rows = conn.execute(f"""
        SELECT id, model, parameter, lead_bucket, fcst_quantiles, obs_quantiles,
               method, metadata, source_type, correction_layer, low_confidence,
               skill_score, trained_at
        FROM qm_cdfs
        WHERE enabled=1 {deprecated}
        ORDER BY trained_at DESC, id DESC
    """).fetchall()
    state: dict[tuple[str, str, str, str], dict] = {}
    counts = {"historical_prior": 0, "operational_residual": 0, "rejected_stale_operational": 0}
    for row in rows:
        metadata = json.loads(row[7] or "{}")
        layer = row[9] or "unknown"
        if layer == "operational_residual" and metadata.get("pairing_contract_version") != TIME_CONTRACT_VERSION:
            counts["rejected_stale_operational"] += 1
            continue
        key = (str(row[1]), str(row[2]), str(layer), str(row[3]))
        if key in state:
            continue
        metadata.update({
            "id": row[0],
            "fcst_quantiles": json.loads(row[4]),
            "obs_quantiles": json.loads(row[5]),
            "method": row[6],
            "source_type": row[8],
            "correction_layer": layer,
            "low_confidence": bool(row[10]),
            "skill_score": row[11],
        })
        state[key] = metadata
        if layer in counts:
            counts[layer] += 1
    return state, counts


def _correct(value: float, model: str, parameter: str, lead: float, state: dict) -> tuple[float, float, str]:
    internal = PARAMETER_KEYS[parameter]
    current = float(value)
    layer = "raw"
    hist = state.get((model, internal, "historical_prior", "GLOBAL"))
    if hist:
        current = float(apply_qm_value(current, internal, hist))
        layer = "historical_prior"
    prior = current
    bucket = lead_bucket(lead)
    residual = state.get((model, internal, "operational_residual", str(bucket)))
    if residual:
        current = float(apply_qm_value(current, internal, residual))
        layer = "operational_residual"
    return prior, current, layer


def _score(parameter: str, frame: pd.DataFrame, column: str) -> dict[str, Any]:
    pair = frame[[column, "obs"]].apply(pd.to_numeric, errors="coerce").dropna()
    if pair.empty:
        return {"sample_count": 0}
    fc = pair[column].astype(float)
    obs = pair["obs"].astype(float)
    if parameter == "Wind Dir.":
        error = ((fc - obs + 180.0) % 360.0) - 180.0
    else:
        error = fc - obs
    result = {
        "sample_count": int(len(pair)),
        "mae": _safe(error.abs().mean()),
        "bias": _safe(error.mean()),
    }
    if parameter == "Rainfall":
        result["occurrence_0.1mm"] = _binary_scores(obs >= 0.1, fc >= 0.1)
        wet_wet = (obs >= 0.1) & (fc >= 0.1)
        result["wet_wet_count"] = int(wet_wet.sum())
        result["wet_wet_amount_mae"] = _safe((fc[wet_wet] - obs[wet_wet]).abs().mean()) if wet_wet.any() else None
    elif parameter == "Wind Gust":
        result["gust_event_15kt"] = _binary_scores(obs >= 15.0, fc >= 15.0)
    return result


def _query_all_pairs(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    models: list[str],
) -> pd.DataFrame:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(openmeteo_forecasts)")}
    valid = valid_time_utc_sql("f", has_explicit_column="valid_time_utc" in columns)
    hard_valid = hard_valid_forecast_sql("f")
    clean_cycle = clean_operational_cycle_sql("f")
    placeholders = ",".join("?" for _ in models)
    value_sql = ",\n            ".join(
        value
        for parameter, (fc_col, obs_col) in PARAMETER_COLUMNS.items()
        for value in (
            f"f.{fc_col} AS fc_{PARAMETER_KEYS[parameter]}",
            f"o.{obs_col} AS ob_{PARAMETER_KEYS[parameter]}",
        )
    )
    return pd.read_sql_query(
        f"""
        SELECT {valid} AS Datetime,
               f.model AS Model,
               f.run_init_utc AS Collection_Cycle_UTC,
               f.scraped_at AS Collected_At_UTC,
               f.lead_hours AS Lead_Hour,
               {value_sql}
        FROM openmeteo_forecasts f
        INNER JOIN awos_observations o
          ON o.location=f.location AND o.obs_time={valid}
        WHERE {valid} >= ? AND {valid} <= ?
          AND f.run_init_utc <> 'historical_forecast_api'
          AND f.lead_hours >= 0
          AND f.model IN ({placeholders})
          AND {hard_valid}
          AND {clean_cycle}
        ORDER BY f.model, f.run_init_utc, {valid}
        """,
        conn,
        params=(start, end, *models),
    )


def _pair_scores(conn: sqlite3.Connection, start: str, end: str, models: list[str]) -> dict[str, Any]:
    qm_state, qm_counts = _load_enabled_qm(conn)
    all_pairs = _query_all_pairs(conn, start, end, models)
    results: dict[str, Any] = {}
    for parameter in PARAMETER_COLUMNS:
        internal = PARAMETER_KEYS[parameter]
        frame = all_pairs[
            ["Datetime", "Model", "Collection_Cycle_UTC", "Collected_At_UTC", "Lead_Hour",
             f"fc_{internal}", f"ob_{internal}"]
        ].rename(columns={f"fc_{internal}": "forecast", f"ob_{internal}": "obs"}).copy()
        frame["forecast"] = pd.to_numeric(frame["forecast"], errors="coerce")
        frame["obs"] = pd.to_numeric(frame["obs"], errors="coerce")
        frame = frame.dropna(subset=["forecast", "obs"])
        if parameter == "Wind Gust":
            frame = frame.loc[frame["obs"] > 0].copy()
        if frame.empty:
            results[parameter] = {"pair_count": 0}
            continue
        corrected = [
            _correct(value, str(model), parameter, float(lead), qm_state)
            for value, model, lead in zip(
                frame["forecast"], frame["Model"], frame["Lead_Hour"]
            )
        ]
        frame["historical_prior"] = [item[0] for item in corrected]
        frame["historical_plus_residual"] = [item[1] for item in corrected]
        frame["layer"] = [item[2] for item in corrected]
        results[parameter] = {
            "pair_count": int(len(frame)),
            "model_count": int(frame["Model"].nunique()),
            "first_valid_utc": str(frame["Datetime"].min()),
            "last_valid_utc": str(frame["Datetime"].max()),
            "layer_coverage": frame["layer"].value_counts().to_dict(),
            "raw": _score(parameter, frame, "forecast"),
            "historical_prior": _score(parameter, frame, "historical_prior"),
            "historical_plus_residual": _score(parameter, frame, "historical_plus_residual"),
        }
    return {"qm_rows": qm_counts, "parameters": results}


def _snapshot_comparison(conn: sqlite3.Connection, models: list[str]) -> dict[str, Any]:
    clean, audit = select_latest_clean_collections(conn, models)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(openmeteo_forecasts)")}
    valid = valid_time_utc_sql("f", has_explicit_column="valid_time_utc" in columns)
    placeholders = ",".join("?" for _ in models)
    latest = pd.read_sql_query(f"""
        WITH ranked AS (
            SELECT f.*, {valid} AS comparison_valid_utc,
                   DENSE_RANK() OVER (
                       PARTITION BY f.model ORDER BY f.scraped_at DESC, f.run_init_utc DESC
                   ) AS cycle_rank
            FROM openmeteo_forecasts f
            WHERE f.run_init_utc <> 'historical_forecast_api'
              AND f.lead_hours >= 0
              AND f.model IN ({placeholders})
        )
        SELECT * FROM ranked WHERE cycle_rank=1
    """, conn, params=tuple(models))
    if clean.empty or latest.empty:
        return {"selection": audit, "overlap_rows": 0, "parameter_differences": {}}
    if "valid_time_utc" in clean.columns:
        clean["comparison_valid_utc"] = clean["valid_time_utc"]
    else:
        clean["comparison_valid_utc"] = (
            pd.to_datetime(clean["forecast_time"], errors="coerce") - pd.Timedelta(hours=8)
        ).dt.strftime("%Y-%m-%d %H:%M:%S")
    merged = latest.merge(
        clean,
        on=["model", "comparison_valid_utc"],
        suffixes=("_latest", "_clean"),
    )
    differences = {}
    for parameter, column in SNAPSHOT_COLUMNS.items():
        left = pd.to_numeric(merged[f"{column}_latest"], errors="coerce")
        right = pd.to_numeric(merged[f"{column}_clean"], errors="coerce")
        mask = left.notna() & right.notna()
        if parameter == "Wind Dir.":
            delta = ((left[mask] - right[mask] + 180.0) % 360.0) - 180.0
        else:
            delta = left[mask] - right[mask]
        differences[parameter] = {
            "comparable_rows": int(mask.sum()),
            "changed_rows": int(delta.abs().gt(1e-9).sum()),
            "mean_absolute_difference": _safe(delta.abs().mean()),
            "maximum_absolute_difference": _safe(delta.abs().max()),
        }
    return {
        "selection": audit,
        "overlap_rows": int(len(merged)),
        "parameter_differences": differences,
    }


def run_shadow_comparison(database: str | Path, lookback_days: int = 60) -> dict[str, Any]:
    database = Path(database).resolve()
    uri = f"file:{database.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        last_obs = conn.execute("SELECT MAX(obs_time) FROM awos_observations").fetchone()[0]
        if not last_obs:
            raise ValueError("No hourly AWOS observations available")
        end = pd.Timestamp(last_obs)
        start = end - pd.Timedelta(days=lookback_days)
        models = list(MODEL_REGISTRY)
        return {
            "shadow_version": SHADOW_VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": str(database),
            "lookback_days": lookback_days,
            "start_utc": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_utc": end.strftime("%Y-%m-%d %H:%M:%S"),
            "observe_only": True,
            "promotion_eligible": False,
            "caveats": [
                "Scores use archived operational pairs that pass current UTC and whole-cycle quarantine contracts.",
                "Existing CDF artifacts may have been fitted using overlapping data; this is diagnostic, not independent promotion evidence.",
                "Operational residual CDFs without the current pairing contract are rejected from the shadow variant.",
                "Rainfall occurrence and wet/wet amount are scored separately.",
            ],
            "latest_snapshot": _snapshot_comparison(conn, models),
            "paired_scores": _pair_scores(
                conn,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"),
                models,
            ),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="wawp_forecasts.db")
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--output", default="audit/shadow/policy_comparison.json")
    args = parser.parse_args()
    report = run_shadow_comparison(args.database, args.lookback_days)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "observe_only": report["observe_only"],
        "selection_changed_models": report["latest_snapshot"]["selection"]["selection_changed_models"],
        "output": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
