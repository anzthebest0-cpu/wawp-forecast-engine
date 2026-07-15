from datetime import datetime, timedelta, timezone

from src.taf_gate_sweep_holdout import _bootstrap_metric_deltas, _contiguous_blocks, _group_event_counts


UTC = timezone.utc


def _row(hour: int, forecast: bool, observed: bool) -> dict:
    return {
        "issuance_key": "control|2026-01-01 05:00:00",
        "valid_time": datetime(2026, 1, 1, hour, tzinfo=UTC),
        "forecast_events": {"rain_any": forecast},
        "observed_events": {"rain_any": observed},
    }


def test_contiguous_blocks_collapse_a_multi_hour_taf_group():
    rows = [_row(0, True, False), _row(1, True, False), _row(2, True, False), _row(3, False, False)]

    blocks = _contiguous_blocks(rows, "rain_any", "forecast")

    assert len(blocks) == 1
    assert blocks[0].start == datetime(2026, 1, 1, 0, tzinfo=UTC)
    assert blocks[0].end == datetime(2026, 1, 1, 2, tzinfo=UTC)


def test_group_matching_allows_timing_displacement_without_multiple_credit():
    rows = [
        _row(0, False, True),
        _row(1, True, True),
        _row(2, True, False),
        _row(3, False, False),
    ]

    counts = _group_event_counts(rows, "rain_any", window_hours=1)

    assert counts["hits"] == 1
    assert counts["misses"] == 0
    assert counts["false_alarms"] == 0


def test_bootstrap_accepts_a_custom_candidate_list():
    rows = []
    for policy, forecast in (("control_current", True), ("custom", True)):
        rows.append({
            "issuance_key": f"{policy}|2026-01-01 05:00:00",
            "valid_time": datetime(2026, 1, 1, 0, tzinfo=UTC),
            "forecast_events": {"rain_any": forecast},
            "observed_events": {"rain_any": True},
        })

    results = _bootstrap_metric_deltas({"control_current": [rows[0]], "custom": [rows[1]]}, ("custom",))

    assert {row["metric"] for row in results} == {"POD", "FAR", "CSI"}
    assert all(row["policy"] == "custom" for row in results)
