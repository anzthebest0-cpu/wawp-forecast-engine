"""
Open-Meteo client for the WAWP forecast pipeline.

The client emits two compatible products:
  1. rows for openmeteo_forecasts
  2. dashboard-shaped rows consumed by older exporter code paths
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

LOCATION_NAME = "Bandara_Sangia_Ni_Bandera"
LATITUDE = -4.338158
LONGITUDE = 121.524047
TIMEZONE = "Asia/Makassar"
WITA_OFFSET_HOURS = 8

OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_PARAMS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "pressure_msl",
    "precipitation_probability",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "weather_code",
    "visibility",
    "cape",
    "lifted_index",
    "convective_inhibition",
    "boundary_layer_height",
]

MODELS_OPENMETEO = {
    "ECMWF_HRES": "ecmwf_ifs025",
    "GFS_GLOBAL": "gfs_global",
    "GFS_SEAMLESS": "gfs_seamless",
    "ICON_GLOBAL": "icon_global",
    "ICON_SEAMLESS": "icon_seamless",
    "GEM_GLOBAL": "gem_global",
    "GEM_SEAMLESS": "gem_seamless",
    "CMA_GRAPES_GLOBAL": "cma_grapes_global",
    "JMA_GSM": "jma_gsm",
    "BOM_ACCESS_GLOBAL": "bom_access_global",
    "METEOFRANCE_ARPEGE_WORLD": "meteofrance_arpege_world",
    "UKMO_GLOBAL_10KM": "ukmo_global_deterministic_10km",
    "ERA5_SEAMLESS": "era5_seamless",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("openmeteo")


def _get_json(url: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 400:
                log.warning(f"Open-Meteo 400 skipped: {url}")
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


def fetch_model(model_name: str, model_id: str, forecast_days: int = 16) -> tuple[list[dict], list[dict]]:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(HOURLY_PARAMS),
        "models": model_id,
        "forecast_days": forecast_days,
        "timezone": TIMEZONE,
        "wind_speed_unit": "kn",
        "precipitation_unit": "mm",
    }
    url = OPENMETEO_FORECAST_URL + "?" + urllib.parse.urlencode(params)
    payload = _get_json(url)
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return [], []

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    generation = payload.get("generationtime_ms")
    run_init_utc = scraped_at[:14] + "00:00"
    if times:
        first_valid_wita = pd.to_datetime(times[0])
        run_init_utc = (first_valid_wita - pd.Timedelta(hours=WITA_OFFSET_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

    dashboard_rows = []
    openmeteo_rows = []
    for idx, time_str in enumerate(times):
        valid_wita = pd.to_datetime(time_str)
        forecast_time = valid_wita.strftime("%Y-%m-%d %H:%M:%S")
        lead_hours = (valid_wita - pd.to_datetime(run_init_utc) - pd.Timedelta(hours=WITA_OFFSET_HOURS)).total_seconds() / 3600.0

        def h(name, default=None):
            values = hourly.get(name) or []
            return values[idx] if idx < len(values) else default

        rain = h("rain", 0.0) or 0.0
        showers = h("showers", 0.0) or 0.0
        snowfall = h("snowfall", 0.0) or 0.0
        precipitation = h("precipitation")
        total_rain = float(precipitation) if precipitation is not None else float(rain) + float(showers) + float(snowfall)
        precip_prob = h("precipitation_probability")
        om = {
            "location": LOCATION_NAME,
            "model": model_name,
            "run_init_utc": run_init_utc,
            "forecast_time": forecast_time,
            "lead_hours": lead_hours,
            "scraped_at": scraped_at,
            "temperature": h("temperature_2m"),
            "dewpoint": h("dew_point_2m"),
            "humidity": h("relative_humidity_2m"),
            "pressure_msl": h("pressure_msl"),
            "rain": total_rain,
            "precipitation": precipitation,
            "showers": showers,
            "snowfall": snowfall,
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
            "precipitation_probability": precip_prob,
        }
        openmeteo_rows.append(om)
        dashboard_rows.append({
            "Location": LOCATION_NAME,
            "Model": model_name,
            "Run_Init_UTC": run_init_utc,
            "Datetime": forecast_time,
            "Scraped_At": scraped_at,
            "Temperature": om["temperature"],
            "Dewpoint": om["dewpoint"],
            "Humidity": om["humidity"],
            "Pressure": om["pressure_msl"],
            "Rain": total_rain,
            "Prob_Precip_0.1": precip_prob,
            "Prob_Precip_1.0": precip_prob,
            "Prob_Precip_10.0": None,
            "Wind": om["wind_speed"],
            "Gust": om["wind_gust"],
            "Wind Dir.": om["wind_dir"],
            "Sunshine": None,
            "Low_Clouds": om["cloud_cover_low"],
            "Mid_Clouds": om["cloud_cover_mid"],
            "High_Clouds": om["cloud_cover_high"],
            "Condition": str(om["weather_code"]) if om["weather_code"] is not None else "",
            "CAPE": om["cape"],
            "Lifted Index": om["lifted_index"],
            "Convective Inhibition": om["convective_inhib"],
            "Weather Code": om["weather_code"],
            "Generation_ms": generation,
        })
    return dashboard_rows, openmeteo_rows


def main() -> tuple[dict, list[dict], list[dict]]:
    all_models_data = {}
    dashboard_rows = []
    openmeteo_rows = []
    for model_name, model_id in MODELS_OPENMETEO.items():
        log.info(f"Fetching Open-Meteo model {model_name} ({model_id})")
        rows, om_rows = fetch_model(model_name, model_id)
        if not rows:
            log.warning(f"No Open-Meteo rows for {model_name}")
            continue
        dashboard_rows.extend(rows)
        openmeteo_rows.extend(om_rows)
        all_models_data[model_name] = rows
    return all_models_data, dashboard_rows, openmeteo_rows


if __name__ == "__main__":
    _, rows, om_rows = main()
    print(f"dashboard_rows={len(rows)} openmeteo_rows={len(om_rows)}")
