from datetime import datetime, timezone

import pandas as pd

from src.taf_replay_event_ledger import (
    _causal_source_time,
    _match_forecasts,
    _rain_trigger_hours,
    _ts_proxy_reason,
)


UTC = timezone.utc


def _event_row(hour: int, observed: bool, forecast: bool) -> dict:
    return {
        "valid_time": datetime(2026, 1, 1, hour, tzinfo=UTC),
        "observed_events": {"rain_any": observed},
        "forecast_events": {"rain_any": forecast},
    }


def test_rain_trigger_distinguishes_amount_probability_and_bridge():
    consensus = pd.DataFrame({
        "Rainfall": [1.1, 0.3, 1.2, 0.2],
        "Precip Probability": [20.0, 20.0, 45.0, 45.0],
    })

    triggers = _rain_trigger_hours(consensus)

    assert triggers[0]["rain_trigger_reason"] == "consensus_amount"
    assert triggers[1]["rain_trigger_reason"] == "bridged_consensus_gap"
    assert triggers[1]["rain_signal_active"] is True
    assert triggers[2]["rain_trigger_reason"] == "amount_and_probability"
    assert triggers[3]["rain_trigger_reason"] == "wet_model_probability"


def test_matching_keeps_one_forecast_for_one_observation_only():
    rows = [_event_row(0, True, False), _event_row(1, True, True), _event_row(2, False, False)]

    matches = _match_forecasts(rows, "rain_any", window_hours=1)

    assert matches == {1: 0}


def test_ts_proxy_labels_weather_code_before_environmental_proxy():
    weather_code = pd.Series({"Rainfall": 1.0, "Weather Code": 95, "Datetime": "2026-01-01 16:00:00"})
    environment = pd.Series({
        "Rainfall": 1.0,
        "Weather Code": 0,
        "CAPE": 900,
        "Lifted Index": -3.0,
        "Convective Inhibition": 20,
        "Datetime": "2026-01-01 16:00:00",
        "Humidity": 80,
    })

    assert _ts_proxy_reason(weather_code) == "weather_code_thunderstorm"
    assert _ts_proxy_reason(environment) == "cape_li_cin_convective_window"


def test_completed_becmg_is_traced_to_its_establishment_hour():
    issuance = datetime(2026, 1, 1, 5, tzinfo=UTC)
    taf = "TAF WAWP 010500Z 0106/0206 00000KT 9999 FEW020 BECMG 0108/0109 RA SCT018="

    source, relation = _causal_source_time(
        taf,
        issuance,
        datetime(2026, 1, 1, 15, tzinfo=UTC),
        "rain_any",
    )

    assert relation == "becmg_establishment"
    assert source == datetime(2026, 1, 1, 9, tzinfo=UTC)
