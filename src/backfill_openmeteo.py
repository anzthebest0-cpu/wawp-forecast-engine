"""
Backfill Open-Meteo previous-run forecasts into openmeteo_forecasts.

Network calls are resumable: existing (model, run_init_utc) runs are skipped.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db_manager import ForecastDB
from src.scrape_openmeteo import (
    HOURLY_PARAMS,
    LATITUDE,
    LOCATION_NAME,
    LONGITUDE,
    MODELS_OPENMETEO,
    TIMEZONE,
)

SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_MONTHS = {
    "ECMWF": 24,
    "GFS": 12,
    "ICON": 12,
    "CMA": 11,
    "METEOFRANCE": 11,
    "UKMO": 5,
    "GEM": 3,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("openmeteo_backfill")


def _root_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fetch_json(url: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                import json
                body = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    log.warning(f"Non-JSON Open-Meteo response skipped: {body[:160]}")
                    return {}
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return {}
            if e.code == 429 and attempt < retries - 1:
                time.sleep(60)
                continue
            raise
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return {}


def existing_run(db_path: str, model: str, run_init_utc: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM openmeteo_forecasts WHERE model=? AND run_init_utc=? LIMIT 1",
            (model, run_init_utc),
        ).fetchone()
    return row is not None


def build_rows(model: str, run_init_utc: str, payload: dict) -> list[dict]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    run_dt = datetime.strptime(run_init_utc, "%Y-%m-%d %H:%M:%S")
    for idx, t in enumerate(times):
        valid_dt = datetime.strptime(t, "%Y-%m-%dT%H:%M")

        def h(name, default=None):
            values = hourly.get(name) or []
            return values[idx] if idx < len(values) else default

        rain = float(h("rain", 0.0) or 0.0)
        showers = float(h("showers", 0.0) or 0.0)
        rows.append({
            "location": LOCATION_NAME,
            "model": model,
            "run_init_utc": run_init_utc,
            "forecast_time": valid_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "lead_hours": (valid_dt - (run_dt + timedelta(hours=8))).total_seconds() / 3600.0,
            "scraped_at": scraped_at,
            "temperature": h("temperature_2m"),
            "dewpoint": h("dew_point_2m"),
            "humidity": h("relative_humidity_2m"),
            "pressure_msl": h("pressure_msl"),
            "rain": rain + showers,
            "showers": showers,
            "wind_speed": h("wind_speed_10m"),
            "wind_gust": h("wind_gusts_10m"),
            "wind_dir": h("wind_direction_10m"),
            "cloud_cover": h("cloud_cover"),
            "cloud_cover_low": h("cloud_cover_low"),
            "cloud_cover_mid": h("cloud_cover_mid"),
            "cloud_cover_high": h("cloud_cover_high"),
            "weather_code": h("weather_code"),
            "visibility": h("visibility"),
            "cape": h("cape"),
            "lifted_index": h("lifted_index"),
            "convective_inhib": h("convective_inhibition"),
            "boundary_layer_h": h("boundary_layer_height"),
        })
    return rows


def fetch_run(model: str, model_id: str, run_date: date, run_hour: int) -> tuple[str, list[dict]]:
    run_init_utc = datetime(run_date.year, run_date.month, run_date.day, run_hour).strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(HOURLY_PARAMS),
        "models": model_id,
        "forecast_days": 16,
        "timezone": TIMEZONE,
        "wind_speed_unit": "kn",
        "precipitation_unit": "mm",
        "run": f"{run_date.isoformat()}T{run_hour:02d}:00",
    }
    url = SINGLE_RUNS_URL + "?" + urllib.parse.urlencode(params)
    payload = _fetch_json(url)
    return run_init_utc, build_rows(model, run_init_utc, payload) if payload else []


def backfill_model(db_path: str, model: str, dry_run: bool = False, max_workers: int = 6) -> int:
    model_id = MODELS_OPENMETEO[model]
    months = ARCHIVE_MONTHS.get(model, 12)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=months * 30)
    runs = []
    d = start
    while d <= end:
        for hour in (0, 6, 12, 18):
            run_init_utc = datetime(d.year, d.month, d.day, hour).strftime("%Y-%m-%d %H:%M:%S")
            if not existing_run(db_path, model, run_init_utc):
                runs.append((d, hour))
        d += timedelta(days=1)

    log.info(f"{model}: {len(runs)} runs pending from {start} to {end}")
    if dry_run:
        return 0

    inserted = 0
    db = ForecastDB(db_path)
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(fetch_run, model, model_id, run_date, hour) for run_date, hour in runs]
            for future in as_completed(futures):
                run_init_utc, rows = future.result()
                if rows:
                    inserted += db.ingest_openmeteo_rows(rows)
                    log.info(f"{model} {run_init_utc}: {len(rows)} rows fetched")
                time.sleep(0.5)
    finally:
        db.close()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS_OPENMETEO.keys()))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=os.path.join(_root_dir(), "wawp_forecasts.db"))
    args = parser.parse_args()

    models = [args.model] if args.model else list(MODELS_OPENMETEO.keys())
    total = 0
    for model in models:
        total += backfill_model(args.db, model, dry_run=args.dry_run)
    log.info(f"Backfill complete. Inserted {total} rows.")


if __name__ == "__main__":
    main()
