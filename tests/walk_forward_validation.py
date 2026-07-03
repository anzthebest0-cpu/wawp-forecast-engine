"""
Walk-forward validation helper for multi-parameter QM.

This script is intentionally lightweight: it reads qm_training_pairs, trains on
one rolling window, tests on the next month, and prints MAE before/after.
"""
from __future__ import annotations

import os
import sqlite3

import numpy as np
import pandas as pd

from src.quantile_mapper import _fit_qm_linear, apply_qm_value


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(root, "wawp_forecasts.db")
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("""
            SELECT valid_time, model, fcst_temperature, obs_temperature
            FROM qm_training_pairs
            WHERE fcst_temperature IS NOT NULL AND obs_temperature IS NOT NULL
            ORDER BY valid_time
        """, conn)
    if df.empty:
        print("No training pairs available.")
        return
    df["valid_time"] = pd.to_datetime(df["valid_time"])
    df["month"] = df["valid_time"].dt.to_period("M")
    months = sorted(df["month"].unique())
    results = []
    for i in range(12, len(months)):
        train = df[df["month"].isin(months[i - 12:i])]
        test = df[df["month"] == months[i]]
        qm = _fit_qm_linear(train["fcst_temperature"].values, train["obs_temperature"].values)
        if not qm or test.empty:
            continue
        before = np.mean(np.abs(test["fcst_temperature"] - test["obs_temperature"]))
        after_values = [apply_qm_value(v, "temperature", qm) for v in test["fcst_temperature"]]
        after = np.mean(np.abs(np.array(after_values) - test["obs_temperature"].values))
        results.append((str(months[i]), before, after))
    for month, before, after in results:
        print(f"{month}: MAE before={before:.3f}, after={after:.3f}")


if __name__ == "__main__":
    main()
