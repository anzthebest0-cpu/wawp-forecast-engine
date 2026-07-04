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
import math
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
try:
    from scipy import stats as scipy_stats
except ModuleNotFoundError:
    scipy_stats = None

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

MODELS = [
    "ECMWF_HRES",
    "GFS_GLOBAL",
    "ICON_SEAMLESS",
    "GEM_GLOBAL",
    "CMA_GRAPES_GLOBAL",
    "JMA_GSM",
    "METEOFRANCE_ARPEGE_WORLD",
    "UKMO_GLOBAL_10KM",
]

MULTIPARAM_MODELS_OPENMETEO = MODELS
LEAD_BUCKETS_DEFAULT = ["L1_0_6h", "L2_6_12h", "L3_12_24h", "L4_24_48h", "L5_48plus"]
LEAD_BUCKETS_GUST = ["L1_0_6h", "L2_6_18h", "L3_18h_plus"]
HISTORICAL_PRIOR_BUCKET = "GLOBAL"
SOURCE_CONTINUOUS = "continuous_historical"
SOURCE_OPERATIONAL = "operational_multiinit"
LAYER_HISTORICAL = "historical_prior"
LAYER_OPERATIONAL = "operational_residual"
QM_MULTIPARAM_PARAMS = {
    "temperature": {"type": "linear", "min_samples": 100},
    "dewpoint": {"type": "linear", "min_samples": 100},
    "pressure": {"type": "linear", "min_samples": 100},
    "wind_speed": {"type": "nonneg", "min_samples": 100},
    "wind_gust": {"type": "gamma_parametric", "min_samples": 50, "min_samples_stable": 200},
    "wind_dir": {"type": "circular", "min_samples": 100},
    "rain": {"type": "zero_inflated", "min_samples": 50},
}
PARAM_DB_MAP = {
    "temperature": ("fcst_temperature", "obs_temperature"),
    "dewpoint": ("fcst_dewpoint", "obs_dewpoint"),
    "pressure": ("fcst_pressure", "obs_pressure"),
    "wind_speed": ("fcst_wind_speed", "obs_wind_speed"),
    "wind_gust": ("fcst_wind_gust", "obs_wind_gust"),
    "wind_dir": ("fcst_wind_dir", "obs_wind_dir"),
    "rain": ("fcst_rain", "obs_rain"),
}

# Seed values from the March 2026 diagnostic.
# Used as hard fallback when data is too sparse for empirical fitting.
# Keys: model name.  Values: list of (fc_mm, obs_mm) anchor pairs at the
# three operationally critical SOP thresholds.
_SEED_ANCHORS: dict[str, list[tuple[float, float]]] = {
    "ECMWF":     [(2.5, 5.0), (3.7, 10.0), (5.0, 20.0)],
    "ECMWF_HRES": [(2.5, 5.0), (3.7, 10.0), (5.0, 20.0)],
    "GFS":       [(2.3, 5.0), (2.9, 10.0), (3.7, 20.0)],
    "GFS_GLOBAL": [(2.3, 5.0), (2.9, 10.0), (3.7, 20.0)],
    "ICON":      [(1.4, 5.0), (1.7, 10.0), (1.9, 20.0)],
    "ICON_SEAMLESS": [(1.4, 5.0), (1.7, 10.0), (1.9, 20.0)],
    "METEOBLUE": [(3.5, 5.0), (6.0, 10.0), (8.0, 20.0)],
    "ACCESS-G3": [(2.9, 5.0), (3.8, 10.0), (5.0, 20.0)],
    "UKMO":      [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "UKMO_GLOBAL_10KM": [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "GEM":       [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "GEM_GLOBAL": [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "Multi-Model": [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "CMA":       [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "CMA_GRAPES_GLOBAL": [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "METEOFRANCE": [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "METEOFRANCE_ARPEGE_WORLD": [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
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
            log_fn(f"[qm] Unknown model '{model}' - skipped.")
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
            fallback = "using seed anchors as fallback" if model in _SEED_ANCHORS else "leaving raw values as pass-through"
            log_fn(f"[qm] {model}: only {n} rain-event pairs (need {QM_MIN_SAMPLES}) - {fallback}.")
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
            f"[qm] {model}: fitted empirical mapper on {n} pairs - "
            f"fc range [{fc_vals.min():.2f}, {fc_vals.max():.2f}] mm -> "
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
        return False, "No paired rainfall data available - QM not fitted."

    qm = QuantileMapper()
    qm.fit_all(df_rain, log_fn=log_fn)
    saved_path = qm.save(path)

    summary = qm.summary()
    empirical = [m for m in MODELS if qm.is_empirical(m)]
    seed_only  = [m for m in MODELS if not qm.is_empirical(m)]

    msg = (
        f"[qm] QM fitted and saved -> {saved_path}\n"
        f"  Empirical: {', '.join(empirical) if empirical else 'none'}\n"
        f"  Seed fallback: {', '.join(seed_only) if seed_only else 'none'}"
    )
    log_fn(msg)
    return True, msg


# ===========================================================================
# Multi-parameter QM stored in SQLite qm_cdfs
# ===========================================================================

def _as_arrays(fcst, obs) -> tuple[np.ndarray, np.ndarray]:
    fc = pd.to_numeric(pd.Series(fcst), errors="coerce").to_numpy(dtype=float)
    ob = pd.to_numeric(pd.Series(obs), errors="coerce").to_numpy(dtype=float)
    mask = ~np.isnan(fc) & ~np.isnan(ob)
    return fc[mask], ob[mask]


def _fit_empirical(fcst: np.ndarray, obs: np.ndarray, n_quantiles: int = 100) -> dict | None:
    if len(fcst) < 2:
        return None
    probs = np.linspace(0.0, 1.0, n_quantiles)
    fc_q = np.maximum.accumulate(np.quantile(fcst, probs))
    obs_q = np.maximum.accumulate(np.quantile(obs, probs))
    return {"fcst_quantiles": fc_q.tolist(), "obs_quantiles": obs_q.tolist(), "n_samples": int(len(fcst))}


def _fit_qm_linear(fcst: np.ndarray, obs: np.ndarray) -> dict | None:
    fc, ob = _as_arrays(fcst, obs)
    result = _fit_empirical(fc, ob)
    if result:
        result["method"] = "linear"
    return result


def _fit_qm_nonneg(fcst: np.ndarray, obs: np.ndarray) -> dict | None:
    fc, ob = _as_arrays(fcst, obs)
    result = _fit_empirical(np.maximum(fc, 0.0), np.maximum(ob, 0.0))
    if result:
        result["method"] = "nonneg"
    return result


def _fit_qm_circular(fcst: np.ndarray, obs: np.ndarray) -> dict | None:
    fc, ob = _as_arrays(fcst, obs)
    if len(fc) < 2:
        return None
    diff = ((ob - fc + 180.0) % 360.0) - 180.0
    radians = np.deg2rad(diff)
    mean_rad = math.atan2(float(np.mean(np.sin(radians))), float(np.mean(np.cos(radians))))
    offset = float(np.rad2deg(mean_rad))
    resultant = float((np.mean(np.sin(radians)) ** 2 + np.mean(np.cos(radians)) ** 2) ** 0.5)
    return {
        "fcst_quantiles": [0.0, 360.0],
        "obs_quantiles": [offset, offset],
        "n_samples": int(len(fc)),
        "method": "circular_offset",
        "offset_deg": offset,
        "resultant_length": resultant,
    }


def _fit_qm_zero_inflated(fcst: np.ndarray, obs: np.ndarray) -> dict | None:
    fc, ob = _as_arrays(fcst, obs)
    wet_mask = (fc > 0.1) & (ob > 0.1)
    fc_wet = np.maximum(fc[wet_mask], 0.0)
    ob_wet = np.maximum(ob[wet_mask], 0.0)
    result = _fit_empirical(fc_wet, ob_wet)
    if result:
        result["method"] = "zero_inflated"
        result["n_events"] = int(wet_mask.sum())
        result["fcst_wet_fraction"] = float((fc > 0.1).mean()) if len(fc) else 0.0
        result["obs_wet_fraction"] = float((ob > 0.1).mean()) if len(ob) else 0.0
    return result


def _fit_qm_gamma(fcst: np.ndarray, obs: np.ndarray) -> dict | None:
    fc, ob = _as_arrays(fcst, obs)
    mask = (fc > 0) & (ob > 0)
    fc, ob = fc[mask], ob[mask]
    if len(fc) < 50:
        return None
    if scipy_stats is None:
        fallback = _fit_qm_nonneg(fc, ob)
        if fallback:
            fallback["method"] = "nonneg_gamma_unavailable"
            fallback["low_confidence"] = True
        return fallback
    try:
        fc_shape, fc_loc, fc_scale = scipy_stats.gamma.fit(fc, floc=0)
        ob_shape, ob_loc, ob_scale = scipy_stats.gamma.fit(ob, floc=0)
        probs = np.linspace(0.01, 0.99, 100)
        fc_q = np.maximum.accumulate(scipy_stats.gamma.ppf(probs, fc_shape, loc=fc_loc, scale=fc_scale))
        ob_q = np.maximum.accumulate(scipy_stats.gamma.ppf(probs, ob_shape, loc=ob_loc, scale=ob_scale))
        return {
            "fcst_quantiles": fc_q.tolist(),
            "obs_quantiles": ob_q.tolist(),
            "n_samples": int(len(fc)),
            "method": "gamma_parametric",
            "low_confidence": bool(len(fc) < 200),
            "fc_shape": float(fc_shape),
            "fc_scale": float(fc_scale),
            "obs_shape": float(ob_shape),
            "obs_scale": float(ob_scale),
        }
    except Exception:
        fallback = _fit_qm_nonneg(fc, ob)
        if fallback:
            fallback["method"] = "nonneg_gamma_fallback"
        return fallback


def _apply_quantile(value: float, fc_q, obs_q) -> float:
    if value is None or pd.isna(value):
        return value
    fc_arr = np.asarray(fc_q, dtype=float)
    obs_arr = np.asarray(obs_q, dtype=float)
    if len(fc_arr) < 2:
        return float(value)
    return float(np.interp(float(value), fc_arr, obs_arr, left=obs_arr[0], right=obs_arr[-1]))


def _apply_qm_linear(value: float, qm: dict) -> float:
    return _apply_quantile(value, qm["fcst_quantiles"], qm["obs_quantiles"])


def _apply_qm_nonneg(value: float, qm: dict) -> float:
    return max(0.0, _apply_qm_linear(value, qm))


def _apply_qm_circular(value: float, qm: dict) -> float:
    if value is None or pd.isna(value):
        return value
    if qm.get("method") == "circular_offset":
        delta = float(qm.get("offset_deg") or 0.0)
    else:
        delta = _apply_quantile(value, qm["fcst_quantiles"], qm["obs_quantiles"])
    return float((float(value) + delta) % 360.0)


def _apply_qm_zero_inflated(value: float, qm: dict) -> float:
    if value is None or pd.isna(value):
        return value
    if float(value) <= 0.1:
        return max(0.0, float(value))
    return max(0.0, _apply_qm_linear(value, qm))


def _lead_bucket(lead_hours: float, parameter: str) -> str:
    h = float(lead_hours or 0.0)
    if parameter == "wind_gust":
        if h <= 6:
            return "L1_0_6h"
        if h <= 18:
            return "L2_6_18h"
        return "L3_18h_plus"
    if h <= 6:
        return "L1_0_6h"
    if h <= 12:
        return "L2_6_12h"
    if h <= 24:
        return "L3_12_24h"
    if h <= 48:
        return "L4_24_48h"
    return "L5_48plus"


def _fit_param(parameter: str, fcst: np.ndarray, obs: np.ndarray) -> dict | None:
    qm_type = QM_MULTIPARAM_PARAMS[parameter]["type"]
    if qm_type == "linear":
        return _fit_qm_linear(fcst, obs)
    if qm_type == "nonneg":
        return _fit_qm_nonneg(fcst, obs)
    if qm_type == "circular":
        return _fit_qm_circular(fcst, obs)
    if qm_type == "zero_inflated":
        return _fit_qm_zero_inflated(fcst, obs)
    if qm_type == "gamma_parametric":
        return _fit_qm_gamma(fcst, obs)
    return None


def _score_before_after(parameter: str, fcst: np.ndarray, obs: np.ndarray, qm: dict) -> dict:
    fc, ob = _as_arrays(fcst, obs)
    if len(fc) == 0:
        return {"mae_before": None, "mae_after": None, "bias_before": None, "bias_after": None}
    after = np.array([apply_qm_value(v, parameter, qm) for v in fc], dtype=float)
    if parameter == "wind_dir":
        before_err = ((fc - ob + 180.0) % 360.0) - 180.0
        after_err = ((after - ob + 180.0) % 360.0) - 180.0
    else:
        before_err = fc - ob
        after_err = after - ob
    return {
        "mae_before": float(np.mean(np.abs(before_err))),
        "mae_after": float(np.mean(np.abs(after_err))),
        "bias_before": float(np.mean(before_err)),
        "bias_after": float(np.mean(after_err)),
    }


def apply_qm_value(value: float, parameter: str, qm: dict) -> float:
    method = qm.get("method") or QM_MULTIPARAM_PARAMS.get(parameter, {}).get("type")
    if method in {"circular_delta", "circular_offset"}:
        return _apply_qm_circular(value, qm)
    if method in {"nonneg", "gamma_parametric", "nonneg_gamma_fallback"}:
        return _apply_qm_nonneg(value, qm)
    if method == "zero_inflated":
        return _apply_qm_zero_inflated(value, qm)
    return _apply_qm_linear(value, qm)


def _table_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_qm_schema(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS qm_cdfs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT NOT NULL,
            parameter TEXT NOT NULL,
            lead_bucket TEXT NOT NULL,
            fcst_quantiles TEXT NOT NULL,
            obs_quantiles TEXT NOT NULL,
            n_samples INTEGER NOT NULL,
            crps_before REAL,
            crps_after REAL,
            bias_before REAL,
            bias_after REAL,
            trained_at TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            method TEXT,
            low_confidence INTEGER DEFAULT 0,
            metadata TEXT,
            source_type TEXT DEFAULT 'unknown',
            correction_layer TEXT DEFAULT 'historical_prior',
            regime TEXT DEFAULT 'ALL',
            valid_period_start TEXT,
            valid_period_end TEXT,
            n_events INTEGER,
            validation_method TEXT,
            mae_before REAL,
            mae_after REAL,
            skill_score REAL,
            deprecated INTEGER DEFAULT 0,
            UNIQUE(model, parameter, lead_bucket)
        )
    """)
    for ddl in [
        "ALTER TABLE qm_cdfs ADD COLUMN source_type TEXT DEFAULT 'unknown'",
        "ALTER TABLE qm_cdfs ADD COLUMN correction_layer TEXT DEFAULT 'historical_prior'",
        "ALTER TABLE qm_cdfs ADD COLUMN regime TEXT DEFAULT 'ALL'",
        "ALTER TABLE qm_cdfs ADD COLUMN valid_period_start TEXT",
        "ALTER TABLE qm_cdfs ADD COLUMN valid_period_end TEXT",
        "ALTER TABLE qm_cdfs ADD COLUMN n_events INTEGER",
        "ALTER TABLE qm_cdfs ADD COLUMN validation_method TEXT",
        "ALTER TABLE qm_cdfs ADD COLUMN mae_before REAL",
        "ALTER TABLE qm_cdfs ADD COLUMN mae_after REAL",
        "ALTER TABLE qm_cdfs ADD COLUMN skill_score REAL",
        "ALTER TABLE qm_cdfs ADD COLUMN deprecated INTEGER DEFAULT 0",
    ]:
        try:
            cur.execute(ddl)
        except Exception:
            pass


def _skill_score(scores: dict) -> float | None:
    before = scores.get("mae_before")
    after = scores.get("mae_after")
    if before is None or after is None or before <= 0:
        return None
    return float((before - after) / before)


def _bias_improved(scores: dict) -> bool:
    before = scores.get("bias_before")
    after = scores.get("bias_after")
    if before is None or after is None:
        return False
    return abs(float(after)) <= abs(float(before))


def _enabled_by_validation(parameter: str, scores: dict) -> bool:
    skill = _skill_score(scores)
    if skill is None:
        return False
    if parameter == "rain":
        return skill >= -0.02 and _bias_improved(scores)
    return skill >= 0.0 or _bias_improved(scores)


def _min_samples_for_layer(parameter: str, correction_layer: str) -> int:
    if correction_layer == LAYER_OPERATIONAL:
        return {
            "temperature": 100,
            "dewpoint": 100,
            "pressure": 100,
            "wind_speed": 150,
            "wind_gust": 100,
            "wind_dir": 100,
            "rain": 75,
        }.get(parameter, QM_MULTIPARAM_PARAMS[parameter]["min_samples"])
    if parameter == "rain":
        return 50
    return QM_MULTIPARAM_PARAMS[parameter]["min_samples"]


def _event_count(parameter: str, fcst: np.ndarray, obs: np.ndarray) -> int:
    fc, ob = _as_arrays(fcst, obs)
    if parameter == "rain":
        return int(((fc > 0.1) & (ob > 0.1)).sum())
    if parameter == "wind_gust":
        return int(((fc > 0.0) & (ob > 0.0)).sum())
    return int(len(fc))


def _read_pair_frame(conn, model: str, parameter: str, bucket: str, source_type: str, correction_layer: str) -> pd.DataFrame:
    fc_col, obs_col = PARAM_DB_MAP[parameter]
    bucket_col = "lead_bucket_gust" if parameter == "wind_gust" else "lead_bucket"
    cols = _table_columns(conn, "qm_training_pairs")
    source_pred = "source_type = ? AND correction_layer = ?" if {"source_type", "correction_layer"}.issubset(cols) else "1=1"
    valid_expr = "valid_time" if "valid_time" in cols else "NULL AS valid_time"
    params: list[Any] = [model, bucket]
    if source_pred != "1=1":
        params.extend([source_type, correction_layer])
    return pd.read_sql_query(
        f"""
        SELECT {fc_col} AS fcst, {obs_col} AS obs, {valid_expr}
        FROM qm_training_pairs
        WHERE model = ? AND {bucket_col} = ?
          AND {source_pred}
          AND {fc_col} IS NOT NULL AND {obs_col} IS NOT NULL
        """,
        conn,
        params=tuple(params),
    )


def _insert_qm(
    conn,
    model: str,
    parameter: str,
    bucket: str,
    qm: dict,
    scores: dict,
    source_type: str,
    correction_layer: str,
    regime: str,
    valid_start: str | None,
    valid_end: str | None,
    enabled: bool,
) -> int:
    skill = _skill_score(scores)
    low_conf = bool(qm.get("low_confidence", False))
    metadata = {k: v for k, v in qm.items() if k not in {"fcst_quantiles", "obs_quantiles"}}
    conn.execute("""
        INSERT INTO qm_cdfs (
            model, parameter, lead_bucket, fcst_quantiles, obs_quantiles,
            n_samples, crps_before, crps_after, bias_before, bias_after,
            trained_at, enabled, method, low_confidence, metadata,
            source_type, correction_layer, regime, valid_period_start, valid_period_end,
            n_events, validation_method, mae_before, mae_after, skill_score, deprecated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(model, parameter, lead_bucket) DO UPDATE SET
            fcst_quantiles=excluded.fcst_quantiles,
            obs_quantiles=excluded.obs_quantiles,
            n_samples=excluded.n_samples,
            crps_before=excluded.crps_before,
            crps_after=excluded.crps_after,
            bias_before=excluded.bias_before,
            bias_after=excluded.bias_after,
            trained_at=excluded.trained_at,
            enabled=excluded.enabled,
            method=excluded.method,
            low_confidence=excluded.low_confidence,
            metadata=excluded.metadata,
            source_type=excluded.source_type,
            correction_layer=excluded.correction_layer,
            regime=excluded.regime,
            valid_period_start=excluded.valid_period_start,
            valid_period_end=excluded.valid_period_end,
            n_events=excluded.n_events,
            validation_method=excluded.validation_method,
            mae_before=excluded.mae_before,
            mae_after=excluded.mae_after,
            skill_score=excluded.skill_score,
            deprecated=excluded.deprecated
    """, (
        model,
        parameter,
        bucket,
        json.dumps(qm["fcst_quantiles"]),
        json.dumps(qm["obs_quantiles"]),
        qm["n_samples"],
        scores["mae_before"],
        scores["mae_after"],
        scores["bias_before"],
        scores["bias_after"],
        datetime.now(timezone.utc).isoformat(),
        1 if enabled else 0,
        qm.get("method"),
        1 if low_conf else 0,
        json.dumps(metadata),
        source_type,
        correction_layer,
        regime,
        valid_start,
        valid_end,
        int(qm.get("n_events") or qm.get("n_samples") or 0),
        "in_sample_guardrail",
        scores["mae_before"],
        scores["mae_after"],
        skill,
        0,
    ))
    row = conn.execute(
        "SELECT id FROM qm_cdfs WHERE model=? AND parameter=? AND lead_bucket=?",
        (model, parameter, bucket),
    ).fetchone()
    return int(row[0]) if row else 0


def fit_multiparam_qm_to_db(conn, log_fn=print) -> dict:
    trained = {}
    _ensure_qm_schema(conn)
    conn.execute(
        """
        UPDATE qm_cdfs
        SET enabled=0, deprecated=1
        WHERE COALESCE(source_type, 'unknown') = 'unknown'
           OR (correction_layer = ? AND lead_bucket <> ?)
        """,
        (LAYER_HISTORICAL, HISTORICAL_PRIOR_BUCKET),
    )
    historical_qm: dict[tuple[str, str], dict] = {}
    for model in MULTIPARAM_MODELS_OPENMETEO:
        trained[model] = {}
        for parameter in PARAM_DB_MAP:
            trained[model][parameter] = {}

            hist_df = _read_pair_frame(
                conn, model, parameter, HISTORICAL_PRIOR_BUCKET, SOURCE_CONTINUOUS, LAYER_HISTORICAL
            )
            min_samples = _min_samples_for_layer(parameter, LAYER_HISTORICAL)
            n_events = _event_count(parameter, hist_df.get("fcst", []), hist_df.get("obs", [])) if not hist_df.empty else 0
            if len(hist_df) >= min_samples and (parameter != "rain" or n_events >= min_samples):
                qm = _fit_param(parameter, hist_df["fcst"].to_numpy(), hist_df["obs"].to_numpy())
                if qm:
                    qm["n_events"] = n_events
                    scores = _score_before_after(parameter, hist_df["fcst"].to_numpy(), hist_df["obs"].to_numpy(), qm)
                    enabled = _enabled_by_validation(parameter, scores)
                    low_conf = bool(qm.get("low_confidence", False))
                    valid_start = str(hist_df["valid_time"].min()) if "valid_time" in hist_df else None
                    valid_end = str(hist_df["valid_time"].max()) if "valid_time" in hist_df else None
                    qm_id = _insert_qm(
                        conn, model, parameter, HISTORICAL_PRIOR_BUCKET, qm, scores,
                        SOURCE_CONTINUOUS, LAYER_HISTORICAL, "ALL", valid_start, valid_end, enabled
                    )
                    historical_qm[(model, parameter)] = qm if enabled else {}
                    trained[model][parameter][HISTORICAL_PRIOR_BUCKET] = {
                        "enabled": enabled,
                        "source_type": SOURCE_CONTINUOUS,
                        "correction_layer": LAYER_HISTORICAL,
                        "n_samples": qm["n_samples"],
                        "n_events": n_events,
                        "low_confidence": low_conf,
                        "skill_score": _skill_score(scores),
                        "qm_id": qm_id,
                    }
                    if low_conf:
                        log_fn(f"[LOW-CONF] {model}/{parameter}/GLOBAL historical prior: {qm['n_samples']} samples")
                else:
                    trained[model][parameter][HISTORICAL_PRIOR_BUCKET] = {"enabled": False, "n_samples": int(len(hist_df)), "n_events": n_events}
            else:
                trained[model][parameter][HISTORICAL_PRIOR_BUCKET] = {"enabled": False, "n_samples": int(len(hist_df)), "n_events": n_events}

            buckets = LEAD_BUCKETS_GUST if parameter == "wind_gust" else LEAD_BUCKETS_DEFAULT
            for bucket in buckets:
                op_df = _read_pair_frame(conn, model, parameter, bucket, SOURCE_OPERATIONAL, LAYER_OPERATIONAL)
                min_op = _min_samples_for_layer(parameter, LAYER_OPERATIONAL)
                op_events = _event_count(parameter, op_df.get("fcst", []), op_df.get("obs", [])) if not op_df.empty else 0
                key = f"{bucket}_operational_residual"
                if len(op_df) < min_op or (parameter == "rain" and op_events < min_op):
                    trained[model][parameter][key] = {"enabled": False, "n_samples": int(len(op_df)), "n_events": op_events}
                    continue
                prior = historical_qm.get((model, parameter))
                fc_values = op_df["fcst"].to_numpy()
                if prior:
                    fc_values = np.array([apply_qm_value(v, parameter, prior) for v in fc_values], dtype=float)
                qm = _fit_param(parameter, fc_values, op_df["obs"].to_numpy())
                if not qm:
                    trained[model][parameter][key] = {"enabled": False, "n_samples": int(len(op_df)), "n_events": op_events}
                    continue
                qm["n_events"] = op_events
                scores = _score_before_after(parameter, fc_values, op_df["obs"].to_numpy(), qm)
                enabled = _enabled_by_validation(parameter, scores)
                valid_start = str(op_df["valid_time"].min()) if "valid_time" in op_df else None
                valid_end = str(op_df["valid_time"].max()) if "valid_time" in op_df else None
                qm_id = _insert_qm(
                    conn, model, parameter, bucket, qm, scores,
                    SOURCE_OPERATIONAL, LAYER_OPERATIONAL, "ALL", valid_start, valid_end, enabled
                )
                trained[model][parameter][key] = {
                    "enabled": enabled,
                    "source_type": SOURCE_OPERATIONAL,
                    "correction_layer": LAYER_OPERATIONAL,
                    "n_samples": qm["n_samples"],
                    "n_events": op_events,
                    "low_confidence": bool(qm.get("low_confidence", False)),
                    "skill_score": _skill_score(scores),
                    "qm_id": qm_id,
                }
    conn.commit()
    return trained


def _load_qm_by_bucket(
    conn,
    model: str,
    parameter: str,
    bucket: str,
    source_type: str | None = None,
    correction_layer: str | None = None,
) -> dict | None:
    _ensure_qm_schema(conn)
    filters = ["model=?", "parameter=?", "lead_bucket=?", "enabled=1", "COALESCE(deprecated, 0)=0"]
    params: list[Any] = [model, parameter, bucket]
    if source_type:
        filters.append("source_type=?")
        params.append(source_type)
    if correction_layer:
        filters.append("correction_layer=?")
        params.append(correction_layer)
    row = conn.execute(f"""
        SELECT id, fcst_quantiles, obs_quantiles, method, metadata, source_type,
               correction_layer, low_confidence, skill_score
        FROM qm_cdfs
        WHERE {" AND ".join(filters)}
        ORDER BY trained_at DESC
        LIMIT 1
    """, tuple(params)).fetchone()
    if not row:
        return None
    metadata = json.loads(row[4] or "{}")
    metadata.update({
        "id": row[0],
        "fcst_quantiles": json.loads(row[1]),
        "obs_quantiles": json.loads(row[2]),
        "method": row[3],
        "source_type": row[5],
        "correction_layer": row[6],
        "low_confidence": bool(row[7]),
        "skill_score": row[8],
    })
    return metadata


def load_multiparam_qm(conn, model: str, parameter: str, lead_hours: float) -> dict | None:
    bucket = _lead_bucket(lead_hours, parameter)
    return (
        _load_qm_by_bucket(conn, model, parameter, bucket, SOURCE_OPERATIONAL, LAYER_OPERATIONAL)
        or _load_qm_by_bucket(conn, model, parameter, HISTORICAL_PRIOR_BUCKET, SOURCE_CONTINUOUS, LAYER_HISTORICAL)
    )


def apply_qm_with_layers(value: float, model: str, parameter: str, lead_hours: float, conn=None) -> dict:
    raw_value = value
    result = {
        "raw_value": raw_value,
        "historical_prior_value": raw_value,
        "operational_residual_value": None,
        "final_value": raw_value,
        "historical_qm_id": None,
        "operational_qm_id": None,
        "correction_layer_used": "raw",
        "low_confidence": False,
        "lead_bucket": _lead_bucket(lead_hours, parameter),
    }
    if conn is None or value is None or pd.isna(value):
        return result

    hist = _load_qm_by_bucket(
        conn, model, parameter, HISTORICAL_PRIOR_BUCKET, SOURCE_CONTINUOUS, LAYER_HISTORICAL
    )
    current = raw_value
    if hist:
        current = apply_qm_value(current, parameter, hist)
        result.update({
            "historical_prior_value": current,
            "final_value": current,
            "historical_qm_id": hist.get("id"),
            "correction_layer_used": LAYER_HISTORICAL,
            "low_confidence": bool(hist.get("low_confidence")),
        })

    op = _load_qm_by_bucket(
        conn, model, parameter, result["lead_bucket"], SOURCE_OPERATIONAL, LAYER_OPERATIONAL
    )
    if op:
        current = apply_qm_value(current, parameter, op)
        result.update({
            "operational_residual_value": current,
            "final_value": current,
            "operational_qm_id": op.get("id"),
            "correction_layer_used": LAYER_OPERATIONAL,
            "low_confidence": bool(result["low_confidence"] or op.get("low_confidence")),
        })
    return result


def apply_multiparam_qm(value: float, model: str, parameter: str, lead_hours: float, conn=None) -> float:
    return apply_qm_with_layers(value, model, parameter, lead_hours, conn=conn)["final_value"]


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
        tag = "-> empirical" if v >= VOTE_THR else "-> pass-through"
        print(f"  {v:.1f} mm {tag}: {out:.2f} mm")

    # 3. Seed fallback for a model with no data
    qm._set_seed_table("GFS")
    print(f"\nGFS seed transform (3.7mm -> should be ~10mm): "
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
    print(f"\nRound-trip save/load: ECMWF 3.0 mm -> {v_after:.2f} mm OK")

    print("\nSummary:")
    for model, info in qm2.summary().items():
        print(f"  {model}: {info}")

    print("\nAll tests passed.")
