import sqlite3
from types import SimpleNamespace

import pandas as pd

from src.export_dashboard_data import _observation_freshness, _select_default_taf_window


FRESH_MODELS = {"A", "B", "C", "D", "E"}


def _forecast_rows(models, start, hours=6):
    rows = []
    for timestamp in pd.date_range(start, periods=hours, freq="h"):
        for model in models:
            rows.append({"model": model, "forecast_time": timestamp, "visibility": 9999.0})
    return rows


def test_default_selection_skips_stale_early_single_model_timeline():
    rows = _forecast_rows({"STALE"}, "2026-07-10 15:00:00")
    rows += _forecast_rows(FRESH_MODELS, "2026-07-10 20:00:00")
    selection = _select_default_taf_window(
        pd.DataFrame(rows),
        FRESH_MODELS,
        pd.Timestamp("2026-07-10 10:00:41", tz="UTC").to_pydatetime(),
    )

    assert selection["selection_status"] == "selected"
    assert selection["selected_issuance"] == "1100"
    assert selection["selected_valid_start_wita"] == "2026-07-10 20:00:00"


def test_default_selection_suppresses_when_quorum_is_not_available():
    models = {"A", "B", "C", "D"}
    selection = _select_default_taf_window(
        pd.DataFrame(_forecast_rows(models, "2026-07-10 20:00:00")),
        models,
        pd.Timestamp("2026-07-10 10:00:41", tz="UTC").to_pydatetime(),
    )

    assert selection["selection_status"] == "suppressed"
    assert selection["selected_issuance"] is None


def test_observation_freshness_freezes_verification_when_hourly_data_is_stale():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE awos_observations (obs_time TEXT)")
    conn.execute("CREATE TABLE awos_observations_1min (obs_time TEXT)")
    conn.execute("INSERT INTO awos_observations VALUES ('2026-07-01 00:00:00')")
    conn.execute("INSERT INTO awos_observations_1min VALUES ('2026-07-01 00:00:00')")

    freshness = _observation_freshness(
        SimpleNamespace(conn=conn),
        pd.Timestamp("2026-07-02 00:01:00", tz="UTC").to_pydatetime(),
    )

    assert freshness["hourly"]["status"] == "stale"
    assert freshness["verification_status"] == "frozen"
