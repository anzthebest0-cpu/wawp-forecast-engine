import pandas as pd

from src.event_window_verification import event_window_metrics, event_window_weight_scores


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


def _window_metric(hss, csi, far, events=30, forecast_events=28):
    return {
        "sample_size": 300,
        "observed_events": events,
        "forecast_events": forecast_events,
        "hits": int(events * csi),
        "misses": max(0, events - int(events * csi)),
        "false_alarms": int(forecast_events * far),
        "POD": csi,
        "FAR": far,
        "CSI": csi,
        "HSS": hss,
    }


def _metric_bundle(hss, csi, far, events=30):
    return {
        "pm0h": _window_metric(hss * 0.70, csi * 0.70, min(1.0, far + 0.05), events),
        "pm1h": _window_metric(hss * 0.90, csi * 0.90, far, events),
        "pm2h": _window_metric(hss, csi, far, events),
        "3h_block": _window_metric(hss * 0.85, csi * 0.85, far, events),
        "amount_or_peak_error": {"MAE": 2.0, "Bias": 0.0, "event_count": events},
    }


def test_event_weight_scores_require_two_positive_models():
    metrics = {
        "GFS_GLOBAL": _metric_bundle(0.55, 0.45, 0.15, events=30),
        "ICON_SEAMLESS": _metric_bundle(0.0, 0.0, 0.90, events=30),
    }

    scores = event_window_weight_scores(metrics, "Rainfall", ["GFS_GLOBAL", "ICON_SEAMLESS"], min_events=10)

    assert scores["applied"] is False
    assert scores["event_weights"] == {}


def test_rain_event_weight_scores_rank_better_event_model_higher():
    metrics = {
        "GFS_GLOBAL": _metric_bundle(0.60, 0.50, 0.12, events=30),
        "ICON_SEAMLESS": _metric_bundle(0.25, 0.25, 0.30, events=30),
        "GEM_GLOBAL": _metric_bundle(0.50, 0.40, 0.20, events=5),
    }

    scores = event_window_weight_scores(
        metrics,
        "Rainfall",
        ["GFS_GLOBAL", "ICON_SEAMLESS", "GEM_GLOBAL"],
        min_events=10,
    )

    assert scores["applied"] is True
    assert scores["event_weights"]["GFS_GLOBAL"] > scores["event_weights"]["ICON_SEAMLESS"]
    assert scores["event_weights"]["GEM_GLOBAL"] == 0.0
    assert round(sum(scores["event_weights"].values()), 6) == 1.0


def test_gust_event_weight_scores_use_peak_error_and_timing_skill():
    metrics = {
        "ECMWF_HRES": _metric_bundle(0.35, 0.35, 0.20, events=24),
        "GEM_GLOBAL": _metric_bundle(0.25, 0.25, 0.25, events=24),
    }
    metrics["ECMWF_HRES"]["amount_or_peak_error"]["MAE"] = 1.0
    metrics["GEM_GLOBAL"]["amount_or_peak_error"]["MAE"] = 8.0

    scores = event_window_weight_scores(metrics, "Wind Gust", ["ECMWF_HRES", "GEM_GLOBAL"], min_events=10)

    assert scores["applied"] is True
    assert scores["event_weights"]["ECMWF_HRES"] > scores["event_weights"]["GEM_GLOBAL"]
