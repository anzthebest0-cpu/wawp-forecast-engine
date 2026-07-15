from datetime import datetime, timedelta, timezone

from src.legacy_taf_verification import (
    WeatherState,
    _quality_rain_provenance,
    active_state,
    active_state_structured,
    legacy_tempo_adjustment,
    parse_metar,
    parse_taf,
    repair_validity_to_24h,
    score_hour,
)


UTC = timezone.utc


def test_parse_taf_and_becmg_state_transition():
    taf = parse_taf(
        "TAF WAWP 312300Z 0100/0200 29006KT 9000 SCT020 BECMG 0106/0108 11012KT 5000 BKN010=",
        "2026-01",
    )
    before = active_state(taf, datetime(2026, 1, 1, 7, tzinfo=UTC))
    after = active_state(taf, datetime(2026, 1, 1, 8, tzinfo=UTC))
    assert before[0] == "GENERAL"
    assert before[1].wind_direction == 290
    assert after[0] == "BECMG"
    assert after[1].wind_direction == 110
    assert after[1].cloud_base_ft == 1000


def test_tempo_overrides_only_inside_window():
    taf = parse_taf(
        "TAF WAWP 010500Z 0106/0206 29006KT 9999 FEW020 TEMPO 0110/0112 3000 TSRA BKN015=",
        "2026-01",
    )
    outside = active_state(taf, datetime(2026, 1, 1, 9, tzinfo=UTC))
    inside = active_state(taf, datetime(2026, 1, 1, 10, tzinfo=UTC))
    assert outside[0] == "GENERAL"
    assert outside[1].visibility_m == 9999
    assert inside[0] == "TEMPO"
    assert inside[1].visibility_m == 3000
    assert inside[1].weather == "TSRA"


def test_structured_state_keeps_completed_becmg_before_tempo_overlay():
    taf = parse_taf(
        "TAF WAWP 010500Z 0106/0206 29006KT 9999 FEW020 "
        "BECMG 0107/0108 11010KT 8000 SCT018 "
        "TEMPO 0110/0112 3000 TSRA BKN015=",
        "2026-01",
    )
    state = active_state_structured(taf, datetime(2026, 1, 1, 10, tzinfo=UTC))
    assert state[0] == "TEMPO"
    assert state[1].wind_direction == 110
    assert state[1].visibility_m == 3000


def test_legacy_tempo_rule_is_preserved_for_reproducibility():
    assert legacy_tempo_adjustment(0, 4, "rain_occurrence", False) == 1.2
    assert legacy_tempo_adjustment(1, 4, "rain_occurrence", False) == 4.0
    assert legacy_tempo_adjustment(4, 4, "rain_occurrence", False) == 2.4


def test_legacy_visibility_missing_is_explicitly_configurable():
    forecast = WeatherState(290, False, 6, 0, 9000, "0", "SCT", 2000)
    observed = parse_metar("METAR WAWP 010000Z 20002KT //// ////// 28/25 Q1008=")
    legacy = score_hour(forecast, observed, "0", legacy_missing_visibility_high=True)
    quality = score_hour(forecast, observed, "0", legacy_missing_visibility_high=False)
    assert legacy["visibility"] == 1
    assert quality["visibility"] is None


def test_impossible_historical_validity_is_repaired_to_one_day_with_provenance():
    source = parse_taf("TAF WAWP 261700Z 2618/2618 32008KT 9000 BKN020=", "2026-01")
    repaired, changed, reported_hours = repair_validity_to_24h(source)
    assert changed is True
    assert reported_hours == 744
    assert repaired.valid_end - repaired.valid_start == timedelta(hours=24)


def test_stale_rain_date_is_accepted_only_when_its_hour_matches_the_metar():
    eligible, provenance = _quality_rain_provenance({
        "rain_source_timestamp_matches_observed_at": "false",
        "metar_time_group_matches_rain_timestamp": "true",
    })
    assert eligible is True
    assert provenance == "timestamp_normalized_from_row_alignment"
    eligible, provenance = _quality_rain_provenance({
        "rain_source_timestamp_matches_observed_at": "false",
        "metar_time_group_matches_rain_timestamp": "false",
    })
    assert eligible is False
    assert provenance == "not_scored_unaligned_rain_timestamp"
