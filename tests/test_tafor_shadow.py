from datetime import datetime, timezone
import json

from src.tafor_shadow import SHADOW_TAF_CONFIGS, build_shadow_taf_payload
from src.tafor_generator import _event_aware_config, _overlay_shadow_policy


def test_shadow_config_registry_matches_replay_candidates():
    configs = {config.key: config for config in SHADOW_TAF_CONFIGS}

    assert tuple(configs) == ("control_current", "rain_50_20", "rain_50_20_becmg3")
    assert configs["control_current"].generator_config().MAX_BRIDGE_GAP == 2
    assert configs["rain_50_20"].generator_config().WET_MODEL_PROBABILITY_THR == 50.0
    assert configs["rain_50_20_becmg3"].generator_config().BECMG_MIN_RAIN_HOURS == 3
    assert configs["rain_50_20_becmg3"].generator_config().BECMG_MIN_AGREEMENT == 0.25


def test_shadow_payload_is_explicitly_non_operational_and_covers_each_issuance():
    calls = []

    def fake_generate(*args, **kwargs):
        calls.append(kwargs)
        return {"taf_text": f"TAF {kwargs['target_issuance']}", "warnings": []}

    payload = build_shadow_taf_payload(
        consensus=object(),
        model_data={},
        qm_rain_data={},
        model_weights={},
        generated_at=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
        issuances=("0500",),
        generate_fn=fake_generate,
    )

    assert payload["status"] == "shadow_only"
    assert payload["config_order"] == ["control_current", "rain_50_20", "rain_50_20_becmg3"]
    assert len(calls) == 3
    assert all(call["target_issuance"] == "0500" for call in calls)
    assert all(call["experimental_taf_config"] is not None for call in calls)

    alternatives = payload["issuances"]["0500"]
    assert alternatives["rain_50_20"]["guidance"]["taf_text"] == "TAF 0500"
    assert alternatives["rain_50_20"]["provenance"]["selected_operational_impact"] == "none"
    json.dumps(payload)


def test_shadow_policy_keeps_event_calibration_outside_its_explicit_overrides():
    event_config = _event_aware_config(
        {
            "Rainfall": {"applied": True, "strength": 0.75},
            "Wind Gust": {"applied": True, "strength": 0.50},
        }
    )
    shadow = _overlay_shadow_policy(
        event_config,
        next(config for config in SHADOW_TAF_CONFIGS if config.key == "rain_50_20").generator_config(),
    )

    assert shadow.WET_MODEL_PROBABILITY_THR == 50.0
    assert shadow.MIN_PROBABILITY_AGREEMENT == 0.20
    assert shadow.GUST_EVENT_ENABLED is True
    assert shadow.GUST_TRIGGER_DELTA == event_config.GUST_TRIGGER_DELTA
