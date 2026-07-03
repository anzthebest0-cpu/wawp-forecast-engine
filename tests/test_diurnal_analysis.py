import pandas as pd

from src.diurnal_analysis import (
    compute_hourly_climatology,
    compute_rain_diurnal_cycle,
    identify_peak_convective_window,
    identify_sea_breeze,
)


def _df():
    times = pd.date_range("2026-01-01", periods=240, freq="h")
    return pd.DataFrame({
        "datetime_utc": times,
        "datetime_wita": times + pd.Timedelta(hours=8),
        "hour_wita": (times + pd.Timedelta(hours=8)).hour,
        "month": (times + pd.Timedelta(hours=8)).month,
        "season": "wet",
        "temperature": 25 + ((times + pd.Timedelta(hours=8)).hour / 24),
        "rain_1h": [2.0 if 12 <= h <= 17 else 0.0 for h in (times + pd.Timedelta(hours=8)).hour],
        "wind_gust_max": [15.0 if 12 <= h <= 17 else None for h in (times + pd.Timedelta(hours=8)).hour],
        "wind_dir": [150.0 if 10 <= h <= 16 else 330.0 for h in (times + pd.Timedelta(hours=8)).hour],
        "wind_speed": [8.0 if 10 <= h <= 16 else 3.0 for h in (times + pd.Timedelta(hours=8)).hour],
    })


def test_compute_hourly_climatology():
    clim = compute_hourly_climatology(_df(), "temperature")
    assert clim["stats"]["0"]["n"] >= 10


def test_compute_rain_diurnal_cycle():
    rain = compute_rain_diurnal_cycle(_df())
    assert rain["frequency_pct"][12] > rain["frequency_pct"][0]


def test_identify_sea_breeze():
    regime = identify_sea_breeze(_df())
    assert regime["direction_difference_deg"] > 30


def test_identify_peak_convective_window():
    window = identify_peak_convective_window(_df())
    assert 12 in window["peak_window_hours_wita"]
