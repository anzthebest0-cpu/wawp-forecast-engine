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
OPENMETEO_RETRIES = int(os.environ.get("OPENMETEO_RETRIES", "1"))
OPENMETEO_TIMEOUT_S = int(os.environ.get("OPENMETEO_TIMEOUT_S", "20"))
OPENMETEO_BACKOFF_S = int(os.environ.get("OPENMETEO_BACKOFF_S", "10"))
OPENMETEO_429_BACKOFF_S = int(os.environ.get("OPENMETEO_429_BACKOFF_S", "30"))
OPENMETEO_NETWORK_FAILURE_LIMIT = int(os.environ.get("OPENMETEO_NETWORK_FAILURE_LIMIT", "2"))
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
    "ICON_SEAMLESS": "icon_seamless",
    "GEM_GLOBAL": "gem_global",
    "CMA_GRAPES_GLOBAL": "cma_grapes_global",
    "JMA_GSM": "jma_gsm",
    "METEOFRANCE_ARPEGE_WORLD": "meteofrance_arpege_world",
    "UKMO_GLOBAL_10KM": "ukmo_global_deterministic_10km",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("openmeteo")


def _is_network_timeout(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "timed out" in text
        or "timeout" in text
        or "ssl.c" in text
        or "handshake operation" in text
        or "temporary failure" in text
        or "name resolution" in text
    )


def _get_json(url: str, retries: int = OPENMETEO_RETRIES, timeout: int = OPENMETEO_TIMEOUT_S) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "wawp-forecast-engine/1.0 (+https://github.com/anzthebest0-cpu/wawp-forecast-engine)"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 400:
                log.warning(f"Open-Meteo 400 skipped: {url}")
                return {}
            if e.code == 429 and attempt < retries - 1:
                wait_s = min(OPENMETEO_429_BACKOFF_S * (attempt + 1), 90)
                log.warning(f"Open-Meteo 429 rate limit; retrying in {wait_s}s ({attempt + 1}/{retries})")
                time.sleep(wait_s)
                continue
            raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait_s = min(OPENMETEO_BACKOFF_S * (attempt + 1), 30)
            log.warning(f"Open-Meteo request failed ({attempt + 1}/{retries}): {e}; retrying in {wait_s}s")
            time.sleep(wait_s)
    return {}


def fetch_model(model_name: str, model_id: str, forecast_days: int = 16) -> tuple[list[dict], list[dict]]:
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(HOURLY_PARAMS),
        "models": model_id,
        "forecast_hours": forecast_days * 24,
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
    # Open-Meteo's forecast endpoint does not expose a true model initialization
    # timestamp. Use the scrape hour as the operational archive reference so
    # repeated live collections are retained and can later form lead-residual
    # verification pairs.
    run_init_utc = scraped_at[:14] + "00:00"

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
    consecutive_network_failures = 0
    for model_name, model_id in MODELS_OPENMETEO.items():
        log.info(f"Fetching Open-Meteo model {model_name} ({model_id})")
        try:
            rows, om_rows = fetch_model(model_name, model_id)
        except Exception as e:
            log.error(f"Open-Meteo model {model_name} failed after retries: {e}")
            if _is_network_timeout(e):
                consecutive_network_failures += 1
                if consecutive_network_failures >= OPENMETEO_NETWORK_FAILURE_LIMIT:
                    log.error(
                        "Open-Meteo network appears unavailable after "
                        f"{consecutive_network_failures} consecutive timeout/TLS failures; "
                        "aborting remaining model fetches and using archived forecasts."
                    )
                    break
            else:
                consecutive_network_failures = 0
            continue
        if not rows:
            log.warning(f"No Open-Meteo rows for {model_name}")
            continue
        consecutive_network_failures = 0
        dashboard_rows.extend(rows)
        openmeteo_rows.extend(om_rows)
        all_models_data[model_name] = rows
    if not openmeteo_rows:
        raise RuntimeError("All Open-Meteo model fetches failed or returned no rows")
    return all_models_data, dashboard_rows, openmeteo_rows


if __name__ == "__main__":
    _, rows, om_rows = main()
    print(f"dashboard_rows={len(rows)} openmeteo_rows={len(om_rows)}")
