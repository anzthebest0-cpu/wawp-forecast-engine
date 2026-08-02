from datetime import datetime, timezone

from src.taf_mahringer_verification import (
    _binary_metrics,
    _significant_weather_class,
    _taf_syntax_issues,
    parse_observation,
)
from src.taf_native_verification import parse_native_taf


UTC = timezone.utc


def _row(text: str) -> dict[str, str]:
    return {
        "period": "2026-01",
        "observed_at_utc": "2026-01-01T00:00:00Z",
        "report_type": "METAR",
        "is_correction": "false",
        "source_sheet": "Sheet1",
        "source_row": "2",
        "source_column": "1",
        "metar_text": text,
    }


def test_weather_classes_keep_light_rain_out_of_mahringer_class_two():
    assert _significant_weather_class("-RA") == 0
    assert _significant_weather_class("RA") == 2
    assert _significant_weather_class("+SHRA") == 2
    assert _significant_weather_class("TSRA") == 6


def test_missing_auto_weather_is_unknown_not_dry():
    parsed = parse_observation(_row("METAR WAWP 010000Z AUTO 20002KT //// ////// 28/25 Q1008="))
    assert parsed["observed_at"] == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert parsed["weather_usable"] is False
    assert parsed["weather_class"] is None
    assert parsed["any_rain"] is None


def test_explicit_rain_remains_usable_and_trend_weather_is_not_observed():
    parsed = parse_observation(
        _row("METAR WAWP 010000Z 20002KT 5000 RA BKN018 27/25 Q1008 BECMG 2000 TSRA=")
    )
    assert parsed["weather_usable"] is True
    assert parsed["weather_class"] == 2
    assert parsed["any_rain"] is True
    assert parsed["thunderstorm"] is False


def test_binary_metrics_use_standard_pod_far_and_csi():
    rows = [
        {"forecast": True, "observed": True},
        {"forecast": False, "observed": True},
        {"forecast": True, "observed": False},
        {"forecast": False, "observed": False},
    ]
    result = _binary_metrics(rows, "forecast", "observed")
    assert result["hits"] == 1
    assert result["misses"] == 1
    assert result["false_alarms"] == 1
    assert result["POD"] == 0.5
    assert result["FAR"] == 0.5
    assert result["CSI"] == 0.3333


def test_taf_syntax_gate_rejects_groups_outside_validity():
    taf = parse_native_taf(
        "TAF WAWP 140500Z 1406/1506 27008KT 9000 SCT019 "
        "BECMG 1309/1311 10003KT=",
        "2026-04",
    )

    assert "becmg_outside_validity" in _taf_syntax_issues(taf)
