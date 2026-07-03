"""
Backfill Open-Meteo Historical Forecast API data for QM training.

This endpoint returns a continuous forecast time series. It is suitable for
forecast-observation training pairs, but it does not provide full forecast
horizons from individual model runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db_manager import ForecastDB
from src.scrape_openmeteo import LATITUDE, LOCATION_NAME, LONGITUDE

HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

HISTORICAL_HOURLY_PARAMS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "pressure_msl",
    "rain",
    "showers",
    "precipitation",
    "snowfall",
    "weather_code",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "visibility",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "cape",
    "lifted_index",
    "convective_inhibition",
    "boundary_layer_height",
    "sunshine_duration",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "precipitation_probability",
    "soil_temperature_0_to_7cm",
    "soil_moisture_0_to_7cm",
]

HISTORICAL_MODELS_OPENMETEO = {
    # ECMWF HRES only, per migration requirement.
    "ECMWF_HRES": "ecmwf_ifs025",
    "GFS_GLOBAL": "gfs_global",
    "ICON_SEAMLESS": "icon_seamless",
    "GEM_GLOBAL": "gem_global",
    "CMA_GRAPES_GLOBAL": "cma_grapes_global",
    "JMA_GSM": "jma_gsm",
    "METEOFRANCE_ARPEGE_WORLD": "meteofrance_arpege_world",
    "UKMO_GLOBAL_10KM": "ukmo_global_deterministic_10km",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("historical_forecast")


def _root_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fetch_json(params: dict, retries: int = 3) -> dict:
    url = HISTORICAL_FORECAST_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                body = response.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read(1000).decode("utf-8", errors="replace")
            if e.code == 429 and attempt < retries - 1:
                time.sleep(60)
                continue
            log.warning(f"HTTP {e.code}: {body[:240]}")
            return {}
        except Exception as e:
            if attempt == retries - 1:
                log.warning(f"Fetch failed: {e}")
                return {}
            time.sleep(2 ** attempt)
    return {}


def _date_chunks(start: pd.Timestamp, end: pd.Timestamp, days: int = 31):
    cursor = start.normalize()
    final = end.normalize()
    while cursor <= final:
        chunk_end = min(cursor + pd.Timedelta(days=days - 1), final)
        yield cursor.date().isoformat(), chunk_end.date().isoformat()
        cursor = chunk_end + pd.Timedelta(days=1)


def _expected_hours(start_date: str, end_date: str) -> int:
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    return int(((end - start).days + 1) * 24)


def _get_awos_period(db_path: str) -> tuple[str, str]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT MIN(obs_time), MAX(obs_time) FROM awos_observations WHERE temperature IS NOT NULL").fetchone()
    if not row or not row[0] or not row[1]:
        raise RuntimeError("No hourly AWOS observations available to define historical backfill period.")
    return row[0][:10], row[1][:10]


def _rows_from_payload(model_name: str, payload: dict) -> list[dict]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = []

    def h(name: str, idx: int, default=None):
        values = hourly.get(name) or []
        return values[idx] if idx < len(values) else default

    for idx, time_str in enumerate(times):
        # Historical backfill is requested in UTC so this joins AWOS obs_time.
        forecast_time = pd.to_datetime(time_str).strftime("%Y-%m-%d %H:%M:%S")
        rain = h("rain", idx, 0.0) or 0.0
        showers = h("showers", idx, 0.0) or 0.0
        precipitation = h("precipitation", idx, None)
        snowfall = h("snowfall", idx, 0.0) or 0.0
        total_rain = float(precipitation) if precipitation is not None else float(rain) + float(showers) + float(snowfall)
        rows.append({
            "location": LOCATION_NAME,
            "model": model_name,
            "run_init_utc": "historical_forecast_api",
            "forecast_time": forecast_time,
            "lead_hours": 0.0,
            "scraped_at": scraped_at,
            "temperature": h("temperature_2m", idx),
            "dewpoint": h("dew_point_2m", idx),
            "humidity": h("relative_humidity_2m", idx),
            "pressure_msl": h("pressure_msl", idx),
            "rain": total_rain,
            "precipitation": precipitation,
            "showers": showers,
            "snowfall": snowfall,
            "wind_speed": h("wind_speed_10m", idx),
            "wind_gust": h("wind_gusts_10m", idx),
            "wind_dir": h("wind_direction_10m", idx),
            "cloud_cover": h("cloud_cover", idx),
            "cloud_cover_low": h("cloud_cover_low", idx),
            "cloud_cover_mid": h("cloud_cover_mid", idx),
            "cloud_cover_high": h("cloud_cover_high", idx),
            "weather_code": h("weather_code", idx),
            "visibility": h("visibility", idx),
            "cape": h("cape", idx),
            "lifted_index": h("lifted_index", idx),
            "convective_inhib": h("convective_inhibition", idx),
            "boundary_layer_h": h("boundary_layer_height", idx),
            "sunshine_duration": h("sunshine_duration", idx),
            "shortwave_radiation": h("shortwave_radiation", idx),
            "direct_radiation": h("direct_radiation", idx),
            "diffuse_radiation": h("diffuse_radiation", idx),
            "precipitation_probability": h("precipitation_probability", idx),
            "soil_temperature_0_to_7cm": h("soil_temperature_0_to_7cm", idx),
            "soil_moisture_0_to_7cm": h("soil_moisture_0_to_7cm", idx),
        })
    return rows


def fetch_model_chunk(model_name: str, model_id: str, start_date: str, end_date: str) -> list[dict]:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(HISTORICAL_HOURLY_PARAMS),
        "models": model_id,
        "timezone": "UTC",
        "wind_speed_unit": "kn",
        "precipitation_unit": "mm",
    }
    payload = _fetch_json(params)
    return _rows_from_payload(model_name, payload) if payload else []


def backfill(db_path: str, models: list[str], start_date: str, end_date: str, chunk_days: int = 31) -> dict:
    db = ForecastDB(db_path)
    summary = {}
    try:
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        for model_name in models:
            model_id = HISTORICAL_MODELS_OPENMETEO[model_name]
            inserted = 0
            fetched = 0
            skipped = 0
            for c_start, c_end in _date_chunks(start, end, days=chunk_days):
                existing = db.conn.execute("""
                    SELECT COUNT(*)
                    FROM openmeteo_forecasts
                    WHERE run_init_utc='historical_forecast_api'
                      AND model=?
                      AND forecast_time >= ?
                      AND forecast_time <= ?
                """, (model_name, f"{c_start} 00:00:00", f"{c_end} 23:00:00")).fetchone()[0]
                expected = _expected_hours(c_start, c_end)
                if existing >= expected:
                    skipped += expected
                    log.info(f"{model_name} {c_start}..{c_end}: skipped existing={existing}/{expected}")
                    continue
                rows = fetch_model_chunk(model_name, model_id, c_start, c_end)
                fetched += len(rows)
                inserted += db.ingest_openmeteo_rows(rows)
                log.info(f"{model_name} {c_start}..{c_end}: fetched={len(rows)} inserted_total={inserted}")
                time.sleep(0.2)
            summary[model_name] = {"fetched": fetched, "inserted": inserted, "skipped": skipped}
    finally:
        db.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.path.join(_root_dir(), "wawp_forecasts.db"))
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--model", action="append", choices=sorted(HISTORICAL_MODELS_OPENMETEO.keys()))
    parser.add_argument("--chunk-days", type=int, default=31)
    args = parser.parse_args()

    start_date, end_date = (args.start_date, args.end_date)
    if not start_date or not end_date:
        _, end_date = _get_awos_period(args.db)
        start_date = "2023-01-01"
    models = args.model or list(HISTORICAL_MODELS_OPENMETEO.keys())
    log.info(f"Historical Forecast API backfill: {start_date}..{end_date}, models={models}")
    summary = backfill(args.db, models, start_date, end_date, chunk_days=args.chunk_days)
    for model, stats in summary.items():
        log.info(f"{model}: fetched={stats['fetched']} inserted={stats['inserted']} skipped={stats['skipped']}")


if __name__ == "__main__":
    main()
