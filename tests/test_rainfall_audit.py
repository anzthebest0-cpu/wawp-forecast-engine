import sqlite3

import pandas as pd

from src.rainfall_audit import build_observation_ledger, profile_minute_hourly_reconciliation


def _connection(values):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE awos_observations "
        "(location TEXT, obs_time TEXT, rain_1h REAL)"
    )
    conn.executemany(
        "INSERT INTO awos_observations VALUES ('Bandara_Sangia_Ni_Bandera', ?, ?)",
        values,
    )
    return conn


def test_isolated_hour_is_one_episode_but_three_legacy_samples():
    conn = _connection([
        ("2026-01-01 00:00:00", 0.0),
        ("2026-01-01 01:00:00", 2.0),
        ("2026-01-01 02:00:00", 0.0),
    ])

    _, summary = build_observation_ledger(conn)
    metrics = summary["metric_comparison_at_threshold"]

    assert metrics["strict_hourly_wet_samples"] == 1
    assert metrics["unique_contiguous_episodes"] == 1
    assert metrics["legacy_centered_rolling_peak_samples"] == 3
    assert metrics["complete_utc_aligned_3h_end_labeled_bins"] == 0
    assert metrics["wet_complete_3h_bins_at_same_numeric_threshold"] == 0


def test_gap_breaks_rain_episode_and_incomplete_three_hour_bin():
    conn = _connection([
        ("2026-01-01 00:00:00", 2.0),
        ("2026-01-01 02:00:00", 2.0),
    ])

    _, summary = build_observation_ledger(conn)
    metrics = summary["metric_comparison_at_threshold"]

    assert metrics["strict_hourly_wet_samples"] == 2
    assert metrics["unique_contiguous_episodes"] == 2
    assert metrics["complete_utc_aligned_3h_end_labeled_bins"] == 0


def test_interval_end_hours_01_to_03_form_complete_three_hour_block():
    conn = _connection([
        ("2026-01-01 01:00:00", 0.8),
        ("2026-01-01 02:00:00", 0.8),
        ("2026-01-01 03:00:00", 0.0),
    ])

    ledger, summary = build_observation_ledger(conn)
    metrics = summary["metric_comparison_at_threshold"]

    assert metrics["complete_utc_aligned_3h_end_labeled_bins"] == 1
    assert metrics["wet_complete_3h_bins_at_same_numeric_threshold"] == 1
    assert ledger["block_3h_end_utc"].nunique() == 1
    assert ledger["block_3h_end_utc"].iloc[0] == pd.Timestamp("2026-01-01 03:00:00")


def test_minute_rain_matches_hourly_at_interval_end_boundary_not_by_sum():
    conn = _connection([("2026-01-01 01:00:00", 0.3)])
    conn.execute(
        "CREATE TABLE awos_observations_1min "
        "(location TEXT, obs_time TEXT, rain_1min REAL)"
    )
    conn.executemany(
        "INSERT INTO awos_observations_1min VALUES "
        "('Bandara_Sangia_Ni_Bandera', ?, ?)",
        [
            ("2026-01-01 00:58:00", 0.2),
            ("2026-01-01 00:59:00", 0.3),
            ("2026-01-01 01:00:00", 0.3),
        ],
    )

    result = profile_minute_hourly_reconciliation(conn)

    assert result["paired_hour_count"] == 1
    assert result["all_hour_boundary_value_matches_hourly_count"] == 1
    assert result["all_hour_preceding_minute_sum_matches_hourly_count"] == 0
    assert result["boundary_match_rate"] == 1.0
