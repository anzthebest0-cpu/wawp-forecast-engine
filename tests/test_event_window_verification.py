import pandas as pd

from src.event_window_verification import event_window_metrics


def test_rain_event_window_recovers_one_hour_displacement():
    times = pd.date_range("2026-07-06 00:00:00", periods=5, freq="h")
    df = pd.DataFrame({
        "Datetime": times,
        "Model": ["GFS_GLOBAL"] * len(times),
        "obs": [0.0, 2.0, 0.0, 0.0, 0.0],
        "forecast": [0.0, 0.0, 2.0, 0.0, 0.0],
    })

    metrics = event_window_metrics(df, threshold=1.5, windows=(0, 1, 2), block_hours=3)
    strict = metrics["GFS_GLOBAL"]["pm0h"]
    plus_one = metrics["GFS_GLOBAL"]["pm1h"]

    assert strict["hits"] == 0
    assert strict["misses"] == 1
    assert strict["false_alarms"] == 1
    assert plus_one["hits"] == 1
    assert plus_one["misses"] == 0
    assert plus_one["false_alarms"] == 0
    assert plus_one["mean_abs_timing_error_h"] == 1.0


def test_gust_event_window_recovers_two_hour_peak_displacement():
    times = pd.date_range("2026-07-06 00:00:00", periods=7, freq="h")
    df = pd.DataFrame({
        "Datetime": times,
        "Model": ["ECMWF_HRES"] * len(times),
        "obs": [8.0, 9.0, 18.0, 9.0, 8.0, 8.0, 8.0],
        "forecast": [8.0, 8.0, 8.0, 8.0, 19.0, 8.0, 8.0],
    })

    metrics = event_window_metrics(df, threshold=15.0, windows=(0, 1, 2), block_hours=3)
    strict = metrics["ECMWF_HRES"]["pm0h"]
    plus_two = metrics["ECMWF_HRES"]["pm2h"]
    peak_error = metrics["ECMWF_HRES"]["amount_or_peak_error"]

    assert strict["hits"] == 0
    assert plus_two["hits"] == 1
    assert plus_two["mean_abs_timing_error_h"] == 2.0
    assert peak_error["MAE"] == 1.0
