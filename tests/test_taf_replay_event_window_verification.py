from datetime import datetime, timezone

from src.taf_replay_event_window_verification import (
    _forecast_event,
    _matching_counts,
    _observed_event,
)


UTC = timezone.utc


def _row(hour: int, observed: bool, forecast: bool) -> dict:
    return {
        "valid_time": datetime(2026, 1, 1, hour, tzinfo=UTC),
        "observed_events": {"rain_any": observed},
        "forecast_events": {"rain_any": forecast},
    }


def test_event_match_is_one_to_one_for_nearby_observed_hours():
    rows = [_row(0, True, False), _row(1, True, True), _row(2, False, False)]

    counts, offsets = _matching_counts(rows, "rain_any", window_hours=1)

    assert counts["hits"] == 1
    assert counts["misses"] == 1
    assert counts["false_alarms"] == 0
    assert offsets == [1.0]


def test_rain_and_ts_categories_are_parsed_from_taf_and_metar_values():
    assert _forecast_event("TSRA", "rain_any") is True
    assert _forecast_event("TSRA", "thunderstorm") is True
    assert _forecast_event("BR", "rain_any") is False

    wet = {"rainfall_raw_tenths_mm": "1", "metar_text": "METAR WAWP 010000Z 00000KT 9999 RA="}
    heavy = {"rainfall_raw_tenths_mm": "41", "metar_text": "METAR WAWP 010000Z 00000KT 9999 TSRA="}
    assert _observed_event(wet, "rain_any") is True
    assert _observed_event(wet, "rain_heavy") is False
    assert _observed_event(heavy, "rain_heavy") is True
    assert _observed_event(heavy, "thunderstorm") is True
