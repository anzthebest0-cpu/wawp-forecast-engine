from src.taf_replay_gate_sweep import _policy_config
from src.taf_replay_rain_persistence_sweep import POLICIES


def test_persistence_sweep_uses_50_20_baseline_and_keeps_ts_policy_constant():
    policies = {policy.name: policy for policy in POLICIES}

    baseline = _policy_config(policies["rain_50_20"])
    persistence = _policy_config(policies["rain_50_20_persist2"])
    becmg = _policy_config(policies["rain_50_20_becmg3"])

    assert baseline.WET_MODEL_PROBABILITY_THR == 50.0
    assert baseline.MIN_PROBABILITY_AGREEMENT == 0.20
    assert persistence.MIN_RAIN_SIGNAL_HOURS == 2
    assert becmg.BECMG_MIN_RAIN_HOURS == 3
    assert becmg.BECMG_MIN_AGREEMENT == 0.25
    assert all(policy.ts_policy == "broad_current" for policy in POLICIES)
