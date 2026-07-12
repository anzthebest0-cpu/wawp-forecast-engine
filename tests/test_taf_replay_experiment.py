import pandas as pd

from src.taf_replay_experiment import AsOfWeightEngine, MODELS, PAIR_COLUMNS, _flatten_taf, _issue_times


def test_flattened_replay_taf_has_no_bulletin_header_or_blank_lines():
    raw = "FTID40 WAWP 312300\nTAF WAWP 312300Z 0100/0200 16005KT 9999 FEW019\nTEMPO 0106/0110 4000 TSRA SCT016CB BKN018="

    assert _flatten_taf(raw) == (
        "TAF WAWP 312300Z 0100/0200 16005KT 9999 FEW019 "
        "TEMPO 0106/0110 4000 TSRA SCT016CB BKN018="
    )


def test_issue_sequence_includes_prior_day_23z_then_official_shifts():
    issues = _issue_times(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-01"))

    assert [issue.strftime("%Y-%m-%d %H:%M") for issue in issues] == [
        "2025-12-31 23:00",
        "2026-01-01 05:00",
        "2026-01-01 11:00",
        "2026-01-01 17:00",
        "2026-01-01 23:00",
    ]


def test_asof_weight_engine_excludes_future_pairs_and_normalizes_weights():
    rows = []
    timestamps = pd.date_range("2025-12-01", periods=40, freq="h")
    for model_number, model in enumerate(MODELS, start=1):
        for timestamp in timestamps:
            row = {"model": model, "valid_dt": timestamp}
            for forecast_column, observation_column, _ in PAIR_COLUMNS.values():
                row[forecast_column] = float(model_number)
                row[observation_column] = 0.0
            rows.append(row)
        future = rows[-1].copy()
        future["valid_dt"] = pd.Timestamp("2026-02-01")
        future["fcst_temperature"] = 999.0
        rows.append(future)

    weights = AsOfWeightEngine(pd.DataFrame(rows)).weights(
        pd.Timestamp("2026-01-01"), lookback_days=90, equal=False
    )

    assert weights["Temperature"]["ECMWF_HRES"] > weights["Temperature"]["UKMO_GLOBAL_10KM"]
    for parameter_weights in weights.values():
        assert abs(sum(parameter_weights.values()) - 1.0) < 1e-12
