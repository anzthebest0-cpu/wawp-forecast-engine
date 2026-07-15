from datetime import datetime, timedelta, timezone

from src.taf_native_verification import (
    _has_complete_half_hour_coverage,
    _interval_match_counts,
    forecast_event_intervals,
    parse_native_taf,
)


UTC = timezone.utc


def test_tempo_interval_is_exact_and_does_not_receive_a_timing_grace():
    taf = parse_native_taf(
        "TAF WAWP 010500Z 0106/0206 12004KT 9999 FEW020 "
        "TEMPO 0112/0114 4000 TSRA BKN018=",
        "2026-06",
    )

    intervals = forecast_event_intervals(taf, "thunderstorm")

    assert intervals == [
        (datetime(2026, 6, 1, 12, tzinfo=UTC), datetime(2026, 6, 1, 14, tzinfo=UTC))
    ]
    counts = _interval_match_counts(intervals, [(datetime(2026, 6, 1, 14, tzinfo=UTC), datetime(2026, 6, 1, 14, 30, tzinfo=UTC))])
    assert counts["hits"] == 0
    assert counts["misses"] == 1
    assert counts["false_alarms"] == 1


def test_becmg_event_is_a_transition_then_persists_as_prevailing_condition():
    taf = parse_native_taf(
        "TAF WAWP 010500Z 0106/0206 12004KT 9999 FEW020 "
        "BECMG 0112/0114 4000 RA BKN018=",
        "2026-06",
    )

    intervals = forecast_event_intervals(taf, "rain")

    assert intervals == [
        (datetime(2026, 6, 1, 12, tzinfo=UTC), datetime(2026, 6, 2, 6, tzinfo=UTC))
    ]


def test_prob_tempo_groups_preserve_probability_and_are_not_primary_deterministic_alerts():
    taf = parse_native_taf(
        "TAF WAWP 010500Z 0106/0206 12004KT 9999 FEW020 "
        "PROB40 TEMPO 0112/0114 4000 RA BKN018=",
        "2026-06",
    )

    assert taf.groups[0].kind == "PROB_TEMPO"
    assert taf.groups[0].probability == 0.4
    assert forecast_event_intervals(taf, "rain") == []
    assert forecast_event_intervals(taf, "rain", include_probability=True) == [
        (datetime(2026, 6, 1, 12, tzinfo=UTC), datetime(2026, 6, 1, 14, tzinfo=UTC))
    ]


def test_complete_coverage_requires_every_half_hour_in_the_taf_validity():
    taf = parse_native_taf("TAF WAWP 010500Z 0106/0206 12004KT 9999 FEW020=", "2026-06")
    observations = [
        {"observed_at_utc": (taf.valid_start + timedelta(minutes=30 * index)).isoformat().replace("+00:00", "Z")}
        for index in range(48)
    ]

    assert _has_complete_half_hour_coverage(taf, observations)
    assert not _has_complete_half_hour_coverage(taf, observations[:-1])
