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
    "GFS_SEAMLESS",
    "ICON_GLOBAL",
    "ICON_SEAMLESS",
    "GEM_GLOBAL",
    "GEM_SEAMLESS",
    "CMA_GRAPES_GLOBAL",
    "JMA_GSM",
    "BOM_ACCESS_GLOBAL",
    "METEOFRANCE_ARPEGE_WORLD",
    "UKMO_GLOBAL_10KM",
    "ERA5_SEAMLESS",
]

MULTIPARAM_MODELS_OPENMETEO = MODELS
LEAD_BUCKETS_DEFAULT = ["L1_0_6h", "L2_6_12h", "L3_12_24h", "L4_24_48h", "L5_48plus"]
LEAD_BUCKETS_GUST = ["L1_0_6h", "L2_6_18h", "L3_18h_plus"]
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
    "GFS":       [(2.3, 5.0), (2.9, 10.0), (3.7, 20.0)],
    "ICON":      [(1.4, 5.0), (1.7, 10.0), (1.9, 20.0)],
    "METEOBLUE": [(3.5, 5.0), (6.0, 10.0), (8.0, 20.0)],
    "ACCESS-G3": [(2.9, 5.0), (3.8, 10.0), (5.0, 20.0)],
    "UKMO":      [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "GEM":       [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "Multi-Model": [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "CMA":       [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
    "METEOFRANCE": [(2.5, 5.0), (3.5, 10.0), (5.0, 20.0)],
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
    result = _fit_empirical(fc, diff)
    if result:
        result["method"] = "circular_delta"
    return result


def _fit_qm_zero_inflated(fcst: np.ndarray, obs: np.ndarray) -> dict | None:
    fc, ob = _as_arrays(fcst, obs)
    wet_mask = (fc > 0.1) | (ob > 0.1)
    fc_wet = np.maximum(fc[wet_mask], 0.0)
    ob_wet = np.maximum(ob[wet_mask], 0.0)
    result = _fit_empirical(fc_wet, ob_wet)
    if result:
        result["method"] = "zero_inflated"
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
    if method == "circular_delta":
        return _apply_qm_circular(value, qm)
    if method in {"nonneg", "gamma_parametric", "nonneg_gamma_fallback"}:
        return _apply_qm_nonneg(value, qm)
    if method == "zero_inflated":
        return _apply_qm_zero_inflated(value, qm)
    return _apply_qm_linear(value, qm)


def fit_multiparam_qm_to_db(conn, log_fn=print) -> dict:
    trained = {}
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
            UNIQUE(model, parameter, lead_bucket)
        )
    """)
    for model in MULTIPARAM_MODELS_OPENMETEO:
        trained[model] = {}
        for parameter, (fc_col, obs_col) in PARAM_DB_MAP.items():
            buckets = LEAD_BUCKETS_GUST if parameter == "wind_gust" else LEAD_BUCKETS_DEFAULT
            bucket_col = "lead_bucket_gust" if parameter == "wind_gust" else "lead_bucket"
            trained[model][parameter] = {}
            for bucket in buckets:
                df = pd.read_sql_query(
                    f"""
                    SELECT {fc_col} AS fcst, {obs_col} AS obs
                    FROM qm_training_pairs
                    WHERE model = ? AND {bucket_col} = ?
                      AND {fc_col} IS NOT NULL AND {obs_col} IS NOT NULL
                    """,
                    conn,
                    params=(model, bucket),
                )
                min_samples = QM_MULTIPARAM_PARAMS[parameter]["min_samples"]
                if len(df) < min_samples:
                    trained[model][parameter][bucket] = {"enabled": False, "n_samples": int(len(df))}
                    continue
                qm = _fit_param(parameter, df["fcst"].to_numpy(), df["obs"].to_numpy())
                if not qm:
                    trained[model][parameter][bucket] = {"enabled": False, "n_samples": int(len(df))}
                    continue
                scores = _score_before_after(parameter, df["fcst"].to_numpy(), df["obs"].to_numpy(), qm)
                low_conf = bool(qm.get("low_confidence", False))
                cur.execute("""
                    INSERT INTO qm_cdfs (
                        model, parameter, lead_bucket, fcst_quantiles, obs_quantiles,
                        n_samples, crps_before, crps_after, bias_before, bias_after,
                        trained_at, enabled, method, low_confidence, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        metadata=excluded.metadata
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
                    1,
                    qm.get("method"),
                    1 if low_conf else 0,
                    json.dumps({k: v for k, v in qm.items() if k not in {"fcst_quantiles", "obs_quantiles"}}),
                ))
                trained[model][parameter][bucket] = {"enabled": True, "n_samples": qm["n_samples"], "low_confidence": low_conf}
                if low_conf:
                    log_fn(f"[LOW-CONF] {model}/{parameter}/{bucket}: {qm['n_samples']} samples")
    conn.commit()
    return trained


def load_multiparam_qm(conn, model: str, parameter: str, lead_hours: float) -> dict | None:
    bucket = _lead_bucket(lead_hours, parameter)
    row = conn.execute("""
        SELECT fcst_quantiles, obs_quantiles, method, metadata
        FROM qm_cdfs
        WHERE model=? AND parameter=? AND lead_bucket=? AND enabled=1
    """, (model, parameter, bucket)).fetchone()
    if not row:
        return None
    metadata = json.loads(row[3] or "{}")
    metadata.update({
        "fcst_quantiles": json.loads(row[0]),
        "obs_quantiles": json.loads(row[1]),
        "method": row[2],
    })
    return metadata


def apply_multiparam_qm(value: float, model: str, parameter: str, lead_hours: float, conn=None) -> float:
    if conn is None:
        return value
    qm = load_multiparam_qm(conn, model, parameter, lead_hours)
    if not qm:
        return value
    return apply_qm_value(value, parameter, qm)


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
