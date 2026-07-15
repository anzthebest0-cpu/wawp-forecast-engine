from src.taf_replay_gate_sweep import POLICIES, _policy_config
from src.vis_cloud_proxy import detect_tsra, get_weather_phenomenon


def test_gate_policy_config_keeps_control_defaults_and_can_disable_bridge():
    control = _policy_config(next(policy for policy in POLICIES if policy.name == "control_current"))
    strict = _policy_config(next(policy for policy in POLICIES if policy.name == "rain_strict"))

    assert control.WET_MODEL_PROBABILITY_THR == 40.0
    assert control.MAX_BRIDGE_GAP == 2
    assert control.MIN_RAIN_SIGNAL_HOURS == 1
    assert strict.WET_MODEL_PROBABILITY_THR > 100.0
    assert strict.MIN_RAIN_SIGNAL_AGREEMENT == 0.15
    assert strict.MAX_BRIDGE_GAP == 0

    becmg_guard = _policy_config(next(policy for policy in POLICIES if policy.name == "rain_moderate_becmg_guard"))
    assert becmg_guard.BECMG_MIN_RAIN_HOURS == 2
    assert becmg_guard.BECMG_MIN_AGREEMENT == 0.20


def test_ts_policy_preserves_direct_weather_code_and_tightens_environmental_proxy():
    direct = detect_tsra(weather_code=95, rain_mmh=0.2, ts_policy="direct_weather_code")
    broad = detect_tsra(cape=700, lifted_index=-3, cin=80, rain_mmh=1.0, local_hour_wita=16)
    strict = detect_tsra(cape=700, lifted_index=-3, cin=80, rain_mmh=1.0, local_hour_wita=16, ts_policy="strict_environmental")

    assert direct == "TSRA"
    assert broad == "TSRA"
    assert strict is None


def test_restrictive_ts_policy_downgrades_environmental_ts_to_rain_not_nothing():
    weather = get_weather_phenomenon(
        rain_mmh=1.0,
        rh_pct=85,
        temp_c=29,
        dewpoint_c=25,
        vis_m=9999,
        local_hour_wita=16,
        cape=700,
        lifted_index=-3,
        cin=80,
        month=1,
        ts_policy="direct_weather_code",
    )

    assert weather == "RA"
