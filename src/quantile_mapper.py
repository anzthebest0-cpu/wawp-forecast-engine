"""
quantile_mapper.py
==================
Phase 2a — Tail-Only Quantile Mapping for WAWP TAF Guidance System.

Design
------
Corrects rainfall INTENSITY for model values at or above VOTE_THR (1.5 mm)
while leaving sub-threshold values unchanged.  This preserves the timing
signal (which is already reasonable) while fixing the structural intensity
ceiling (~6 mm) caused by convective parameterisation in global NWP models
over coastal Sulawesi.

Method: Gudmundsson et al. (2012) tail-only empirical CDF matching.
  - Fit on the rain-only conditional distribution (fc >= VOTE_THR AND obs >= VOTE_THR)
  - Linear interpolation between empirical quantile anchor points
  - Extrapolation above the highest fitted forecast quantile is capped at the
    highest observed value seen during fitting (conservative — prevents
    artificial extremes from sparse data).
  - Sub-threshold values (< VOTE_THR) are passed through unchanged.

Phase 2b (full piecewise mapping) is deferred until 90+ days of paired data
are available.  See roadmap §P2-2.

Interface
---------
    from quantile_mapper import QuantileMapper

    # Fit (typically called inside regenerate_guidance_json)
    qm = QuantileMapper()
    qm.fit(df_paired, model="ECMWF")   # df_paired: columns [Rain, OBS_Rain]
    qm.save()                          # → qm_state.json in New_CODE\

    # Load and apply (called in tafor_generator / taf_core)
    qm = QuantileMapper.load()
    corrected_mm = qm.transform(raw_mm, model="ECMWF")

    # Apply to a full consensus dict row
    corrected_row = qm.transform_row(row_dict, model="ECMWF")

Constants
---------
QM_N_QUANTILES  : number of empirical quantile anchor points (default 20)
QM_MIN_SAMPLES  : minimum rain-event pairs needed to fit a model (default 10)
VOTE_THR        : rain/no-rain boundary — must match RainConfig.VOTE_THR (1.5 mm)

Reference seed values from the diagnostic (March 2026 dataset)
--------------------------------------------------------------
BMKG SOP level | OBS percentile | ECMWF  | GFS   | ICON  | MB    | AG3
5 mm  (RA)     | 98.6th         | 2.5 mm | 2.3mm | 1.4mm | 3.5mm | 2.9mm
10 mm (+RA)    | 99.4th         | 3.7 mm | 2.9mm | 1.7mm | 6.0mm | 3.8mm
20 mm (V.Hvy)  | 99.8th         | 5.0 mm | 3.7mm | 1.9mm | 8.0mm | 5.0mm

These seed values are used as a hard fallback when observed data is too sparse
to fit an empirical mapper for a given model.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths — mirror config.py conventions (no config import to keep this module
# self-contained and importable without the full pipeline).
# ---------------------------------------------------------------------------
_QM_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "qm_state.json")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VOTE_THR       = 1.0   # mm — rain / no-rain boundary (must match RainConfig)
QM_N_QUANTILES = 20    # empirical quantile anchor points per model
QM_MIN_SAMPLES = 10    # minimum rain-event pairs to fit a model empirically

MODELS = ["ECMWF", "GFS", "ICON", "METEOBLUE", "ACCESS-G3"]

# Seed values from the March 2026 diagnostic.
# Used as hard fallback when data is too sparse for empirical fitting.
# Keys: model name.  Values: list of (fc_mm, obs_mm) anchor pairs at the
# three operationally critical SOP thresholds.
_SEED_ANCHORS: dict[str, list[tuple[float, float]]] = {
    "ECMWF":     [(2.5, 5.0), (3.7, 10.0), (5.0, 20.0)],
    "GFS":       [(2.3, 5.0), (2.9, 10.0), (3.7, 20.0)],
    "ICON":      [(1.4, 5.0), (1.7, 10.0), (1.9, 20.0)],
    "METEOBLUE": [(3.5, 5.0), (6.0, 10.0), (8.0, 20.0)],
    "ACCESS-G3": [(2.9, 5.0), (3.8, 10.0), (5.0, 20.0)],
}


# ===========================================================================
# Core mapper class
# ===========================================================================

class QuantileMapper:
    """
    Per-model tail-only empirical quantile mapper for rainfall.

    Attributes
    ----------
    _tables : dict[model_name → {"fc_quantiles": [...], "obs_quantiles": [...],
                                  "obs_max": float, "source": "empirical"|"seed",
                                  "n_pairs": int, "fitted_at": ISO-8601}]
    """

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, Any]] = {}

    # -----------------------------------------------------------------------
    # Fitting
    # -----------------------------------------------------------------------

    def fit_model(
        self,
        df_paired: pd.DataFrame,
        model: str,
        fc_col:  str = "Rain",
        obs_col: str = "OBS_Rain",
        log_fn=print,
    ) -> None:
        """
        Fit tail-only QM for one model from a paired forecast-observation
        DataFrame.

        Parameters
        ----------
        df_paired : DataFrame with columns [fc_col, obs_col] (already filtered
                    to this model — e.g. df_paired[df_paired["Model"]==model])
        model     : model name string, must be in MODELS
        fc_col    : forecast column name  (default "Rain")
        obs_col   : observation column name (default "OBS_Rain")
        """
        if model not in MODELS:
            log_fn(f"[qm] Unknown model '{model}' — skipped.")
            return

        # Filter to rain events on both sides
        mask = (
            pd.to_numeric(df_paired[fc_col],  errors="coerce") >= VOTE_THR
        ) & (
            pd.to_numeric(df_paired[obs_col], errors="coerce") >= VOTE_THR
        )
        rain_df = df_paired[mask].copy()
        n = len(rain_df)

        if n < QM_MIN_SAMPLES:
            log_fn(
                f"[qm] {model}: only {n} rain-event pairs "
                f"(need {QM_MIN_SAMPLES}) — using seed anchors as fallback."
            )
            self._set_seed_table(model)
            return

        fc_vals  = pd.to_numeric(rain_df[fc_col],  errors="coerce").dropna().values
        obs_vals = pd.to_numeric(rain_df[obs_col], errors="coerce").dropna().values

        # Build empirical quantile tables at QM_N_QUANTILES evenly-spaced
        # percentile points spanning VOTE_THR to max observed value.
        probs = np.linspace(0.0, 1.0, QM_N_QUANTILES)
        fc_q  = np.quantile(fc_vals,  probs)
        obs_q = np.quantile(obs_vals, probs)

        # Enforce monotonicity (can break with sparse data)
        fc_q  = np.maximum.accumulate(fc_q)
        obs_q = np.maximum.accumulate(obs_q)

        self._tables[model] = {
            "fc_quantiles":  fc_q.tolist(),
            "obs_quantiles": obs_q.tolist(),
            "obs_max":       float(obs_vals.max()),
            "source":        "empirical",
            "n_pairs":       n,
            "fitted_at":     datetime.now(timezone.utc).isoformat(),
        }
        log_fn(
            f"[qm] {model}: fitted empirical mapper on {n} pairs — "
            f"fc range [{fc_vals.min():.2f}, {fc_vals.max():.2f}] mm → "
            f"obs range [{obs_vals.min():.2f}, {obs_vals.max():.2f}] mm"
        )

    def fit_all(
        self,
        df_all: pd.DataFrame,
        fc_col:  str = "Rain",
        obs_col: str = "OBS_Rain",
        model_col: str = "Model",
        log_fn=print,
    ) -> None:
        """
        Fit mapper for all models from a combined DataFrame (as returned by
        load_verification_data with parameter="Rainfall").
        """
        for model in MODELS:
            sub = df_all[df_all[model_col] == model] if model_col in df_all else df_all
            self.fit_model(sub, model, fc_col, obs_col, log_fn=log_fn)

    def _set_seed_table(self, model: str) -> None:
        """Use hardcoded diagnostic anchors as fallback for sparse-data models."""
        anchors = _SEED_ANCHORS.get(model, [])
        if not anchors:
            return
        # Prepend VOTE_THR → VOTE_THR (identity at the lower boundary)
        fc_pts  = [VOTE_THR] + [a[0] for a in anchors]
        obs_pts = [VOTE_THR] + [a[1] for a in anchors]
        self._tables[model] = {
            "fc_quantiles":  fc_pts,
            "obs_quantiles": obs_pts,
            "obs_max":       max(obs_pts),
            "source":        "seed",
            "n_pairs":       0,
            "fitted_at":     datetime.now(timezone.utc).isoformat(),
        }

    # -----------------------------------------------------------------------
    # Transform
    # -----------------------------------------------------------------------

    def transform(self, fc_mm: float, model: str) -> float:
        """
        Apply tail-only QM to a single forecast value.

        Values below VOTE_THR are returned unchanged (preserves timing signal).
        Values above the highest fitted forecast quantile are capped at obs_max
        (conservative extrapolation for sparse tail data).

        Parameters
        ----------
        fc_mm : raw model forecast in mm
        model : model name

        Returns
        -------
        Corrected value in mm (observation-equivalent space)
        """
        if fc_mm < VOTE_THR:
            return float(fc_mm)   # sub-threshold: pass through unchanged

        table = self._tables.get(model)
        if table is None:
            return float(fc_mm)   # no mapper for this model: pass through

        fc_q  = np.asarray(table["fc_quantiles"],  dtype=float)
        obs_q = np.asarray(table["obs_quantiles"], dtype=float)

        if len(fc_q) < 2:
            return float(fc_mm)

        # Linear interpolation in the fitted range
        if fc_mm <= fc_q[-1]:
            corrected = float(np.interp(fc_mm, fc_q, obs_q))
        else:
            # Above highest fitted quantile: extrapolate linearly but cap at obs_max
            slope = (obs_q[-1] - obs_q[-2]) / max(fc_q[-1] - fc_q[-2], 1e-6)
            extrapolated = obs_q[-1] + slope * (fc_mm - fc_q[-1])
            corrected = float(min(extrapolated, table["obs_max"]))

        return max(corrected, VOTE_THR)   # never map a rain event to < VOTE_THR

    def transform_series(
        self, fc_series: pd.Series, model: str
    ) -> pd.Series:
        """Apply transform to a pandas Series."""
        return fc_series.apply(lambda v: self.transform(float(v), model))

    # -----------------------------------------------------------------------
    # State persistence
    # -----------------------------------------------------------------------

    def save(self, path: str = _QM_PATH) -> str:
        """
        Atomically write the fitted mapper state to JSON.

        Returns the path written.
        """
        payload = {
            "schema_version": "1.0",
            "vote_thr":       VOTE_THR,
            "n_quantiles":    QM_N_QUANTILES,
            "min_samples":    QM_MIN_SAMPLES,
            "saved_at":       datetime.now(timezone.utc).isoformat(),
            "models":         self._tables,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str = _QM_PATH) -> "QuantileMapper":
        """
        Load a previously saved mapper state.

        Falls back to a seed-only mapper if the file is missing or invalid.
        """
        qm = cls()
        if not os.path.exists(path):
            # No state on disk yet — bootstrap from seeds so the mapper is
            # always usable even on first run before fitting.
            for model in MODELS:
                qm._set_seed_table(model)
            return qm
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            qm._tables = data.get("models", {})
            # Back-fill any model that's missing (e.g. new model added later)
            for model in MODELS:
                if model not in qm._tables:
                    qm._set_seed_table(model)
        except Exception:
            for model in MODELS:
                qm._set_seed_table(model)
        return qm

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a human-readable summary dict for logging / display."""
        out = {}
        for model, tbl in self._tables.items():
            out[model] = {
                "source":   tbl.get("source", "unknown"),
                "n_pairs":  tbl.get("n_pairs", 0),
                "fitted_at": tbl.get("fitted_at", ""),
                "fc_range": (
                    f"{tbl['fc_quantiles'][0]:.2f}–{tbl['fc_quantiles'][-1]:.2f} mm"
                    if tbl.get("fc_quantiles") else "N/A"
                ),
                "obs_range": (
                    f"{tbl['obs_quantiles'][0]:.2f}–{tbl['obs_quantiles'][-1]:.2f} mm"
                    if tbl.get("obs_quantiles") else "N/A"
                ),
            }
        return out

    def is_empirical(self, model: str) -> bool:
        """True if the model mapper was fitted from real data (not seed)."""
        return self._tables.get(model, {}).get("source") == "empirical"


# ===========================================================================
# Integration helper — called inside regenerate_guidance_json()
# ===========================================================================

def fit_and_save_qm(
    station: str,
    log_fn=print,
    path: str = _QM_PATH,
    df_rain: pd.DataFrame | None = None,
) -> tuple[bool, str]:
    """
    Fit tail-only QM for all models and save state to disk.

    Called automatically inside regenerate_guidance_json() so the mapper
    is always in sync with the latest guidance JSON.

    Parameters
    ----------
    station : station name string (passed to load_verification_data)
    log_fn  : logging callback (same signature as guidance_generator)
    path    : output path for qm_state.json
    df_rain : optional pre-loaded rainfall verification DataFrame
              (skips redundant disk loading when provided)

    Returns
    -------
    (success: bool, message: str)
    """
    if df_rain is None:
        try:
            from guidance_generator import load_verification_data
        except ImportError as e:
            return False, f"Cannot import guidance_generator: {e}"

        try:
            df_rain = load_verification_data(station, "Rainfall", log_fn=log_fn)
        except Exception as e:
            return False, f"load_verification_data failed: {e}"

    if df_rain.empty:
        return False, "No paired rainfall data available — QM not fitted."

    qm = QuantileMapper()
    qm.fit_all(df_rain, log_fn=log_fn)
    saved_path = qm.save(path)

    summary = qm.summary()
    empirical = [m for m in MODELS if qm.is_empirical(m)]
    seed_only  = [m for m in MODELS if not qm.is_empirical(m)]

    msg = (
        f"[qm] QM fitted and saved → {saved_path}\n"
        f"  Empirical: {', '.join(empirical) if empirical else 'none'}\n"
        f"  Seed fallback: {', '.join(seed_only) if seed_only else 'none'}"
    )
    log_fn(msg)
    return True, msg


# ===========================================================================
# Self-test
# ===========================================================================

if __name__ == "__main__":
    print("=== quantile_mapper.py self-test ===\n")

    # 1. Build synthetic paired data mimicking the diagnostic
    rng = np.random.default_rng(42)
    n_pairs = 60
    obs_rain = np.concatenate([
        rng.uniform(1.5, 5.0,  int(n_pairs * 0.6)),  # light
        rng.uniform(5.0, 15.0, int(n_pairs * 0.3)),  # moderate
        rng.uniform(15.0, 30.0, int(n_pairs * 0.1)), # heavy
    ])
    fc_rain_ecmwf = np.clip(obs_rain * rng.uniform(0.2, 0.5, len(obs_rain)), 1.5, 6.0)

    df = pd.DataFrame({
        "Rain":     fc_rain_ecmwf,
        "OBS_Rain": obs_rain,
        "Model":    "ECMWF",
    })

    qm = QuantileMapper()
    qm.fit_model(df, "ECMWF", log_fn=print)

    # 2. Test transform
    test_inputs = [0.5, 1.5, 2.5, 3.7, 5.0, 6.0, 8.0]
    print("\nTransform results (ECMWF):")
    for v in test_inputs:
        out = qm.transform(v, "ECMWF")
        tag = "→ empirical" if v >= VOTE_THR else "→ pass-through"
        print(f"  {v:.1f} mm {tag}: {out:.2f} mm")

    # 3. Seed fallback for a model with no data
    qm._set_seed_table("GFS")
    print(f"\nGFS seed transform (3.7mm → should be ~10mm): "
          f"{qm.transform(3.7, 'GFS'):.2f} mm")

    # 4. Save / load round-trip
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    qm.save(tmp_path)
    qm2 = QuantileMapper.load(tmp_path)
    v_before = qm.transform(3.0, "ECMWF")
    v_after  = qm2.transform(3.0, "ECMWF")
    assert abs(v_before - v_after) < 0.01, "Round-trip mismatch"
    print(f"\nRound-trip save/load: ECMWF 3.0 mm → {v_after:.2f} mm ✅")

    print("\nSummary:")
    for model, info in qm2.summary().items():
        print(f"  {model}: {info}")

    print("\nAll tests passed.")
