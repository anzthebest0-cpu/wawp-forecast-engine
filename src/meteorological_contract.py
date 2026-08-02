"""Versioned meteorological semantics shared by WAWP audit paths.

These constants describe source intervals and verification products. They are
not automatic ICAO/TAF decision thresholds and remain subject to WAWP
meteorological approval.
"""
from __future__ import annotations

import pandas as pd


SEMANTICS_VERSION = "wawp-meteorological-contract-v1-shadow"

AWOS_HOURLY_RAIN_INTERVAL_HOURS = 1
AWOS_HOURLY_RAIN_TIMESTAMP_LABEL = "interval_end"
AWOS_MINUTE_RAIN_SEMANTICS = "rolling_one_hour_accumulation_snapshot"

OPENMETEO_PRECIPITATION_INTERVAL_HOURS = 1
OPENMETEO_PRECIPITATION_TIMESTAMP_LABEL = "interval_end"
OPENMETEO_PRECIPITATION_PROBABILITY_THRESHOLD_MM = 0.1
OPENMETEO_TOTAL_PRECIPITATION_FIELD = "precipitation"
OPENMETEO_RAIN_COMPATIBILITY_ALIAS = "rain stores total precipitation in the WAWP operational table"
OPENMETEO_COMPONENT_POLICY = "rain, showers, and snowfall are components; do not add them to precipitation"

AWOS_OPERATIONAL_WIND_SOURCE = "first WD36/WS36 pair (10-minute aviation wind)"
AWOS_SECONDARY_WIND_SOURCE = "second WD36/WS36 pair is retained outside the operational parser"
AWOS_GUST_TIMESTAMP_LABEL = "maximum over (hour-1h, hour], labelled at interval end"

RAIN_MEASURABLE_MM_H = 0.1
RAIN_EVENT_SHADOW_MM_H = 1.5
RAIN_3H_SHADOW_MM = 1.5
RAIN_REPLAY_THRESHOLDS_MM_H = (0.1, 1.0, 1.5, 5.0, 10.0, 20.0)
GUST_EVENT_SHADOW_KT = 15.0


def interval_end_block_labels(index: pd.DatetimeIndex, block_hours: int) -> pd.DatetimeIndex:
    """Return fixed UTC block-end labels for interval-ending hourly samples.

    For a three-hour block, samples labelled 01, 02, and 03 belong to the
    block ending 03. A sample labelled 00 belongs to the block ending 00.
    """
    if block_hours <= 0:
        raise ValueError("block_hours must be positive")
    timestamps = pd.DatetimeIndex(index)
    return (timestamps - pd.Timedelta(nanoseconds=1)).floor(f"{int(block_hours)}h") + pd.Timedelta(
        hours=int(block_hours)
    )
