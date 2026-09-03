import pandas as pd

from src.export_dashboard_data import (
    _aviation_visibility_consensus,
    _coherent_gust_consensus,
    _ts_proxy_components,
    _weighted_weather_code_consensus,
)
from src.tafor_generator import _build_taf_text, generate_tafor
from src.vis_cloud_proxy import build_hourly_vis_cloud, estimate_visibility, get_weather_phenomenon


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


def test_two_visibility_outliers_cannot_create_prevailing_restriction():
    row = pd.Series({"A": 50.0, "B": 100.0, "C": 9999.0, "D": 9999.0, "E": 9999.0, "F": 9999.0, "G": 9999.0, "H": 9999.0})
    weights = {model: 1 / len(row) for model in row.index}
    assert _aviation_visibility_consensus(row, weights) == 9999.0


def test_weather_code_consensus_keeps_a_valid_weighted_category():
    row = pd.Series({"ECMWF_HRES": 95.0, "GFS_GLOBAL": 0.0, "UKMO_GLOBAL_10KM": 0.0})
    weights = {"ECMWF_HRES": 0.6, "GFS_GLOBAL": 0.2, "UKMO_GLOBAL_10KM": 0.2}

    assert _weighted_weather_code_consensus(row, weights) == 95.0


def test_ts_proxy_breakdown_matches_documented_peak_arithmetic():
    row = pd.Series({
        "Datetime": pd.Timestamp("2026-07-15 16:00:00"),
        "Precip Probability": 29.11627817920574,
        "Rain": 0.20931451435283108,
        "CAPE": 1322.8877653867767,
        "Lifted Index": -2.2627206723485394,
        "Convective Inhibition": 1.447970142385186,
        "Weather Code": 30.0,
    })
    components = _ts_proxy_components(row)

    assert components["total"] == 70.19
    assert [item["contribution"] for item in components["components"]] == [10.19, 8.0, 20.0, 10.0, 12.0, 10.0, 0.0]


def test_proxy_alone_cannot_create_extreme_fog_or_low_ceiling():
    vis, cloud = build_hourly_vis_cloud({
        "temp_c": 25.0,
        "dewpoint_c": 25.0,
        "relative_humidity_pct": 100.0,
        "rain": 0.0,
        "spd": 0.4,
        "pressure_hpa": 1011.0,
        "month": 7,
        "model_visibility_m": 9999.0,
        "low_clouds": 95.0,
        "mid_clouds": 0.0,
        "high_clouds": 0.0,
    }, [1011.0, 1011.0, 1011.0])
    assert vis == "9999"
    assert cloud == "OVC010"


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


def test_sub_one_knot_consensus_rounds_to_one_knot_not_calm():
    df = _base_consensus_frame()
    df["Wind"] = 0.8
    models = ["ECMWF_HRES", "GFS_GLOBAL"]
    model_data = {"Rainfall": {model: pd.Series([0.0] * len(df)) for model in models}}
    qm_rain = {model: {index: 0.0 for index in range(len(df))} for model in models}
    weights = {"Rainfall": {model: 0.5 for model in models}}
    taf = generate_tafor(df, model_data, qm_rain, weights, target_issuance="2300")
    assert "09001KT" in taf["taf_text"]


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
        "TAF Gust": gust_values,
        "Gust Support": [0.0] * len(times),
        "Gust Supporting Models": [0] * len(times),
        "Gust Available Models": [8] * len(times),
        "Wind Dir.": [90.0] * len(times),
        "Prob Precip 1.0mm": [0.0] * len(times),
        "Low Clouds": [40.0] * len(times),
        "Mid Clouds": [20.0] * len(times),
        "Condition": ["Normal"] * len(times),
        "Weather Code": [0.0] * len(times),
    })


def _set_gust_support(df, indexes, support=0.75, model_count=4):
    df.loc[indexes, "Gust Support"] = support
    df.loc[indexes, "Gust Supporting Models"] = model_count
    return df


def test_coherent_gust_consensus_uses_paired_model_excess():
    times = pd.date_range("2026-07-03 08:00:00", periods=1, freq="h")
    speeds = {
        "A": pd.Series([5.0], index=times),
        "B": pd.Series([15.0], index=times),
        "C": pd.Series([5.0], index=times),
    }
    gusts = {
        "A": pd.Series([17.0], index=times),
        "B": pd.Series([19.0], index=times),
        "C": pd.Series([16.0], index=times),
    }
    result = _coherent_gust_consensus(
        speeds,
        gusts,
        pd.Series([6.0], index=times),
        {"A": 0.4, "B": 0.2, "C": 0.4},
    ).iloc[0]

    assert round(result["Wind Gust"], 1) == 16.0
    assert round(result["TAF Gust"], 1) == 17.5
    assert result["Gust Support"] == 0.8
    assert result["Gust Supporting Models"] == 2
    assert result["Gust Available Models"] == 3


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
    _set_gust_support(df, [3, 4], support=0.60, model_count=3)
    df.loc[[3, 4], "Weather Code"] = 95.0
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
    assert "TEMPO" in event_aware["taf_text"]
    assert "G18KT" in event_aware["taf_text"] or "G19KT" in event_aware["taf_text"]
    assert event_aware["event_skill_context"]["Wind Gust"]["applied"] is True


def test_unverified_prevailing_gust_is_withheld_from_base_group():
    models = ["ECMWF_HRES", "GFS_GLOBAL", "UKMO_GLOBAL_10KM"]
    df = _base_consensus_frame(gust_values=[20.0] * 30)
    _set_gust_support(df, df.index, support=0.60, model_count=3)
    model_data = {"Rainfall": {model: pd.Series([0.0] * len(df)) for model in models}}
    qm_rain = {model: {index: 0.0 for index in range(len(df))} for model in models}
    weights = {"Rainfall": {model: 1 / len(models) for model in models}}

    taf = generate_tafor(df, model_data, qm_rain, weights, target_issuance="2300")

    assert "G20KT" not in taf["taf_text"]
    assert taf["gust_guidance"]["base_group_active"] is False
    assert any("withheld" in warning for warning in taf["warnings"])


def test_strong_unverified_gust_uses_degraded_raw_mode():
    models = ["ECMWF_HRES", "GFS_GLOBAL", "GEM_GLOBAL", "UKMO_GLOBAL_10KM"]
    df = _base_consensus_frame(gust_values=[20.0] * 30)
    _set_gust_support(df, df.index, support=0.75, model_count=4)
    model_data = {"Rainfall": {model: pd.Series([0.0] * len(df)) for model in models}}
    qm_rain = {model: {index: 0.0 for index in range(len(df))} for model in models}
    weights = {"Rainfall": {model: 1 / len(models) for model in models}}

    taf = generate_tafor(df, model_data, qm_rain, weights, target_issuance="2300")

    assert "09005G20KT" in taf["taf_text"]
    assert taf["gust_guidance"]["mode"] == "degraded_raw"
    assert any("degraded raw-only mode" in warning for warning in taf["warnings"])


def test_verified_prevailing_gust_is_encoded_in_base_group():
    models = ["ECMWF_HRES", "GFS_GLOBAL", "UKMO_GLOBAL_10KM"]
    df = _base_consensus_frame(gust_values=[20.0] * 30)
    _set_gust_support(df, df.index)
    model_data = {"Rainfall": {model: pd.Series([0.0] * len(df)) for model in models}}
    qm_rain = {model: {index: 0.0 for index in range(len(df))} for model in models}
    weights = {"Rainfall": {model: 1 / len(models) for model in models}}

    taf = generate_tafor(
        df,
        model_data,
        qm_rain,
        weights,
        target_issuance="2300",
        event_weight_diagnostics=_event_diag("Wind Gust", models),
    )

    assert "09005G20KT" in taf["taf_text"]
    assert taf["gust_guidance"]["base_group_active"] is True


def test_verified_persistent_gust_onset_uses_becmg():
    models = ["ECMWF_HRES", "GFS_GLOBAL", "UKMO_GLOBAL_10KM"]
    gusts = [20.0] * 3 + [28.0] * 27
    df = _base_consensus_frame(gust_values=gusts)
    df["Wind"] = 16.0
    _set_gust_support(df, range(3, len(df)))
    model_data = {"Rainfall": {model: pd.Series([0.0] * len(df)) for model in models}}
    qm_rain = {model: {index: 0.0 for index in range(len(df))} for model in models}
    weights = {"Rainfall": {model: 1 / len(models) for model in models}}

    taf = generate_tafor(
        df,
        model_data,
        qm_rain,
        weights,
        target_issuance="2300",
        event_weight_diagnostics=_event_diag("Wind Gust", models),
    )

    assert "BECMG" in taf["taf_text"]
    assert "09016G28KT" in taf["taf_text"]


def test_verified_gust_cessation_removes_gust_with_becmg():
    models = ["ECMWF_HRES", "GFS_GLOBAL", "UKMO_GLOBAL_10KM"]
    gusts = [28.0] * 8 + [20.0] * 22
    df = _base_consensus_frame(gust_values=gusts)
    df["Wind"] = 16.0
    _set_gust_support(df, range(8))
    model_data = {"Rainfall": {model: pd.Series([0.0] * len(df)) for model in models}}
    qm_rain = {model: {index: 0.0 for index in range(len(df))} for model in models}
    weights = {"Rainfall": {model: 1 / len(models) for model in models}}

    taf = generate_tafor(
        df,
        model_data,
        qm_rain,
        weights,
        target_issuance="2300",
        event_weight_diagnostics=_event_diag("Wind Gust", models),
    )

    assert "09016G28KT" in taf["taf_text"]
    assert any(
        trend["type"] == "BECMG" and trend["gust"] == ""
        for trend in taf["base_group"]["trends"]
    )
