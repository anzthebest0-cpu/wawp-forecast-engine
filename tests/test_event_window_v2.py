import pandas as pd

from src.event_window_verification import event_window_metrics, event_window_weight_scores


def _frame(obs, forecast, start="2026-07-06 00:00:00"):
    times = pd.date_range(start, periods=len(obs), freq="h")
    return pd.DataFrame({
        "Datetime": times,
        "Model": ["TEST_MODEL"] * len(times),
        "obs": obs,
        "forecast": forecast,
    })


def test_single_wet_hour_remains_one_episode_and_one_block_event():
    metrics = event_window_metrics(
        _frame([0.0, 2.0, 0.0], [0.0, 0.0, 2.0], start="2026-07-06 01:00:00"),
        threshold=1.5,
        block_mode="sum",
        block_threshold=1.5,
    )["TEST_MODEL"]

    assert metrics["pm0h"]["observed_events"] == 1
    assert metrics["pm1h"]["observed_events"] == 1
    assert metrics["pm2h"]["observed_events"] == 1
    assert metrics["3h_block"]["observed_events"] == 1
    assert metrics["3h_block"]["verification_unit"] == "complete_3h_sum"


def test_one_forecast_episode_cannot_match_two_observed_episodes():
    metrics = event_window_metrics(
        _frame([2.0, 0.0, 2.0, 0.0], [0.0, 2.0, 0.0, 0.0]),
        threshold=1.5,
    )["TEST_MODEL"]["pm1h"]

    assert metrics["observed_events"] == 2
    assert metrics["forecast_events"] == 1
    assert metrics["hits"] == 1
    assert metrics["misses"] == 1
    assert metrics["false_alarms"] == 0
    assert metrics["HSS"] is None


def test_missing_hour_breaks_episode_and_excludes_incomplete_block():
    frame = _frame([2.0, 2.0, 2.0, 0.0], [2.0, 2.0, 2.0, 0.0])
    frame = frame.drop(index=1)
    metrics = event_window_metrics(frame, threshold=1.5)["TEST_MODEL"]

    assert metrics["pm1h"]["observed_events"] == 2
    assert metrics["3h_block"]["sample_size"] == 0


def test_three_hour_sum_can_cross_accumulation_threshold_without_hourly_event():
    metrics = event_window_metrics(
        _frame([0.8, 0.8, 0.0], [0.7, 0.9, 0.0], start="2026-07-06 01:00:00"),
        threshold=1.5,
        block_mode="sum",
        block_threshold=1.5,
    )["TEST_MODEL"]

    assert metrics["pm0h"]["observed_events"] == 0
    assert metrics["3h_block"]["observed_events"] == 1
    assert metrics["3h_block"]["hits"] == 1


def test_gust_block_uses_peak_not_sum():
    metrics = event_window_metrics(
        _frame([8.0, 16.0, 9.0], [8.0, 17.0, 9.0], start="2026-07-06 01:00:00"),
        threshold=15.0,
        block_mode="max",
        block_threshold=15.0,
    )["TEST_MODEL"]["3h_block"]

    assert metrics["aggregation"] == "max"
    assert metrics["timestamp_label"] == "interval_end"
    assert metrics["observed_events"] == 1
    assert metrics["hits"] == 1


def test_weight_eligibility_counts_episode_not_consecutive_wet_hours():
    metrics = event_window_metrics(
        _frame([2.0, 2.0, 2.0], [2.0, 2.0, 2.0]),
        threshold=1.5,
    )
    scores = event_window_weight_scores(
        metrics,
        parameter="Rainfall",
        models=["TEST_MODEL"],
        min_events=2,
    )

    assert scores["model_scores"]["TEST_MODEL"]["observed_events"] == 1
    assert scores["model_scores"]["TEST_MODEL"]["eligible"] is False
