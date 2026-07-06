"""Static model and parameter provenance for Open-Meteo feeds.

The forecast endpoint returns an hourly output grid for the requested variables,
but provider update frequency and native temporal confidence differ by model.
Keep those facts explicit so verification, dashboard labels, and TAF guidance do
not silently treat every hourly timestamp as equally native-hourly information.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelMetadata:
    name: str
    openmeteo_id: str
    provider: str
    expected_output_interval_hours: float
    provider_update_frequency_hours: float
    forecast_horizon_hours: int
    temporal_confidence: str
    hourly_output_note: str


HOURLY_OUTPUT_NOTE = "Open-Meteo hourly output grid; native model step/update frequency may differ."

MODEL_REGISTRY: dict[str, ModelMetadata] = {
    "ECMWF_HRES": ModelMetadata(
        "ECMWF_HRES", "ecmwf_ifs025", "ECMWF IFS HRES", 1.0, 6.0, 384, "medium", HOURLY_OUTPUT_NOTE
    ),
    "GFS_GLOBAL": ModelMetadata(
        "GFS_GLOBAL", "gfs_global", "NOAA GFS", 1.0, 1.0, 384, "high", HOURLY_OUTPUT_NOTE
    ),
    "ICON_SEAMLESS": ModelMetadata(
        "ICON_SEAMLESS", "icon_seamless", "DWD ICON seamless", 1.0, 3.0, 384, "medium", HOURLY_OUTPUT_NOTE
    ),
    "GEM_GLOBAL": ModelMetadata(
        "GEM_GLOBAL", "gem_global", "CMC GEM", 1.0, 6.0, 384, "medium", HOURLY_OUTPUT_NOTE
    ),
    "CMA_GRAPES_GLOBAL": ModelMetadata(
        "CMA_GRAPES_GLOBAL", "cma_grapes_global", "CMA GRAPES", 1.0, 6.0, 384, "medium", HOURLY_OUTPUT_NOTE
    ),
    "JMA_GSM": ModelMetadata(
        "JMA_GSM", "jma_gsm", "JMA GSM", 1.0, 3.0, 384, "medium", HOURLY_OUTPUT_NOTE
    ),
    "METEOFRANCE_ARPEGE_WORLD": ModelMetadata(
        "METEOFRANCE_ARPEGE_WORLD", "meteofrance_arpege_world", "Meteo-France ARPEGE World", 1.0, 1.0, 384, "high", HOURLY_OUTPUT_NOTE
    ),
    "UKMO_GLOBAL_10KM": ModelMetadata(
        "UKMO_GLOBAL_10KM", "ukmo_global_deterministic_10km", "UK Met Office Global 10 km", 1.0, 1.0, 384, "high", HOURLY_OUTPUT_NOTE
    ),
}


PARAMETER_SOURCES: dict[str, dict[str, Any]] = {
    "temperature_2m": {"dashboard": "Temperature", "verification_interval": "hourly"},
    "dew_point_2m": {"dashboard": "Dewpoint", "verification_interval": "hourly"},
    "relative_humidity_2m": {"dashboard": "Humidity", "verification_interval": "hourly"},
    "pressure_msl": {"dashboard": "Pressure", "verification_interval": "hourly"},
    "precipitation_probability": {"dashboard": "Precip Probability", "verification_interval": "event_window"},
    "precipitation": {"dashboard": "Rainfall", "verification_interval": "event_window"},
    "rain": {"dashboard": "Rainfall", "verification_interval": "event_window"},
    "showers": {"dashboard": "Rainfall", "verification_interval": "event_window"},
    "snowfall": {"dashboard": "Rainfall", "verification_interval": "event_window"},
    "wind_speed_10m": {"dashboard": "Wind Speed", "verification_interval": "hourly"},
    "wind_gusts_10m": {"dashboard": "Wind Gust", "verification_interval": "event_window"},
    "wind_direction_10m": {"dashboard": "Wind Dir.", "verification_interval": "hourly_circular"},
    "cloud_cover": {"dashboard": "Cloud Cover", "verification_interval": "proxy"},
    "cloud_cover_low": {"dashboard": "Low Clouds", "verification_interval": "proxy"},
    "cloud_cover_mid": {"dashboard": "Mid Clouds", "verification_interval": "proxy"},
    "cloud_cover_high": {"dashboard": "High Clouds", "verification_interval": "proxy"},
    "weather_code": {"dashboard": "Weather Code", "verification_interval": "categorical_proxy"},
    "visibility": {"dashboard": "Visibility", "verification_interval": "proxy"},
    "cape": {"dashboard": "CAPE", "verification_interval": "convective_proxy"},
    "lifted_index": {"dashboard": "Lifted Index", "verification_interval": "convective_proxy"},
    "convective_inhibition": {"dashboard": "Convective Inhibition", "verification_interval": "convective_proxy"},
    "boundary_layer_height": {"dashboard": "Boundary Layer Height", "verification_interval": "proxy"},
}


def get_model_metadata(model: str) -> ModelMetadata | None:
    return MODEL_REGISTRY.get(model)


def model_metadata_dict(model: str) -> dict[str, Any]:
    meta = get_model_metadata(model)
    if not meta:
        return {
            "name": model,
            "openmeteo_id": None,
            "provider": "unknown",
            "expected_output_interval_hours": 1.0,
            "provider_update_frequency_hours": None,
            "forecast_horizon_hours": None,
            "temporal_confidence": "unknown",
            "hourly_output_note": "Model is not in WAWP registry; audit before trusting cadence.",
        }
    return asdict(meta)


def registry_payload() -> dict[str, Any]:
    return {
        "models": {name: asdict(meta) for name, meta in MODEL_REGISTRY.items()},
        "parameters": PARAMETER_SOURCES,
        "notes": {
            "output_interval": "Interval between timestamps returned to WAWP after Open-Meteo processing.",
            "provider_update_frequency": "Expected cadence at which provider model guidance is refreshed.",
            "event_window_parameters": "Rain and gust verification should use event/timing windows, not strict hourly-only scoring.",
        },
    }


def freshness_status(age_hours: float | None, provider_update_frequency_hours: float | None) -> str:
    if age_hours is None:
        return "unknown"
    cadence = float(provider_update_frequency_hours or 6.0)
    if age_hours <= cadence * 1.25:
        return "fresh"
    if age_hours <= cadence * 2.0:
        return "aging"
    return "stale"
