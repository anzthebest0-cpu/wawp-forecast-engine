"""Fail-closed raw-model preview. Never generates or authorizes a TAF.

The existing ensemble/QM and TAF algorithms remain research code until
independent verification establishes their suitability for aviation guidance.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

from src.db_manager import LOCATION_NAME
from src.forecast_selection import select_latest_clean_collections
from src.model_registry import PREVIEW_MODEL_REGISTRY as MODEL_REGISTRY, PREVIEW_ONLY_MODELS, model_metadata_dict

CONTRACT = "raw-model-preview-v1"
WINDOWS = ("2300", "0500", "1100", "1700", "default")
# Internal conservative preview expiry; not a BMKG/ICAO issuance criterion.
MAX_COLLECTION_AGE_HOURS = 6.0
FIELDS = {"Temperature": "temperature", "Dewpoint": "dewpoint", "Humidity": "humidity",
          "Pressure": "pressure_msl", "Rainfall": "rain", "Wind Speed": "wind_speed",
          "Wind Dir.": "wind_dir", "Wind Gust": "wind_gust", "Visibility": "visibility",
          "Precip Probability": "precipitation_probability", "Low Clouds": "cloud_cover_low",
          "Mid Clouds": "cloud_cover_mid", "High Clouds": "cloud_cover_high", "CAPE": "cape",
          "Lifted Index": "lifted_index", "Convective Inhibition": "convective_inhib", "Weather Code": "weather_code"}


def utc(value):
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(stamp) else stamp.to_pydatetime()


def scalar(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (str, bool, int)):
        return value
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return str(value)


def selection_manifest(conn, models=tuple(MODEL_REGISTRY), *, now: datetime):
    if now.tzinfo is None:
        raise ValueError("An absolute UTC cutoff is required")
    now = now.astimezone(timezone.utc)
    cutoff = now.strftime("%Y-%m-%d %H:%M:%S")
    rows, audit = select_latest_clean_collections(conn, models, as_of_utc=cutoff)
    selected, excluded, deadlines = {}, {}, []
    accepted = []
    if not rows.empty:
        for model, group in rows.groupby("model", sort=True):
            group = group.loc[group["location"] == LOCATION_NAME].copy()
            if group.empty:
                excluded[model] = "station_mismatch"
                continue
            pulled = utc(group["scraped_at"].iloc[0])
            age = (now - pulled).total_seconds() / 3600 if pulled else None
            cadence = model_metadata_dict(model).get("provider_update_frequency_hours") or 6.0
            limit = min(MAX_COLLECTION_AGE_HOURS, float(cadence) * 1.25)
            if age is None or not 0 <= age <= limit:
                excluded[model] = "expired_or_future_collection"
                continue
            valid = pd.to_datetime(group["valid_time_utc"], utc=True, errors="coerce")
            local = pd.to_datetime(group["forecast_time"], utc=True, errors="coerce")
            # The operational archive labels local valid time WITA. Refuse
            # mismatching hour labels rather than silently reassigning them.
            group = group.loc[(valid >= pd.Timestamp(now)) & (local == valid + pd.Timedelta(hours=8))].copy()
            if group.empty or group["valid_time_utc"].duplicated().any():
                excluded[model] = "no_future_coverage_or_duplicate_valid_time"
                continue
            core = ("temperature", "dewpoint", "pressure_msl", "wind_speed", "precipitation")
            if not any(name in group and pd.to_numeric(group[name], errors="coerce").notna().any() for name in core):
                excluded[model] = "no_usable_numeric_evidence"
                continue
            expiry = pulled + timedelta(hours=limit)
            deadlines.append(expiry)
            selected[model] = {"scraped_at_utc": group["scraped_at"].iloc[0],
                               "collection_cycle_proxy_utc": group["run_init_utc"].iloc[0],
                               "row_count": int(len(group)), "expires_at_utc": expiry.isoformat()}
            accepted.append(group)
    frame = pd.concat(accepted, ignore_index=True).sort_values(["model", "valid_time_utc", "id"]) if accepted else pd.DataFrame()
    digest = hashlib.sha256()
    for row in frame.itertuples(index=False, name=None):
        digest.update(json.dumps([scalar(v) for v in row], ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n")
    audit.update({"applied": True, "operational_policy": "raw model preview; exact as-of clean and unexpired cohorts"})
    manifest = {"contract": CONTRACT, "status": "model_only" if selected else "unavailable",
                "as_of_utc": now.isoformat(), "expires_at_utc": min(deadlines).isoformat() if deadlines else None,
                "provider_initialization_available": False, "rows_sha256": digest.hexdigest(),
                "row_count": int(len(frame)), "models": selected, "excluded_models": excluded,
                "taf_status": "withheld_pending_independent_validation"}
    return frame, manifest, audit


def withheld_taf(reason="Automated TAF withheld pending independent meteorological validation."):
    return {window: {"status": "withheld", "taf_text": None, "base_group": None,
                     "warnings": [reason], "narration": reason} for window in WINDOWS}


def _write(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")


def _mean(values, *, circular=False):
    values = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not values:
        return None
    if circular:
        x = sum(math.cos(math.radians(v)) for v in values)
        y = sum(math.sin(math.radians(v)) for v in values)
        if math.hypot(x, y) < 1e-9:
            return None
        return math.degrees(math.atan2(y, x)) % 360
    return sum(values) / len(values)


def preview_payloads(frame, manifest):
    individual = {name: {} for name in FIELDS}
    records = []
    # Historic records with missing provider total are ambiguous. Do not turn
    # old fabricated zeroes into dry guidance: only use explicit total here.
    for name, col in FIELDS.items():
        for model, group in frame.groupby("model") if not frame.empty else []:
            raw_col = "precipitation" if col == "rain" else col
            individual[name][model] = {str(row["forecast_time"]): scalar(row.get(raw_col)) for _, row in group.iterrows()}
    # Keep the correlated IFS variant visible as independent research guidance,
    # without silently increasing ECMWF's contribution to the raw mean.
    mean_frame = frame.loc[~frame["model"].isin(PREVIEW_ONLY_MODELS)] if not frame.empty else frame
    for timestamp, group in mean_frame.groupby("forecast_time", sort=True) if not mean_frame.empty else []:
        record = {"Datetime": str(timestamp), "Model_Count": int(group["model"].nunique()), "Field_Model_Count": {}}
        for name, col in FIELDS.items():
            if name == "Weather Code":
                continue  # categorical code is not an arithmetic mean
            label = {"Rainfall": "Rain", "Wind Speed": "Wind", "Wind Gust": "Gust"}.get(name, name)
            raw_col = "precipitation" if col == "rain" else col
            values = [scalar(v) for v in group[raw_col]] if raw_col in group else []
            record["Field_Model_Count"][label] = sum(v is not None for v in values)
            record[label] = _mean(values, circular=name == "Wind Dir.")
        records.append(record)
    individual["Run_Init"] = {m: v["collection_cycle_proxy_utc"] for m, v in manifest["models"].items()}
    individual["Data_Pull"] = {m: v["scraped_at_utc"] for m, v in manifest["models"].items()}
    individual["Model_Metadata"] = {m: model_metadata_dict(m) for m in manifest["models"]}
    individual["Preview_Manifest"] = manifest
    return {"metadata": {"preview_manifest": manifest, "method": "raw equal model mean of original model set; IFS 9 km shown independently; unverified research preview",
                         "generated_at": manifest["as_of_utc"], "latest_data_pull_utc": max(individual["Data_Pull"].values(), default=None)},
            "data": records}, individual


def export_preview(conn, output_dir, *, now=None, models=tuple(MODEL_REGISTRY)):
    now = now or datetime.now(timezone.utc)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    frame, manifest, audit = selection_manifest(conn, models, now=now)
    guidance, individual = preview_payloads(frame, manifest)
    _write(output / "taf_guidance.json", guidance)
    _write(output / "individual_models.json", individual)
    _write(output / "forecast_selection.json", audit)
    _write(output / "tafor_intel.json", withheld_taf())
    _write(output / "tafor_shadow.json", {"status": "withheld", "issuances": {}})
    _write(output / "model_preview_manifest.json", manifest)
    return manifest
