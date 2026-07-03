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
