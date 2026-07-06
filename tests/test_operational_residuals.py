import pandas as pd

from src.operational_residuals import (
    circular_diff_deg,
    lead_bucket,
    summarize_parameter_pairs,
)


def _pairs(parameter, n=120, residual=2.0, lead=3.0):
    times = pd.date_range("2026-07-01 00:00:00", periods=n, freq="h")
    forecast = [20.0] * n
    obs = [20.0 + residual] * n
    return pd.DataFrame({
        "Datetime": times,
        "Model": ["GFS_GLOBAL"] * n,
        "Run_Init_UTC": ["2026-06-30 21:00:00"] * n,
        "Lead_Hour": [lead] * n,
        "forecast": forecast,
        "obs": obs,
    })


def test_lead_bucket_classification():
    assert lead_bucket(0) == "L1_0_6h"
    assert lead_bucket(6) == "L2_6_12h"
    assert lead_bucket(12) == "L3_12_24h"
    assert lead_bucket(24) == "L4_24_48h"
    assert lead_bucket(48) == "L5_48plus"
    assert lead_bucket(-1) is None


def test_median_residual_ready_observe_only_when_holdout_improves():
    df = _pairs("Temperature", n=120, residual=2.0, lead=4.0)
    result = summarize_parameter_pairs(df, "Temperature")
    row = result["rows"][0]

    assert row["lead_bucket"] == "L1_0_6h"
    assert row["sample_count"] == 120
    assert row["enabled"] is False
    assert row["promotion_status"] == "ready_observe_only"
    assert row["median_error"] == 2.0
    assert row["mae_after_if_median_correction_used"] == 0.0
    assert row["skill_score"] == 1.0


def test_harmful_holdout_residual_is_disabled_observe_only():
    df = _pairs("Temperature", n=120, residual=5.0, lead=4.0)
    df.loc[84:, "obs"] = 15.0
    result = summarize_parameter_pairs(df, "Temperature")
    row = result["rows"][0]

    assert row["promotion_status"] == "disabled_observe_only"
    assert row["enabled"] is False
    assert row["skill_score"] < 0


def test_wind_direction_uses_circular_residual():
    df = _pairs("Wind Dir.", n=120, residual=0.0, lead=14.0)
    df["forecast"] = 350.0
    df["obs"] = 10.0
    result = summarize_parameter_pairs(df, "Wind Dir.")
    row = result["rows"][0]

    assert row["lead_bucket"] == "L3_12_24h"
    assert circular_diff_deg(pd.Series([10.0]), pd.Series([350.0])).iloc[0] == 20.0
    assert abs(row["median_error"] - 20.0) < 0.001
    assert row["promotion_status"] == "ready_observe_only"


def test_rainfall_tracks_occurrence_and_defers_amount_residual():
    n = 320
    df = _pairs("Rainfall", n=n, residual=0.0, lead=30.0)
    df["forecast"] = 0.0
    df["obs"] = 0.0
    df.loc[:24, "forecast"] = 1.2
    df.loc[:24, "obs"] = 0.6

    result = summarize_parameter_pairs(df, "Rainfall")
    row = result["rows"][0]

    assert row["lead_bucket"] == "L4_24_48h"
    assert row["event_count"] == 25
    assert row["median_error"] is None
    assert row["mae_after_if_median_correction_used"] is None
    assert row["promotion_status"] == "ready_observe_only"
    assert "amount residual deferred" in row["reason"]
    assert row["rainfall_occurrence"]["HSS"] > 0
