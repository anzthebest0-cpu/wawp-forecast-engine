import pandas as pd

from src.tafor_generator import _build_taf_text, generate_tafor
from src.vis_cloud_proxy import estimate_visibility, get_weather_phenomenon


def test_cavok_not_emitted_during_light_rain():
    taf = _build_taf_text(
        {"dir": "090", "spd": "05", "gust": "00", "vis": "9999", "wx": "", "cloud": "NSC", "rain_mmh": 0.3, "trends": []},
        pd.Timestamp("2026-07-03 00:00"),
        3,
        "2300",
    )
    assert "CAVOK" not in taf


def test_visibility_boundary_and_rain_wx():
    assert abs(estimate_visibility(0.49, 85, 28, 24, 5, 1013, 0) - estimate_visibility(0.50, 85, 28, 24, 5, 1013, 0)) < 1500
    assert get_weather_phenomenon(0.5, 70, 30, 20, 3000, 14) == "-RA"


def test_generate_tafor_base_group_has_wx_when_vis_low():
    times = pd.date_range("2026-07-03 08:00:00", periods=30, freq="h")
    df = pd.DataFrame({
        "Datetime": times,
        "Temperature": [28.0] * len(times),
        "Dewpoint": [24.0] * len(times),
        "Pressure": [1010.0] * len(times),
        "Humidity": [80.0] * len(times),
        "Rain": [2.0] + [0.0] * (len(times) - 1),
        "Wind": [5.0] * len(times),
        "Wind Gust": [8.0] * len(times),
        "Wind Dir.": [90.0] * len(times),
        "Prob Precip 1.0mm": [0.0] * len(times),
        "Low Clouds": [60.0] * len(times),
        "Mid Clouds": [0.0] * len(times),
        "Condition": ["Rain"] + ["Normal"] * (len(times) - 1),
    })
    models = ["ECMWF_HRES", "GFS_GLOBAL", "ICON_GLOBAL", "UKMO_GLOBAL_10KM", "GEM_GLOBAL"]
    model_data = {"Rainfall": {m: pd.Series([2.0] + [0.0] * 29) for m in models}}
    qm_rain = {m: {i: (2.0 if i == 0 else 0.0) for i in range(30)} for m in models}
    weights = {"Rainfall": {m: 1 / len(models) for m in models}}
    taf = generate_tafor(df, model_data, qm_rain, weights, target_issuance="2300")
    assert taf["base_group"]["wx"] in {"-RA", "RA", "+RA", "TSRA"}


def _event_diag(param, models):
    return {
        param: {
            "applied": True,
            "reason": "event-window skill blended into event-sensitive weights",
            "event_weights": {m: 1 / len(models) for m in models},
            "model_scores": {
                m: {
                    "eligible": True,
                    "score": 0.45,
                    "observed_events": 30,
                    "forecast_events": 28,
                    "pm2h_far": 0.18,
                }
                for m in models
            },
            "min_events": 10,
            "threshold": 1.5 if param == "Rainfall" else 15.0,
        }
    }


def _base_consensus_frame(rain_values=None, gust_values=None):
    times = pd.date_range("2026-07-03 08:00:00", periods=30, freq="h")
    rain_values = rain_values or [0.0] * len(times)
    gust_values = gust_values or [8.0] * len(times)
    return pd.DataFrame({
        "Datetime": times,
        "Temperature": [28.0] * len(times),
        "Dewpoint": [24.0] * len(times),
        "Pressure": [1010.0] * len(times),
        "Humidity": [82.0] * len(times),
        "Rain": rain_values,
        "Wind": [5.0] * len(times),
        "Wind Gust": gust_values,
        "Wind Dir.": [90.0] * len(times),
        "Prob Precip 1.0mm": [0.0] * len(times),
        "Low Clouds": [40.0] * len(times),
        "Mid Clouds": [20.0] * len(times),
        "Condition": ["Normal"] * len(times),
    })


def test_event_skill_can_promote_marginal_rain_taf_group():
    models = ["ECMWF_HRES", "GFS_GLOBAL"]
    rain_values = [0.0] * 30
    rain_values[3:5] = [0.86, 0.88]
    df = _base_consensus_frame(rain_values=rain_values)
    model_data = {"Rainfall": {m: pd.Series(rain_values) for m in models}}
    qm_rain = {m: {i: rain_values[i] for i in range(30)} for m in models}
    weights = {"Rainfall": {m: 1 / len(models) for m in models}}

    plain = generate_tafor(df, model_data, qm_rain, weights, target_issuance="2300")
    event_aware = generate_tafor(
        df,
        model_data,
        qm_rain,
        weights,
        target_issuance="2300",
        event_weight_diagnostics=_event_diag("Rainfall", models),
    )

    assert "RA" not in plain["taf_text"]
    assert "RA" in event_aware["taf_text"]
    assert event_aware["event_skill_context"]["Rainfall"]["applied"] is True


def test_event_skill_allows_gust_only_taf_group():
    models = ["ECMWF_HRES", "GFS_GLOBAL"]
    gust_values = [8.0] * 30
    gust_values[3:5] = [18.0, 19.0]
    df = _base_consensus_frame(gust_values=gust_values)
    model_data = {"Rainfall": {m: pd.Series([0.0] * 30) for m in models}}
    qm_rain = {m: {i: 0.0 for i in range(30)} for m in models}
    weights = {"Rainfall": {m: 1 / len(models) for m in models}}

    plain = generate_tafor(df, model_data, qm_rain, weights, target_issuance="2300")
    event_aware = generate_tafor(
        df,
        model_data,
        qm_rain,
        weights,
        target_issuance="2300",
        event_weight_diagnostics=_event_diag("Wind Gust", models),
    )

    assert "G18KT" not in plain["taf_text"]
    assert "G18KT" in event_aware["taf_text"] or "G19KT" in event_aware["taf_text"]
    assert event_aware["event_skill_context"]["Wind Gust"]["applied"] is True
