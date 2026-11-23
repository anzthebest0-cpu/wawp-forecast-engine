"""Bounded Open-Meteo retrieval and immutable native collection ingestion.

No TAF, verification promotion or calibration. Collection time is a receipt
proxy, never a provider initialization. Exact response bytes are retained.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from src.model_registry import MODEL_REGISTRY
from src.scrape_openmeteo import LATITUDE, LONGITUDE, LOCATION_NAME, OPENMETEO_FORECAST_URL

CONTRACT = "openmeteo-immutable-receipt-v1"
CORE = {"temperature_2m": ("temperature", "°C"), "dew_point_2m": ("dewpoint", "°C"),
        "relative_humidity_2m": ("humidity", "%"), "pressure_msl": ("pressure_msl", "hPa"),
        "precipitation": ("precipitation", "mm"), "wind_speed_10m": ("wind_speed", "kn"),
        "wind_direction_10m": ("wind_dir", "°"), "cloud_cover": ("cloud_cover", "%")}
OPTIONAL = {"wind_gusts_10m": ("wind_gust", "kn"), "cloud_cover_low": ("cloud_cover_low", "%"),
            "cloud_cover_mid": ("cloud_cover_mid", "%"), "cloud_cover_high": ("cloud_cover_high", "%"),
            "weather_code": ("weather_code", "wmo code"), "visibility": ("visibility", "m"),
            "precipitation_probability": ("precipitation_probability", "%"), "showers": ("showers", "mm"),
            "snowfall": ("snowfall", "cm"), "cape": ("cape", "J/kg"), "lifted_index": ("lifted_index", "K"),
            "convective_inhibition": ("convective_inhib", "J/kg"), "boundary_layer_height": ("boundary_layer_h", "m")}
FIELDS = {**CORE, **OPTIONAL}
MAX_PREVIEW_HOURS = 168  # Explicit seven-day preview; not a change to archived product horizon.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class RateLimited(RuntimeError):
    """Provider asked us to stop; never seek another endpoint to bypass this."""


class RequestRejected(RuntimeError):
    """Explicit 400/422 request schema rejection."""


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def retry_after(value, now):
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(value)
                return max(0.0, (parsed - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return 30.0


def request_bytes(url, *, deadline, attempts=2, opener=None, monotonic=time.monotonic,
                  sleeper=time.sleep, clock=lambda: datetime.now(timezone.utc)):
    opener = opener or urllib.request.urlopen
    for attempt in range(attempts):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("collection deadline reached")
        request = urllib.request.Request(url, headers={"User-Agent": "wawp-model-preview/1.0 (+https://github.com/anzthebest0-cpu/wawp-forecast-engine)"})
        try:
            with opener(request, timeout=min(15.0, remaining)) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("provider response exceeds configured bound")
                return body, clock().astimezone(timezone.utc)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 422):
                raise RequestRejected(f"HTTP {exc.code}") from exc
            if exc.code == 429:
                delay = retry_after(exc.headers.get("Retry-After"), clock())
                if attempt + 1 == attempts or delay >= deadline - monotonic():
                    raise RateLimited("HTTP 429: retry budget exhausted") from exc
            elif 500 <= exc.code < 600:
                delay = 5.0 * (attempt + 1)
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            delay = 5.0 * (attempt + 1)
        if attempt + 1 == attempts or delay >= deadline - monotonic():
            raise TimeoutError("provider request retry budget exhausted")
        sleeper(delay)
    raise TimeoutError("provider attempts exhausted")


def request_url(model, fields):
    meta = MODEL_REGISTRY[model]
    params = {"latitude": LATITUDE, "longitude": LONGITUDE, "hourly": ",".join(fields),
              "models": meta.openmeteo_id, "forecast_hours": min(meta.forecast_horizon_hours, MAX_PREVIEW_HOURS),
              "timezone": "UTC", "wind_speed_unit": "kn", "precipitation_unit": "mm"}
    return OPENMETEO_FORECAST_URL + "?" + urllib.parse.urlencode(params)


def number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("non-numeric or nonfinite provider value")
    return value


def parse_response(body, model, received, requested):
    """Strict UTC, native-unit and axis validation; absent optional fields stay null."""
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get("error") or payload.get("utc_offset_seconds") != 0:
        raise ValueError("provider response error or non-UTC basis")
    for key, expected in (("latitude", LATITUDE), ("longitude", LONGITUDE)):
        value = number(payload.get(key))
        if value is None or abs(value - expected) > 1.0:
            raise ValueError("provider grid location mismatch")
    hourly, units = payload.get("hourly"), payload.get("hourly_units")
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        raise ValueError("missing hourly payload/units")
    times = hourly.get("time")
    if not isinstance(times, list) or len(times) < 24 or len(times) > MAX_PREVIEW_HOURS + 1:
        raise ValueError("insufficient or out-of-bounds time axis")
    parsed = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in times]
    # The requested ISO8601 UTC response uses naive hour labels. Reject an
    # offset-bearing time rather than silently overriding its source offset.
    if any(datetime.fromisoformat(t).tzinfo is not None for t in times) or any(t.minute or t.second or t.microsecond for t in parsed):
        raise ValueError("unexpected timestamp format")
    if any((b-a) != timedelta(hours=1) for a,b in zip(parsed, parsed[1:])):
        raise ValueError("non-contiguous or duplicate provider time axis")
    arrays = {}
    for field in requested:
        if field not in hourly:
            continue
        values = hourly[field]
        if not isinstance(values, list) or len(values) != len(parsed):
            raise ValueError(f"misaligned array: {field}")
        # Entire null optional arrays provide no evidence; unit still must
        # match if values exist. Do not guess a conversion.
        if any(v is not None for v in values) and units.get(field) != FIELDS[field][1]:
            continue  # unsupported/unexpected unit => unknown, recorded below
        arrays[field] = [number(v) for v in values]
    rows = []
    received = received.astimezone(timezone.utc).replace(microsecond=0)
    collection = iso(received)
    for i, valid in enumerate(parsed):
        if valid < received:
            continue
        row = {"location": LOCATION_NAME, "model": model, "run_init_utc": collection,
               "scraped_at": collection, "valid_time_utc": iso(valid),
               "forecast_time": iso(valid + timedelta(hours=8)), "forecast_time_basis": "forecast_api_wita",
               "lead_hours": (valid-received).total_seconds()/3600}
        for field, (column, _) in FIELDS.items():
            row[column] = arrays[field][i] if field in arrays else None
        row["rain"] = row["precipitation"]
        # Quarantine impossible values; retain exact provider bytes separately.
        for col in ("rain", "precipitation", "showers", "snowfall", "wind_speed", "wind_gust", "visibility", "cape"):
            if row.get(col) is not None and row[col] < 0:
                row[col] = None
        for col in ("humidity", "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "precipitation_probability"):
            if row.get(col) is not None and not 0 <= row[col] <= 100:
                row[col] = None
        if row["wind_dir"] is not None and not 0 <= row["wind_dir"] <= 360:
            row["wind_dir"] = None
        if row["dewpoint"] is not None and row["temperature"] is not None and row["dewpoint"] > row["temperature"] + 0.2:
            row["dewpoint"] = None
        if row["wind_gust"] is not None and row["wind_speed"] is not None and row["wind_gust"] < row["wind_speed"]:
            row["wind_gust"] = None
        if row["lifted_index"] is not None and abs(row["lifted_index"]) > 30:
            row["lifted_index"] = None
        if row["weather_code"] not in (0,1,2,3,45,48,51,53,55,56,57,61,63,65,66,67,71,73,75,77,80,81,82,85,86,95,96,99):
            row["weather_code"] = None
        rows.append(row)
    if len(rows) < 24 or not any(any(row.get(c) is not None for c in ("temperature", "wind_speed", "pressure_msl", "precipitation")) for row in rows):
        raise ValueError("no usable future model coverage")
    return rows, {"grid_latitude": payload["latitude"], "grid_longitude": payload["longitude"],
                  "units": units, "unknown_or_unavailable_fields": sorted(set(requested)-set(arrays)),
                  "field_counts": {c: sum(r.get(c) is not None for r in rows) for c, _ in FIELDS.values()}}


def collect(*, budget_seconds=240, models=tuple(MODEL_REGISTRY), requester=request_bytes):
    deadline = time.monotonic() + budget_seconds
    responses, outcomes = [], []
    stop = None
    for model in models:
        if stop or time.monotonic() >= deadline:
            outcomes.append({"model": model, "status": "not_attempted", "reason": stop or "deadline"})
            continue
        for fields in (tuple(FIELDS), tuple(CORE)):
            url = request_url(model, fields)
            try:
                body, received = requester(url, deadline=deadline)
                rows, detail = parse_response(body, model, received, fields)
                digest = hashlib.sha256(body).hexdigest()
                metadata = {"model": model, "provider_model_id": MODEL_REGISTRY[model].openmeteo_id,
                            "retrieved_at_utc": iso(received), "provider_initialization_utc": None,
                            "requested_fields": list(fields),
                            "request_url": url, "payload_sha256": digest, "payload_bytes": len(body),
                            "row_count": len(rows), **detail}
                responses.append((metadata, body, rows))
                outcomes.append({"model": model, "status": "accepted", "rows": len(rows), "sha256": digest,
                                 "request_profile": "full" if len(fields)==len(FIELDS) else "core"})
                break
            except RequestRejected:
                if len(fields) == len(FIELDS):
                    continue
                outcomes.append({"model": model, "status": "rejected", "reason": "provider_schema_rejected"})
            except RateLimited:
                stop = "provider_rate_limit"
                outcomes.append({"model": model, "status": "unavailable", "reason": stop})
                break
            except Exception as exc:
                # A malformed response is rejected, never converted to dry or
                # retried using another provider identity.
                outcomes.append({"model": model, "status": "unavailable", "reason": type(exc).__name__})
                break
    return responses, outcomes


def ingest_immutable(conn: sqlite3.Connection, responses, *, evidence_uri, evidence_sha256):
    """Insert new cohorts only; exact retries are no-ops, collisions fail."""
    if not evidence_uri or not evidence_sha256:
        raise ValueError("Verified evidence archive receipt is required")
    inserted = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS openmeteo_collection_receipts (
            model TEXT NOT NULL, retrieved_at_utc TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL, evidence_uri TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
            PRIMARY KEY(model,retrieved_at_utc))""")
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name IN ('openmeteo_forecasts','openmeteo_collection_receipts')").fetchone():
            raise ValueError("Unexpected forecast-table trigger")
        for metadata, body, rows in responses:
            key = (metadata["model"], metadata["retrieved_at_utc"])
            if hashlib.sha256(body).hexdigest() != metadata["payload_sha256"] or len(rows) != metadata["row_count"]:
                raise ValueError("Response content mismatch")
            reconstructed, _ = parse_response(body, key[0], datetime.fromisoformat(key[1]).replace(tzinfo=timezone.utc), metadata["requested_fields"])
            if reconstructed != rows:
                raise ValueError("Normalized rows do not match verified response")
            current = conn.execute("SELECT payload_sha256 FROM openmeteo_collection_receipts WHERE model=? AND retrieved_at_utc=?", key).fetchone()
            if current:
                if current[0] != metadata["payload_sha256"]:
                    raise ValueError("Different payload for existing collection receipt")
                if conn.execute("SELECT COUNT(*) FROM openmeteo_forecasts WHERE model=? AND run_init_utc=?", key).fetchone()[0] != len(rows):
                    raise ValueError("Existing receipt no longer has complete forecast rows")
                continue
            if conn.execute("SELECT 1 FROM openmeteo_forecasts WHERE model=? AND run_init_utc=?", key).fetchone():
                raise ValueError("Collection proxy collides with existing historical row")
            columns = list(rows[0])
            conn.executemany(f"INSERT INTO openmeteo_forecasts ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                             [tuple(row.get(c) for c in columns) for row in rows])
            conn.execute("INSERT INTO openmeteo_collection_receipts VALUES (?,?,?,?,?,?)",
                         (*key, metadata["payload_sha256"], json.dumps(metadata, sort_keys=True), evidence_uri, evidence_sha256))
            inserted += len(rows)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return inserted
