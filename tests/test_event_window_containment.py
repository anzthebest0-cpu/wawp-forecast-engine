from src.export_dashboard_data import (
    _event_diagnostics_have_signal,
    _operationalize_event_diagnostics,
)


def _ready_diagnostics():
    return {
        "applied": True,
        "reason": "event skill passed its current gate",
        "event_weights": {"GFS_GLOBAL": 0.6, "ECMWF_HRES": 0.4},
    }


def test_event_diagnostics_default_to_observe_only():
    result = _operationalize_event_diagnostics(_ready_diagnostics(), mode="observe_only")

    assert result["skill_ready"] is True
    assert result["applied"] is False
    assert result["weighting_enabled"] is False
    assert result["mode"] == "observe_only"
    assert "excluded from weights" in result["reason"]


def test_event_diagnostics_require_explicit_enablement():
    result = _operationalize_event_diagnostics(_ready_diagnostics(), mode="enabled")

    assert result["skill_ready"] is True
    assert result["applied"] is True
    assert result["weighting_enabled"] is True


def test_observe_only_diagnostics_are_preservable_signal():
    result = _operationalize_event_diagnostics(_ready_diagnostics(), mode="observe_only")

    assert _event_diagnostics_have_signal({"Rainfall": result}) is True
