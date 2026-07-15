from src.taf_replay_june_holdout import _all_hour_accuracy, _weather_has_rain, _weather_has_thunderstorm


def test_text_weather_rain_and_thunderstorm_detection_is_explicit():
    assert _weather_has_rain("METAR WAWP 010000Z 9999 TSRA SCT020=")
    assert _weather_has_rain("METAR WAWP 010000Z 9999 -RA SCT020=")
    assert _weather_has_rain("METAR WAWP 010000Z 9999 +RA SCT020=")
    assert not _weather_has_rain("METAR WAWP 010000Z 9999 SCT020=")
    assert _weather_has_thunderstorm("METAR WAWP 010000Z 9999 TSRA SCT020=")
    assert not _weather_has_thunderstorm("METAR WAWP 010000Z 9999 RA SCT020=")


def test_all_hour_accuracy_counts_dry_agreement_separately_from_event_matching():
    rows = [
        {"forecast_events": {"rain_any": False}, "observed_events": {"rain_any": False}},
        {"forecast_events": {"rain_any": True}, "observed_events": {"rain_any": False}},
        {"forecast_events": {"rain_any": True}, "observed_events": {"rain_any": True}},
        {"forecast_events": {"rain_any": False}, "observed_events": {"rain_any": True}},
    ]

    score = _all_hour_accuracy(rows, "rain_any")

    assert score["hourly_correct_negatives"] == 1
    assert score["hourly_hits"] == 1
    assert score["hourly_false_alarms"] == 1
    assert score["hourly_misses"] == 1
    assert score["all_hour_accuracy"] == 0.5
