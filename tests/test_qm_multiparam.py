import sqlite3

import numpy as np

from src.quantile_mapper import (
    _fit_qm_circular,
    _fit_qm_gamma,
    _fit_qm_linear,
    _fit_qm_nonneg,
    _fit_qm_zero_inflated,
    _enabled_by_validation,
    _qm_is_runtime_safe,
    apply_qm_value,
    fit_multiparam_qm_to_db,
)


def test_fit_qm_linear_bias_correction():
    fcst = np.array([1, 2, 3, 4, 5], dtype=float)
    obs = fcst + 2
    qm = _fit_qm_linear(fcst, obs)
    assert qm
    assert apply_qm_value(3, "temperature", qm) == 5


def test_fit_qm_nonneg_clamps_negative():
    qm = _fit_qm_nonneg(np.array([-1, 0, 1, 2]), np.array([0, 0, 2, 4]))
    assert qm
    assert apply_qm_value(-2, "wind_speed", qm) >= 0


def test_fit_qm_circular_wraps_direction():
    fcst = np.array([350, 355, 0, 5, 10], dtype=float)
    obs = np.array([10, 15, 20, 25, 30], dtype=float)
    qm = _fit_qm_circular(fcst, obs)
    assert qm
    assert qm["method"] == "circular_offset"
    corrected = apply_qm_value(350, "wind_dir", qm)
    assert 0 <= corrected < 360


def test_fit_qm_zero_inflated_preserves_dry():
    qm = _fit_qm_zero_inflated(np.array([0, 0.05, 1, 2, 3]), np.array([0, 0, 2, 4, 6]))
    assert qm
    assert apply_qm_value(0.05, "rain", qm) <= 0.1


def test_fit_qm_gamma_low_confidence_flag():
    fcst = np.linspace(1, 10, 60)
    obs = fcst * 1.5
    qm = _fit_qm_gamma(fcst, obs)
    assert qm
    assert qm["method"] in {"gamma_parametric", "nonneg_gamma_unavailable"}
    assert qm["low_confidence"] is True


def test_gust_qm_requires_positive_skill_and_improved_bias():
    harmful = {
        "mae_before": 5.0,
        "mae_after": 8.0,
        "bias_before": 4.0,
        "bias_after": 1.0,
    }
    useful = {
        "mae_before": 5.0,
        "mae_after": 4.0,
        "bias_before": 3.0,
        "bias_after": 1.0,
    }

    assert _enabled_by_validation("wind_gust", harmful) is False
    assert _enabled_by_validation("wind_gust", useful) is True


def test_low_confidence_gust_qm_is_blocked_at_runtime():
    assert _qm_is_runtime_safe(
        "wind_gust",
        {"low_confidence": True, "skill_score": 0.4},
    ) is False
    assert _qm_is_runtime_safe(
        "wind_gust",
        {"low_confidence": False, "skill_score": -0.1},
    ) is False
    assert _qm_is_runtime_safe(
        "wind_gust",
        {"low_confidence": False, "skill_score": 0.1},
    ) is True


def test_fit_multiparam_qm_to_db_synthetic():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE qm_training_pairs (
            model TEXT, lead_bucket TEXT, lead_bucket_gust TEXT,
            fcst_temperature REAL, obs_temperature REAL,
            fcst_dewpoint REAL, obs_dewpoint REAL,
            fcst_pressure REAL, obs_pressure REAL,
            fcst_wind_speed REAL, obs_wind_speed REAL,
            fcst_wind_gust REAL, obs_wind_gust REAL,
            fcst_wind_dir REAL, obs_wind_dir REAL,
            fcst_rain REAL, obs_rain REAL
        )
    """)
    rows = []
    for i in range(120):
        rows.append((
            "ECMWF_HRES", "L1_0_6h", "L1_0_6h",
            25 + i * 0.01, 26 + i * 0.01,
            23, 24, 1010, 1011, 4, 5, 8, 10, 90, 100, 1, 2,
        ))
    conn.executemany("INSERT INTO qm_training_pairs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    trained = fit_multiparam_qm_to_db(conn, log_fn=lambda *_: None)
    assert trained["ECMWF_HRES"]["temperature"]["L1_0_6h_operational_residual"]["enabled"] is True
