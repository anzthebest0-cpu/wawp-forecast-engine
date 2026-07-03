"""
Advanced Ensemble Weighter for TAF Guidance
===========================================
Version: 5.8.0

[v5.8.0 — Timing Budget Rebalance, Shrinkage Reduction, SOP-Cost Regime Averaging, Weight Spreads]

  DIURNAL COMPONENT REMOVED
  ─────────────────────────
  The diurnal-bin weighting (v5.6.0) has been removed.
  Rationale: the ±2h tolerance merge used in rainfall verification
  smears event timestamps across bin boundaries — exactly the
  transition zones the bins were designed to resolve.  With a
  6-hour bin width and a ±2h tolerance, up to 33% of morning-bin
  records were contaminated by afternoon-regime observations.
  The contamination is directional (systematic per model timing
  bias), not random noise that averages out.
  Removing the component restores clean separation of concerns:
  the tolerance merge is now used only by timing_weight (which
  explicitly measures timing offset — the correct use).

  BUDGET RESTORATION
  ──────────────────
  v5.6.0:  base=0.25  timing=0.20  regime=0.20  temporal=0.20  diurnal=0.15
  v5.7.0:  base=0.30  timing=0.25  regime=0.25  temporal=0.20
  Sum = 1.00 (unchanged).  Values match pre-v5.6.0 (v5.5.1) levels.

  TIMING BUDGET FIX FOR WS/WD (retained from v5.6.0 Issue 1)
  ─────────────────────────────────────────────────────────────
  For non-Rainfall parameters, effective_timing = 0 and the timing
  budget is redirected to temporal.  WS/WD effective temporal = 0.45.

  TEMPORAL BLEND RECENCY BIAS (retained from v5.6.0 Issue 2)
  ────────────────────────────────────────────────────────────
  _average_temporal_weights() uses recency-biased blend:
    24 Hours → 0.40,  3 Days → 0.30,  7 Days → 0.20,  Month → 0.10

  COMPOSITE METRIC REBALANCE (retained from v5.6.0 Issue 3)
  ───────────────────────────────────────────────────────────
  rmse=0.20  mae=0.15  bias=0.05  pod=0.20  far=0.15
  csi=0.10  hss=0.10  mcc=0.05

[v5.5.1 — Timing Weight Component]
  Gap 1 fix: model weighting now penalises rainfall timing errors, not just
  intensity errors.  The ±2-hour tolerance merge (v5.5.0) forgives timing
  offsets for Hit/Miss counting, but the CRPS objective never saw the timing
  error because it was absorbed into the pairing step.  A model that fires
  1.9h early earned the same weight as one that fires on time, even though
  the former systematically produces wrong BECMG/TEMPO start times.

  Lead-time component disabled on OPERATIONAL data (leadtime_weight: 0.00):
  SCHEME_1_OPERATIONAL files cover only lead hours 0-23 (Day_1 bracket).
  The sample-count-weighted average over lead-time brackets collapsed to
  Day_1 = base_w, so 25% of the weight budget was being spent to replicate
  the base component.  The 25% is redirected to the new timing component.
  leadtime_weight is kept in WeightConfiguration at 0.00 so callers that
  explicitly pass it do not break, and it can be restored when RELIABILITY
  data (lead hours 0-120) becomes available.

  New WeightConfiguration fields:
    timing_weight: float = 0.25   (new — replaces leadtime budget)

  New method:
    calculate_timing_weights(df, forecast_col, obs_col, model_col,
                              parameter, base_w) -> Dict[str, float]
    - Only active for Rainfall when _match_dt_h column is present.
    - For each observed rain event (OBS_Rain >= _RAIN_VOTE_THR), scans
      each model's forecast Rain in the ±_RAIN_TOLERANCE_HOURS window
      and records the signed offset between the model's peak-rain hour
      and the observed event hour.
    - timing_score(model) = 1 / (1 + mean(|offsets|))
      • zero offset → score 1.0
      • 1-hour mean error → score 0.50
      • 2-hour mean error → score 0.33
    - Sample-size shrinkage applied (same formula as all other components).
    - Falls back to base_w for non-Rainfall parameters, or when fewer
      than MIN_TIMING_EVENTS rain events are available.
    - Exposes self.timing_weights after calculate_fused_weights() for
      JSON storage and dashboard display.

  Updated blend formula:
    w_final = 0.30 * w_base  +  0.00 * w_leadtime  +  0.25 * w_timing
            + 0.25 * w_regime + 0.20 * w_temporal
    (sum = 1.00, unchanged)

Version: 5.4.0

Key improvements over v4.0.0:
─────────────────────────────
[FIXES — v5.4.0]
  W-1  — WeightConfiguration.validate() now raises ValueError instead of using
          assert.  Python's -O (optimize) flag strips assert at bytecode level,
          silently disabling all validation.  ValueError is never stripped.
  W-2  — _crps_threshold_weighted() marked as REFERENCE IMPLEMENTATION ONLY.
          The production twCRPS path is the inline vectorized form inside
          _objective() in calculate_weights_crps.  The standalone function is
          retained for readability and external callers but is not called in
          the main code path.
  W-4  — calculate_regime_weights() now accepts an optional base_w parameter.
          calculate_fused_weights() passes the already-computed base_w in, so
          the full-dataset optimisation runs only once per call instead of twice.
          For the CRPS method this eliminates a redundant SLSQP + TimeSeriesSplit
          run (~10–30 s on 90-day data).
  CS-1 — Added check_guidance_json_recommendations() to surface missing v5.1+
          fields (leadtime_bracket_weights, regime_bracket_weights,
          temporal_window_weights, spread_calibration) as warnings rather than
          schema errors.  Old v4.x JSONs still pass validation; operators now
          get explicit notice when a feature will silently degrade.

[FIXES — v5.3.0]
  G2  — DM significance test is now circular-aware.  test_significance_vs_equal
         accepts is_circular, uses circular_weighted_mean for the weighted ensemble
         forecast and circular_difference()**2 for squared errors.  For Wind
         Direction, the previously-meaningless DM statistic is now correct.
         calculate_fused_weights passes is_circular through automatically.
  G3  — Ridge and covariance methods now refuse to run on circular parameters.
         calculate_base_weights redirects Wind Direction to 'composite' (or 'crps'
         if configured) when method='ridge' or 'covariance'.  Prevents linear
         arithmetic on circular data from producing nonsense covariance matrices.
  G9  — _average_regime_weights now deduplicates observations before counting.
         The long-format DataFrame had N_models rows per timestamp, inflating every
         regime count by 5×.  Proportions were correct but diagnostics were wrong.
  G10 — _get_weight_for_regime fallback changed from last regime to first regime.
         Anomalous sub-boundary values (e.g. -0.1 mm cleaning artifact) now receive
         the "dry" / "cool" weights rather than "extreme" / "hot" weights.
  G11 — ridge_alpha field removed from WeightConfiguration.  It was documented,
         validated, and never read by any method.  Ridge uses internal CV; other
         methods don't use it.
  G15 — quick_test now exercises calculate_fused_weights end-to-end with the
         correct column names (was silently broken before).
  G_REGIME — Bayesian shrinkage for data-sparse regimes in calculate_regime_weights.
         Old binary pass/fail gate (min_samples×2) silently dropped moderate/heavy/
         extreme rainfall regimes on 30-day operational data.  New approach computes
         weights for every regime that has ≥ REGIME_MIN_SAMPLES_FLOOR (=5) rows,
         then blends toward base_w proportionally: α = min(1, n / shrinkage_n_min).
         All five regimes now appear in the TAF JSON; sparse regimes are
         appropriately conservative rather than absent.
  A1  — _crps_threshold_weighted vectorized via (K, M) broadcast matrix.
         Eliminates the Python for-loop over thresholds.  ~10-20× speedup.
  A2  — _objective in calculate_weights_crps vectorized via (T, K, M) einsum
         for the twCRPS path.  Eliminates the T-iteration Python loop — the
         hottest loop in the codebase called hundreds of times per SLSQP fold.
         ~50-100× speedup.  Standard energy-score CRPS (circular) retains the
         T-loop (vectorizing pairwise circular distance is not worth the memory).
  F4  — crps_threshold_weighted auto-set to False for Temperature and Wind Speed
         in WeightConfiguration.validate().  Previously the docstring said to do
         this but no code enforced it, so threshold-weighted CRPS ran for all
         non-circular parameters regardless.

[DEFAULT CHANGED — v5.3.0]
  method default changed from "composite" to "crps".  CRPS minimisation is
  statistically superior for probabilistic forecasting and is now the
  recommended method for all parameters.  Composite is retained and fully
  functional as an alternative.


  This is the same class of dead-code bug that was fixed for lead-time in v5.1.0.
  calculate_fused_weights() fell back to w_base for BOTH regime and temporal
  components whenever current_obs and temporal_window were None, which is the case
  for every caller. The formula was silently running as:
      w_final = 0.75·w_base + 0.25·w_leadtime   (after the v5.1 lead-time fix)
  Fix mirrors the lead-time pattern:
  - _average_regime_weights(): when current_obs is None, computes all regime
    weight vectors and returns a sample-count-weighted average, where each
    regime's share is proportional to how many observations fall in that regime.
    Regime slices are mutually exclusive so sample-count weighting is semantically
    correct.  For Wind Direction (no regime partitioning), returns {} → falls back
    to base_w, same as before.
  - _average_temporal_weights(): when temporal_window is None, computes all
    temporal window weight vectors and returns an EQUAL-weighted average across
    available windows.  Equal weighting is deliberately chosen here because
    temporal windows are NESTED (24h ⊂ 3d ⊂ 7d ⊂ Month), so sample-count
    weighting would collapse to ≈ Month ≈ base_w and defeat the purpose of
    the temporal component.  Equal weighting ensures each time scale (recent,
    weekly, monthly) contributes equally.
  - self.regime_weights and self.temporal_weights are now exposed after
    calculate_fused_weights() for JSON storage and diagnostics (same pattern
    as self.leadtime_weights in v5.1.0).
  - The formula now genuinely uses all four components as designed:
      w_final = 0.30·w_base + 0.25·w_leadtime + 0.25·w_regime + 0.20·w_temporal

[`threshold` DEAD PARAMETER REMOVED — v5.2.0]
  calculate_fused_weights() accepted a `threshold` parameter that was never
  used inside the function body. All sub-methods (calculate_base_weights,
  calculate_leadtime_weights, calculate_regime_weights, calculate_temporal_weights)
  source their thresholds from PARAMETERS[parameter]["thresholds"] internally.
  The dead parameter has been removed from the signature. The two internal
  callers (calculate_optimal_ensemble() and the dashboard) have been updated
  to match. External callers that previously passed threshold= as a keyword
  argument will receive a TypeError at import time — the fix is simply to
  remove that argument from the call.

[LEAD-TIME WEIGHT FIX — v5.1.0]
  - calculate_fused_weights() previously fell back to w_base whenever lead_hour=None,
    making the 0.25 leadtime_weight contribution identical to w_base (dead code path).
    This meant the formula was silently running as:
        w_final = 0.55·w_base + 0.25·w_regime + 0.20·w_temporal
    Fix: _average_leadtime_weights() computes a sample-size-weighted average across
    all available lead-time brackets when no specific lead_hour is given.
    The formula now genuinely uses all four components.
  - self.leadtime_weights exposed after calculate_fused_weights() so callers can
    inspect per-bracket weights and store them in the guidance JSON.
  - Dashboard and tafor_generator: when a specific lead_hour IS provided (e.g. per
    TAF validity hour), calculate_fused_weights() still picks the exact bracket weight.

[WEIGHTING SYSTEM — CRPS — v5.0.0]
  - New method="crps" added to WeightConfiguration and calculate_base_weights().
  - Two CRPS variants implemented as module-level functions:
      _crps_weighted_ensemble()     — standard energy-score CRPS
                                      (Gneiting & Raftery 2007, eq. 21).
                                      Supports circular parameters (Wind Direction)
                                      via minimum angular distance.
      _crps_threshold_weighted()    — threshold-weighted CRPS
                                      (Gneiting & Ranjan 2011).
                                      Convex in w; directly penalises skill gaps
                                      at high-impact intensity thresholds.
  - calculate_weights_crps() optimises weights by minimising CRPS via SLSQP
    with TimeSeriesSplit (5-fold) cross-validation. Sample-size shrinkage
    applied post-CV, consistent with the composite path.
  - crps_threshold_weighted flag in WeightConfiguration (default True).
    Set to False for Temperature/Wind Speed where thresholds are less operationally
    critical, or for Wind Direction (forced False automatically).

[CLEANUP]
  - circular_correlation() removed — it was dead code (never called anywhere).
    Circular covariance/correlation is handled via the energy-score CRPS term
    when is_circular=True.

[UNCHANGED from v4.0.0]
  - Newey-West HAC DM test, FAR-paradox mitigation, parameter-specific regimes,
    multi-threshold composite, NNLS covariance, TimeSeriesSplit ridge CV,
    sample-size shrinkage, validate_guidance_json schema.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timedelta
import json
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit
from scipy.optimize import nnls
from scipy import stats


# ==============================================================================
# CONFIGURATION & CONSTANTS
# ==============================================================================

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

PARAMETERS = {
    "Temperature":    {"unit": "°C",  "thresholds": [1.0, 2.0, 3.0],        "primary_threshold": 1.0,
                       "verification_step": "1h", "metric": "RMSE"},
    "Dewpoint":       {"unit": "°C",  "thresholds": [1.0, 2.0, 3.0],        "primary_threshold": 1.0,
                       "verification_step": "1h", "metric": "RMSE"},
    "Pressure":       {"unit": "hPa", "thresholds": [1.0, 2.0, 5.0],        "primary_threshold": 1.0,
                       "verification_step": "1h", "metric": "RMSE"},
    "Rainfall":       {"unit": "mm",  "thresholds": [1.5, 5.0, 10.0, 20.0], "primary_threshold": 1.5,
                       "verification_step": "3h", "metric": "CRPS"},
    "Wind Speed":     {"unit": "kt",  "thresholds": [5, 10, 20, 30],        "primary_threshold": 5,
                       "verification_step": "1h", "metric": "RMSE"},
    "Wind Direction": {"unit": "°",   "thresholds": [15, 30, 45],            "primary_threshold": 15,
                       "verification_step": "1h", "metric": "Circular_MAE"},
    "Wind Gust":      {"unit": "kt",  "thresholds": [15, 25, 35],           "primary_threshold": 15,
                       "verification_step": "3h", "metric": "RMSE"},
    "Humidity":       {"unit": "%",   "thresholds": [5, 10, 20],             "primary_threshold": 5,
                       "verification_step": "1h", "metric": "RMSE",
                       "derived": True},
}

TEMPORAL_WINDOWS = {
    "24 Hours": 1,
    "3 Days":   3,
    "7 Days":   7,
    "Month":    30,
}

# Recency-biased blend weights for _average_temporal_weights() [v5.6.0].
# Replaces flat 25/25/25/25 equal-weighting.  24h is noisiest but most
# operationally current; Month is stable but near-climatological.
TEMPORAL_WINDOW_BLEND = {
    "24 Hours": 0.40,
    "3 Days":   0.30,
    "7 Days":   0.20,
    "Month":    0.10,
}

# Minimum rows required to *attempt* any regime-specific training.
# Regimes with fewer rows than this receive base_w directly (fully shrunk).
# Keep very low — shrinkage handles trust, not this gate.
REGIME_MIN_SAMPLES_FLOOR = 5

LEADTIME_BRACKETS = {
    "Day_1":  (0,   24),
    "Day_2":  (24,  48),
    "Day_3":  (48,  72),
    "Day_4+": (72, 120),
}

# Parameter-specific regime boundaries (physically meaningful per variable)
PARAMETER_REGIMES = {
    "Rainfall": {
        "dry":      (0.0,  0.1),
        "light":    (0.1,  5.0),
        "moderate": (5.0,  20.0),
        "heavy":    (20.0, 50.0),
        "extreme":  (50.0, 9999.0),
    },
    "Temperature": {
        "cool":   (0.0,   20.0),
        "mild":   (20.0,  25.0),
        "warm":   (25.0,  30.0),
        "hot":    (30.0,  9999.0),
    },
    "Dewpoint": {
        "dry":   (0.0, 18.0),
        "moist": (18.0, 23.0),
        "humid": (23.0, 26.0),
        "saturated": (26.0, 9999.0),
    },
    "Pressure": None,
    "Wind Speed": {
        "calm":     (0.0,  5.0),
        "light":    (5.0,  15.0),
        "moderate": (15.0, 25.0),
        "strong":   (25.0, 9999.0),
    },
    "Wind Direction": None,
    "Wind Gust": {
        "calm":     (0.0, 10.0),
        "moderate": (10.0, 20.0),
        "strong":   (20.0, 35.0),
        "severe":   (35.0, 9999.0),
    },
    "Humidity": None,
}

REGIME_SOP_COSTS = {
    "Rainfall": {
        "dry":      0.5,
        "light":    1.0,
        "moderate": 2.0,
        "heavy":    4.0,
        "extreme":  3.0,
    },
    "Temperature": {
        "cool": 1.0,
        "mild": 1.0,
        "warm": 1.0,
        "hot":  1.0,
    },
    "Dewpoint": {
        "dry": 1.0,
        "moist": 1.0,
        "humid": 1.0,
        "saturated": 1.0,
    },
    "Wind Speed": {
        "calm":     0.5,
        "light":    1.0,
        "moderate": 2.0,
        "strong":   4.0,
    },
    "Wind Gust": {
        "calm":     0.5,
        "moderate": 1.0,
        "strong":   2.0,
        "severe":   4.0,
    },
}

# Keep legacy alias for any external code that references WEATHER_REGIMES
WEATHER_REGIMES = PARAMETER_REGIMES["Rainfall"]

# Threshold importance weights for multi-threshold composite scoring.
# Rainfall weights are data-derived (log-inverse frequency × SOP cost multiplier)
# from 40,012 h of AWOS observations at WAWP (2021-07-19 → 2026-03-18, 4.66 years).
#
# Method: w(t) = ln(1/P(obs>=t)) × sop_cost(t), normalised.
# log-inverse is used instead of raw-inverse to prevent the 20mm tier from
# dominating via pure rarity — models structurally cannot differentiate skill
# above ~10mm before full piecewise QM is applied, so raw-inverse would assign
# 66% of the twCRPS budget to a zero-gradient term.
#
# Exceedance counts (basis for weights):
#   1.5mm  — 1,183 events  (2.957%)   RA onset gate
#   5.0mm  —   580 events  (1.450%)   moderate RA
#  10.0mm  —   240 events  (0.600%)   +RA trigger  ← peak budget here
#  20.0mm  —    74 events  (0.185%)   very heavy RA
#  50.0mm  —     8 events  (0.020%)   EXCLUDED (< 25 event minimum for stable Brier score)
#
# SOP cost multipliers (BMKG SOP/024/DM/X/2025 priority hierarchy):
#   1.5mm=1.0,  5.0mm=2.0,  10.0mm=4.0,  20.0mm=3.0
#
# Rerun derive_threshold_importance.py annually or when obs archive grows >1 year.
THRESHOLD_IMPORTANCE = {
    "Rainfall":       {1.5: 0.0686, 5.0: 0.165, 10.0: 0.3987, 20.0: 0.3678},  # derived 2026-03-19 — 40,012 h AWOS obs
    "Temperature":    {1.0: 0.40, 2.0: 0.35, 3.0:  0.25},
    "Wind Speed":     {5:   0.15, 10:  0.30, 20:   0.35, 30: 0.20},
    "Wind Direction": {15:  0.40, 30:  0.35, 45:   0.25},
}


# ==============================================================================
# JSON SCHEMA DEFINITION & VALIDATION
# ==============================================================================

GUIDANCE_PARAM_KEYS  = {"rainfall", "temperature", "wind_speed", "wind_direction"}
GUIDANCE_PARAM_REQUIRED = {
    "optimal_weights", "significance", "diurnal_bias", "lookback_metrics"
}
GUIDANCE_LOOKBACK_WINDOWS = {"24 Hours", "3 Days", "7 Days", "Month"}
GUIDANCE_MODEL_METRIC_KEYS = {"RMSE", "MAE", "Bias", "POD", "FAR", "CSI", "HSS", "Sample_Size"}


def validate_guidance_json(data: Any) -> Tuple[bool, List[str]]:
    """
    Validate TAF guidance JSON against the expected schema.

    Returns
    -------
    (is_valid : bool, errors : List[str])
        is_valid  – True only if zero errors were found.
        errors    – Human-readable list of every schema violation found.
    """
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["Root element must be a JSON object (dict)."]

    for key in ("station", "generated", "weight_method", "parameters"):
        if key not in data:
            errors.append(f"Missing top-level key: '{key}'")

    params = data.get("parameters", {})
    if not isinstance(params, dict):
        errors.append("'parameters' must be an object.")
        return False, errors

    for pk in GUIDANCE_PARAM_KEYS:
        if pk not in params:
            errors.append(f"Missing parameter block: '{pk}'")
            continue
        pb = params[pk]
        if not isinstance(pb, dict):
            errors.append(f"Parameter block '{pk}' must be an object.")
            continue
        for req in GUIDANCE_PARAM_REQUIRED:
            if req not in pb:
                errors.append(f"'{pk}' missing required key: '{req}'")

        # Validate weights sum to ~1
        ow = pb.get("optimal_weights", {})
        if isinstance(ow, dict) and ow:
            w_sum = sum(float(v) for v in ow.values() if isinstance(v, (int, float)))
            if abs(w_sum - 1.0) > 0.02:
                errors.append(
                    f"'{pk}.optimal_weights' sum = {w_sum:.4f} (expected ~1.0)"
                )

        # Validate lookback windows
        lm = pb.get("lookback_metrics", {})
        if isinstance(lm, dict):
            for win in GUIDANCE_LOOKBACK_WINDOWS:
                if win not in lm:
                    errors.append(f"'{pk}.lookback_metrics' missing window: '{win}'")

    return len(errors) == 0, errors


# Fields added in v5.1–v5.3 that are strongly recommended but not required for
# backward compatibility.  Missing fields produce warnings (not errors) so that
# old v4.x JSONs still pass validation but callers know which features will degrade.
GUIDANCE_PARAM_RECOMMENDED = {
    "leadtime_bracket_weights": "v5.1 lead-time weights (missing → all lead hours use optimal_weights)",
    "regime_bracket_weights":   "v5.2/v5.3 regime weights (missing → all intensity regimes use optimal_weights)",
    "temporal_window_weights":  "v5.2 temporal weights (missing → all time windows use optimal_weights)",
    "spread_calibration":       "v5.3 spread calibration scalar (missing → confidence uses legacy 1/(1+spread) formula)",
}


def check_guidance_json_recommendations(data: Any) -> List[str]:
    """
    Return a list of human-readable warnings for recommended (not required)
    fields that are absent from the guidance JSON.

    These fields were added in v5.1–v5.3.  Old JSONs will be missing them and
    will silently fall back to less precise behaviour.  This function surfaces
    those degradations explicitly so operators can regenerate the JSON.

    Returns
    -------
    List[str]
        Empty list if all recommended fields are present.
        One warning string per missing field per parameter block.
    """
    warnings: List[str] = []
    if not isinstance(data, dict):
        return warnings

    params = data.get("parameters", {})
    if not isinstance(params, dict):
        return warnings

    for pk in GUIDANCE_PARAM_KEYS:
        pb = params.get(pk)
        if not isinstance(pb, dict):
            continue
        for field, desc in GUIDANCE_PARAM_RECOMMENDED.items():
            if field not in pb or pb[field] is None:
                warnings.append(f"'{pk}' missing recommended field '{field}': {desc}")

    return warnings


# ==============================================================================
# CIRCULAR STATISTICS HELPERS
# ==============================================================================

def circular_mean(angles_deg: np.ndarray) -> float:
    """Circular mean of angles in degrees."""
    rad = np.deg2rad(angles_deg)
    return float(np.rad2deg(np.arctan2(np.sum(np.sin(rad)), np.sum(np.cos(rad)))) % 360)


def circular_std(angles_deg: np.ndarray) -> float:
    """Circular standard deviation in degrees (Mardia & Jupp)."""
    rad = np.deg2rad(angles_deg)
    n = len(angles_deg)
    R = np.sqrt(np.sum(np.sin(rad))**2 + np.sum(np.cos(rad))**2) / n
    R = min(R, 1.0 - 1e-12)   # numerical guard against log(0)
    return float(np.rad2deg(np.sqrt(-2.0 * np.log(R))))


def circular_difference(angle1_deg: np.ndarray, angle2_deg: np.ndarray) -> np.ndarray:
    """Unsigned minimum angular difference ∈ [0°, 180°]."""
    diff = (np.asarray(angle1_deg) - np.asarray(angle2_deg)) % 360
    return np.minimum(diff, 360 - diff)


def circular_weighted_mean(angles_deg: np.ndarray, weights: np.ndarray) -> float:
    """Weighted circular mean of angles in degrees."""
    rad = np.deg2rad(np.asarray(angles_deg, dtype=float))
    w   = np.asarray(weights, dtype=float)
    w   = w / w.sum()
    sin_w = np.sum(w * np.sin(rad))
    cos_w = np.sum(w * np.cos(rad))
    return float(np.rad2deg(np.arctan2(sin_w, cos_w)) % 360)


# ==============================================================================
# CRPS FUNCTIONS  (Hersbach 2000; Gneiting & Raftery 2007; Gneiting & Ranjan 2011)
# ==============================================================================

def _crps_weighted_ensemble(obs: float, forecasts: np.ndarray,
                             weights: np.ndarray,
                             is_circular: bool = False) -> float:
    """
    CRPS of a weighted discrete ensemble against a scalar observation.

    Uses the energy-score decomposition (Gneiting & Raftery 2007, eq. 21):

        CRPS(F_w, y) = E_w|X − y| − ½ E_w|X − X'|

    For a discrete weighted distribution this becomes:

        = Σ_m  w_m |fc_m − y|
          − ½  Σ_m Σ_k  w_m w_k |fc_m − fc_k|

    For circular parameters (Wind Direction), absolute differences are replaced
    by the minimum angular distance ∈ [0°, 180°].

    Note
    ----
    CRPS is *concave* in w (the pairwise-spread term is concave), so minimising
    over the simplex is a valid convex program when the objective is negated.
    In practice SLSQP reliably finds the global minimum from equal-weight starts.
    """
    w = np.asarray(weights, dtype=float)
    w = w / w.sum()
    fc = np.asarray(forecasts, dtype=float)

    if is_circular:
        dist_to_obs  = circular_difference(fc, np.full(len(fc), obs))
        pair_matrix  = circular_difference(fc[:, None], fc[None, :])
    else:
        dist_to_obs  = np.abs(fc - obs)
        pair_matrix  = np.abs(fc[:, None] - fc[None, :])

    term1 = float(np.dot(w, dist_to_obs))
    term2 = 0.5 * float(w @ pair_matrix @ w)
    return term1 - term2


def _crps_threshold_weighted(obs: float, forecasts: np.ndarray,
                              weights: np.ndarray,
                              thresholds: List[float],
                              t_weights: List[float]) -> float:
    """
    Threshold-weighted CRPS (twCRPS) — Gneiting & Ranjan (2011).

    Emphasises performance at high-impact intensity levels by computing a
    weighted average of Brier scores at each threshold z:

        twCRPS = Σ_k  v_k · (F_w(z_k) − 1[y ≥ z_k])²

    where F_w(z) = Σ_m w_m · 1[fc_m ≥ z]  (exceedance probability).

    Unlike the standard CRPS energy form, twCRPS is *convex* in w, so
    SLSQP is guaranteed to find the global minimum.

    [v5.3.0 A1 — vectorized]
    Replaced the Python for-loop over thresholds with a single (K, M)
    broadcast matrix operation.  ~10-20× speedup per call.

    ⚠️  [v5.4.0 W-2 note — REFERENCE IMPLEMENTATION ONLY]
    This function is not called anywhere in the production code path.
    The live twCRPS computation is performed inline inside _objective()
    within calculate_weights_crps(), where the vectorized (T, K, M) form
    avoids repeated re-allocation of the exceedance matrix across timestamps.
    This standalone function is retained as a readable reference and for
    potential use in external callers or unit tests.

    Parameters
    ----------
    thresholds : list of float
        Intensity thresholds (e.g. [0.1, 5.0, 20.0, 50.0] mm for rainfall).
    t_weights : list of float
        Importance weight for each threshold (normalised internally).
    """
    w   = np.asarray(weights,   dtype=float); w  = w  / w.sum()
    tv  = np.asarray(t_weights, dtype=float); tv = tv / tv.sum()
    fc  = np.asarray(forecasts, dtype=float)
    thr = np.asarray(thresholds, dtype=float)   # (K,)

    # (K, M) exceedance matrix — broadcast: fc[None,:] >= thr[:,None]
    exceedance = (fc[None, :] >= thr[:, None]).astype(float)   # (K, M)
    F_z = exceedance @ w                                        # (K,) — weighted exceedance
    O_z = (obs >= thr).astype(float)                           # (K,)
    return float(np.dot(tv, (F_z - O_z) ** 2))


# ==============================================================================
# NEWEY-WEST HAC VARIANCE ESTIMATOR
# ==============================================================================

def _newey_west_variance(d: np.ndarray, max_lag: Optional[int] = None) -> float:
    """
    Heteroskedasticity and Autocorrelation Consistent (HAC) variance estimator.

    Uses the Bartlett (triangle) kernel. If `max_lag` is None, the bandwidth is
    chosen by the Newey-West (1994) data-driven rule:
        L = floor(4 * (n/100)^(2/9))

    This is critical for the Diebold-Mariano test when forecast errors are
    serially correlated (as hourly met errors almost always are). Using plain
    std() understates variance and inflates the DM statistic.
    """
    n = len(d)
    if max_lag is None:
        max_lag = max(1, int(np.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))))

    d_dm = d - d.mean()
    gamma0 = np.dot(d_dm, d_dm) / n
    nw_var = gamma0
    for lag in range(1, max_lag + 1):
        bartlett_w = 1.0 - lag / (max_lag + 1.0)
        gamma_l = np.dot(d_dm[lag:], d_dm[:-lag]) / n
        nw_var += 2.0 * bartlett_w * gamma_l

    # Guard against degenerate (near-zero or negative) variance
    return max(nw_var, 1e-12 * np.var(d))


# ==============================================================================
# DATACLASSES
# ==============================================================================

@dataclass
class SkillMetrics:
    """
    Complete skill metrics for one model over one data slice.

    `threshold_metrics` holds per-threshold dichotomous results so that callers
    can inspect performance at each intensity level without re-computing.
    """
    model_name:       str
    rmse:             float = 0.0
    mae:              float = 0.0
    bias:             float = 0.0
    pod:              float = 0.0    # primary threshold
    far:              float = 0.0    # primary threshold
    csi:              float = 0.0    # primary threshold
    hss:              float = 0.0    # primary threshold
    f1:               float = 0.0    # primary threshold
    mcc:              float = 0.0    # primary threshold
    sample_size:      int   = 0
    threshold_metrics: Dict = field(default_factory=dict)  # {threshold: {POD,FAR,...}}
    had_zero_positive_forecasts: bool = False  # FAR paradox flag
    observed_events:  int   = 0      # number of observed events at primary threshold

    def to_dict(self) -> Dict:
        return {
            "RMSE":            self.rmse,
            "MAE":             self.mae,
            "Bias":            self.bias,
            "POD":             self.pod,
            "FAR":             self.far,
            "CSI":             self.csi,
            "HSS":             self.hss,
            "F1":              self.f1,
            "MCC":             self.mcc,
            "Sample_Size":     self.sample_size,
            "FAR_paradox":     self.had_zero_positive_forecasts,
            "Observed_Events": self.observed_events,
        }


@dataclass
class WeightConfiguration:
    """Configuration for weight calculation."""
    # Metric weights for composite scoring (must sum to 1.0)
    # [v5.6.0] rmse 0.25→0.20: remains dominant but freed budget to HSS/MCC.
    # [v5.6.0] bias 0.10→0.05: diurnal bias already corrected upstream —
    #          double-counting it here over-penalises for a signal already removed.
    # [v5.6.0] hss  0.05→0.10: HSS corrects for random chance; critical at WAWP
    #          where dry hours are ~70–75% of all hours (class-imbalance robustness).
    # [v5.6.0] mcc  0.00→0.05: MCC activated — similar imbalance correction to HSS.
    rmse_weight:  float = 0.20
    mae_weight:   float = 0.15
    bias_weight:  float = 0.05
    pod_weight:   float = 0.20
    far_weight:   float = 0.15
    csi_weight:   float = 0.10
    hss_weight:   float = 0.10
    # F1 is redundant (harmonic mean of POD and Precision ≈ combination of POD/FAR).
    # MCC is valuable but informationally overlaps HSS. Both kept at 0 by default.
    # Set them to non-zero if you want them to contribute.
    f1_weight:    float = 0.00
    mcc_weight:   float = 0.05

    # Strategy fusion weights (must sum to 1.0)
    # [v5.8.0] timing_weight reduced to 0.10. Freed 0.15 redistributed to base+regime (+0.075 each).
    # base 0.30→0.375, timing 0.25→0.10, regime 0.25→0.325; temporal unchanged.
    # NOTE: timing_weight applies ONLY to Rainfall.  For Wind Speed / Wind
    # Direction the timing component falls back to base_w — the freed 0.10
    # budget is redirected to temporal in the blend formula so effective
    # temporal for WS/WD = 0.30 (temporal 0.20 + timing 0.10).
    base_weight:     float = 0.375
    leadtime_weight: float = 0.00   # zeroed on OPERATIONAL data — all lead
                                    # hours fall in Day_1, collapsing to base_w.
                                    # Restore to 0.15 when RELIABILITY data available.
    timing_weight:   float = 0.10   # penalises rainfall timing error.
                                    # Falls back to base_w for non-Rainfall parameters.
    regime_weight:   float = 0.325  # rainfall/wind regime conditioning.
    temporal_weight: float = 0.20   # recency weighting.

    # Algorithm selection
    method: str = "crps"   # 'composite' | 'ridge' | 'covariance' | 'crps'
    # Default changed to 'crps' in v5.3.0 — statistically superior for
    # probabilistic forecasting.  'composite' retained as alternative.

    # Shrinkage: weights collapse toward equal when sample size < shrinkage_n_min.
    # Set to 0 to disable shrinkage entirely.
    shrinkage_n_min: int = 100

    # CRPS options (only used when method == 'crps')
    # When True: uses threshold-weighted CRPS (convex, emphasises extreme events).
    # When False: uses standard energy-score CRPS (better for continuous variables).
    # Automatically set to False for circular parameters (Wind Direction) AND for
    # Temperature and Wind Speed (F4 fix — these are continuous, not threshold-critical).
    crps_threshold_weighted: bool = True

    def validate(self):
        # [v5.4.0 W-1] Use raise ValueError instead of assert.
        # Python's -O (optimize) flag strips all assert statements at bytecode
        # level, silently disabling all validation.  ValueError is never stripped.
        metric_sum = (self.rmse_weight + self.mae_weight + self.bias_weight +
                      self.pod_weight  + self.far_weight  + self.csi_weight  +
                      self.hss_weight  + self.f1_weight   + self.mcc_weight)
        strategy_sum = (self.base_weight + self.leadtime_weight +
                        self.timing_weight + self.regime_weight +
                        self.temporal_weight)
        if abs(metric_sum - 1.0) > 0.001:
            raise ValueError(
                f"Metric weights sum={metric_sum:.4f}, expected 1.0. "
                "Check rmse/mae/bias/pod/far/csi/hss/f1/mcc_weight fields."
            )
        if abs(strategy_sum - 1.0) > 0.001:
            raise ValueError(
                f"Strategy weights sum={strategy_sum:.4f}, expected 1.0. "
                "Check base/leadtime/regime/temporal_weight fields."
            )
        if self.method not in ("composite", "ridge", "covariance", "crps"):
            raise ValueError(
                f"Unknown method: '{self.method}'. "
                "Valid options: 'composite', 'ridge', 'covariance', 'crps'."
            )


# ==============================================================================
# MAIN WEIGHTER CLASS
# ==============================================================================

class AdvancedEnsembleWeighter:
    """
    Ensemble weighter with composite, ridge, and covariance methods.

    Usage
    -----
    weighter = AdvancedEnsembleWeighter(MODELS, WeightConfiguration())
    weights  = weighter.calculate_fused_weights(df, parameter="Rainfall", ...)
    """

    def __init__(self, models: List[str] = MODELS,
                 config: Optional[WeightConfiguration] = None):
        self.models = models
        self.config = config or WeightConfiguration()
        self.config.validate()

        self.base_weights:       Dict = {}
        self.leadtime_weights:   Dict = {}
        self.timing_weights:     Dict = {}   # [v5.5.1] per-event timing accuracy weights
        self.regime_weights:     Dict = {}
        self.temporal_weights:   Dict = {}
        self.skill_metrics:      Dict = {}
        self.significance:       Dict = {}
        # Populated by calculate_fused_weights() / compute_spread_calibration().
        # Key = parameter name; value = historical ensemble MAE used to convert
        # raw ensemble spread → a calibrated probability (see
        # generate_ensemble_forecast for the formula).
        self.spread_calibration: Dict[str, float] = {}
        self.weight_diagnostics: Dict[str, float] = {}  # [v5.8.0] weight spread diagnostics (max - min)

    # ==========================================================================
    # CONTINUOUS METRICS
    # ==========================================================================

    def _is_circular(self, param: str) -> bool:
        return param == "Wind Direction"

    def calculate_continuous_metrics(self, forecasts: np.ndarray,
                                     observations: np.ndarray,
                                     is_circular: bool = False) -> Dict[str, float]:
        """Compute RMSE, MAE, Bias (circular-aware)."""
        if is_circular:
            errors      = circular_difference(forecasts, observations)
            rmse        = float(np.sqrt(np.mean(errors**2)))
            mae         = float(np.mean(errors))
            signed_diff = (forecasts - observations) % 360
            signed_diff = np.where(signed_diff > 180, signed_diff - 360, signed_diff)
            bias        = float(np.mean(signed_diff))
        else:
            errors = forecasts - observations
            rmse   = float(np.sqrt(np.mean(errors**2)))
            mae    = float(np.mean(np.abs(errors)))
            bias   = float(np.mean(errors))

        return {
            "RMSE":      rmse,
            "MAE":       mae,
            "Bias":      bias,
            "Max_Error": float(np.max(np.abs(errors))),
            "Std_Error": float(np.std(errors)),
        }

    # ==========================================================================
    # DICHOTOMOUS METRICS (single threshold, circular-aware)
    # ==========================================================================

    def calculate_dichotomous_metrics(self, forecasts: np.ndarray,
                                      observations: np.ndarray,
                                      threshold: float = 0.1,
                                      is_circular: bool = False) -> Dict[str, float]:
        """
        Compute POD, FAR, CSI, HSS, F1, MCC for a binary event defined by
        `threshold`.

        For circular parameters (Wind Direction), the "event" is defined as the
        forecast angular error being ≤ threshold (in degrees) — i.e. the model
        gets the direction right within ±threshold.

        FAR paradox handling
        --------------------
        If a model NEVER forecasts an event (hits + false_alarms = 0), a naive
        formula returns FAR = 0 — which would appear as a perfect FAR score.
        We instead return FAR = 1.0 in that case and set `far_paradox = True`.
        """
        if is_circular:
            # Event = angular error ≤ threshold degrees
            errors   = circular_difference(forecasts, observations)
            f_binary = (errors <= threshold).astype(int)
            o_binary = np.ones(len(observations), dtype=int)   # observation "event" = any direction exists
            # Re-define: f_binary = model within threshold, o_binary = 1 always
            # (we're scoring "directional accuracy" as the event)
            hits              = int(np.sum(f_binary))
            misses            = int(np.sum(1 - f_binary))
            false_alarms      = 0
            correct_negatives = 0
            far_paradox       = False
        else:
            f_binary          = (forecasts   >= threshold).astype(int)
            o_binary          = (observations >= threshold).astype(int)
            hits              = int(np.sum((f_binary == 1) & (o_binary == 1)))
            misses            = int(np.sum((f_binary == 0) & (o_binary == 1)))
            false_alarms      = int(np.sum((f_binary == 1) & (o_binary == 0)))
            correct_negatives = int(np.sum((f_binary == 0) & (o_binary == 0)))
            far_paradox       = (hits + false_alarms) == 0

        total = hits + misses + false_alarms + correct_negatives

        # POD
        pod = hits / (hits + misses) if (hits + misses) > 0 else 0.0

        # FAR (with paradox mitigation)
        if is_circular:
            far = 0.0   # not applicable in the angular accuracy framing
        elif far_paradox:
            far = 1.0   # model never forecasts event → penalise fully
        else:
            far = false_alarms / (hits + false_alarms)

        # CSI
        csi_denom = hits + misses + false_alarms
        csi = hits / csi_denom if csi_denom > 0 else 0.0

        # HSS
        if total > 0:
            expected_correct = (
                (hits + misses) * (hits + false_alarms) +
                (correct_negatives + misses) * (correct_negatives + false_alarms)
            ) / total
            hss_denom = total - expected_correct
            hss = (hits + correct_negatives - expected_correct) / hss_denom if hss_denom > 0 else 0.0
        else:
            hss = 0.0

        # Precision, Recall, F1
        precision = hits / (hits + false_alarms) if (hits + false_alarms) > 0 and not far_paradox else 0.0
        recall    = pod
        f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        # MCC
        mcc_denom = np.sqrt(
            float((hits + misses) * (hits + false_alarms) *
                  (correct_negatives + misses) * (correct_negatives + false_alarms))
        )
        mcc = float(hits * correct_negatives - misses * false_alarms) / mcc_denom if mcc_denom > 0 else 0.0

        return {
            "POD":          pod,
            "FAR":          far,
            "FAR_paradox":  far_paradox,
            "CSI":          csi,
            "HSS":          hss,
            "F1":           f1,
            "MCC":          mcc,
            "Hits":         hits,
            "Misses":       misses,
            "False_Alarms": false_alarms,
            "Correct_Neg":  correct_negatives,
        }

    # ==========================================================================
    # MULTI-THRESHOLD DICHOTOMOUS METRICS
    # ==========================================================================

    def calculate_dichotomous_metrics_multithreshold(
            self, forecasts: np.ndarray, observations: np.ndarray,
            thresholds: List[float], is_circular: bool = False) -> Dict[float, Dict]:
        """Return dichotomous metrics at every threshold."""
        return {
            t: self.calculate_dichotomous_metrics(forecasts, observations, t, is_circular)
            for t in thresholds
        }

    # ==========================================================================
    # AGGREGATE ALL METRICS
    # ==========================================================================

    def calculate_all_metrics(self, df: pd.DataFrame,
                              forecast_col: str = "forecast",
                              obs_col: str = "obs",
                              model_col: str = "Model",
                              parameter: str = "Rainfall",
                              is_circular: bool = False,
                              min_samples: int = 10) -> Dict[str, SkillMetrics]:
        """
        Calculate all metrics for every model in a DataFrame.

        Parameters
        ----------
        parameter : str
            Used to look up the full threshold list from PARAMETERS.
        min_samples : int
            Models with fewer samples than this receive a sentinel SkillMetrics
            (rmse=999) so they do not distort ensemble weights.
        """
        param_cfg  = PARAMETERS.get(parameter, PARAMETERS["Rainfall"])
        thresholds = param_cfg["thresholds"]
        primary_t  = param_cfg["primary_threshold"]

        metrics_by_model: Dict[str, SkillMetrics] = {}
        for model in self.models:
            model_data = df[df[model_col] == model]
            n = len(model_data)
            if n < min_samples:
                metrics_by_model[model] = SkillMetrics(
                    model_name=model, rmse=999.0, sample_size=n
                )
                continue

            fc = model_data[forecast_col].values.astype(float)
            ob = model_data[obs_col].values.astype(float)

            cont       = self.calculate_continuous_metrics(fc, ob, is_circular)
            thresh_all = self.calculate_dichotomous_metrics_multithreshold(
                fc, ob, thresholds, is_circular
            )
            primary    = thresh_all[primary_t]

            metrics_by_model[model] = SkillMetrics(
                model_name=model,
                rmse=cont["RMSE"], mae=cont["MAE"], bias=cont["Bias"],
                pod=primary["POD"], far=primary["FAR"],
                csi=primary["CSI"], hss=primary["HSS"],
                f1=primary["F1"],   mcc=primary["MCC"],
                sample_size=n,
                threshold_metrics=thresh_all,
                had_zero_positive_forecasts=primary["FAR_paradox"],
                observed_events=int(primary["Hits"] + primary["Misses"]),
            )
        return metrics_by_model

    # ==========================================================================
    # COMPOSITE SCORE
    # ==========================================================================

    def calculate_composite_score(self, metrics: SkillMetrics,
                                  parameter: str = "Rainfall") -> float:
        """
        Weighted composite skill score.

        Dichotomous sub-scores use a threshold-importance-weighted average across
        all thresholds (not just the primary threshold).  This ensures that skill
        at detecting extreme events is rewarded proportionally to their importance.
        """
        # Continuous sub-scores (higher = better, all ∈ [0, 1])
        rmse_score = 1.0 / (1.0 + metrics.rmse)
        mae_score  = 1.0 / (1.0 + metrics.mae)
        bias_score = 1.0 / (1.0 + abs(metrics.bias))

        # Threshold-importance weights for dichotomous sub-scores
        importance = THRESHOLD_IMPORTANCE.get(parameter, {})
        thresh_mets = metrics.threshold_metrics

        if thresh_mets and importance:
            # Accumulate importance-weighted averages
            i_total = sum(importance.values())
            pod_score = csi_score = hss_score = f1_score = mcc_score = 0.0
            far_sum   = 0.0
            for t, im in importance.items():
                tm = thresh_mets.get(t, {})
                w  = im / i_total
                pod_score += w * tm.get("POD", 0.0)
                # FAR: models with paradox get penalised (FAR forced to 1 already)
                far_sum   += w * tm.get("FAR", metrics.far)
                csi_score += w * tm.get("CSI", 0.0)
                hss_score += w * ((tm.get("HSS", 0.0) + 1.0) / 2.0)
                f1_score  += w * tm.get("F1",  0.0)
                mcc_score += w * ((tm.get("MCC", 0.0) + 1.0) / 2.0)
            far_score = 1.0 - far_sum
        else:
            # Fall back to primary-threshold values
            pod_score = metrics.pod
            far_score = 1.0 - metrics.far
            csi_score = metrics.csi
            hss_score = (metrics.hss + 1.0) / 2.0
            f1_score  = metrics.f1
            mcc_score = (metrics.mcc + 1.0) / 2.0

        composite = (
            self.config.rmse_weight * rmse_score +
            self.config.mae_weight  * mae_score  +
            self.config.bias_weight * bias_score +
            self.config.pod_weight  * pod_score  +
            self.config.far_weight  * far_score  +
            self.config.csi_weight  * csi_score  +
            self.config.hss_weight  * hss_score  +
            self.config.f1_weight   * f1_score   +
            self.config.mcc_weight  * mcc_score
        )
        return float(composite)

    # ==========================================================================
    # SAMPLE-SIZE SHRINKAGE
    # ==========================================================================

    def _apply_shrinkage(self, scores: Dict[str, float],
                         sample_sizes: Dict[str, int]) -> Dict[str, float]:
        """
        Shrink composite-based weights toward equal weighting when sample sizes
        are small.  Uses a linear shrinkage factor:

            alpha_m = min(1, n_m / shrinkage_n_min)
            w_m     = alpha_m * score_m + (1 - alpha_m) * equal_w

        When n_m >= shrinkage_n_min, the score is used as-is.
        When n_m << shrinkage_n_min, the weight collapses toward 1/M.
        """
        if self.config.shrinkage_n_min <= 0:
            return scores

        n_min   = float(self.config.shrinkage_n_min)
        n_mod   = len(self.models)
        equal_w = 1.0 / n_mod if n_mod > 0 else 0.0
        shrunk  = {}
        for model in scores:
            n     = float(sample_sizes.get(model, 0))
            alpha = min(1.0, n / n_min)
            shrunk[model] = alpha * scores[model] + (1.0 - alpha) * equal_w
        return shrunk

    # ==========================================================================
    # RIDGE REGRESSION (with TimeSeriesSplit CV)
    # ==========================================================================

    def calculate_weights_ridge(self, df: pd.DataFrame,
                                 forecast_col: str = "forecast",
                                 obs_col: str = "obs",
                                 model_col: str = "Model") -> Dict[str, float]:
        """
        Regularised regression weights (NOAA-style).

        Improvement over v3.x: alpha is selected by TimeSeriesSplit cross-
        validation (n_splits=5) rather than in-sample MSE, which prevents
        overfitting to the training period.

        Constraints: positive=True ensures non-negative weights; weights are
        normalised to sum to 1 after fitting.
        """
        pivot = df.pivot_table(index="WITA_Target", columns=model_col,
                               values=forecast_col, aggfunc="first")
        obs = df.groupby("WITA_Target")[obs_col].first()
        idx = pivot.index.intersection(obs.index)
        if len(idx) < 20:
            return self._equal_weights()

        X = pivot.loc[idx].values
        y = obs.loc[idx].values

        # Drop rows with any NaN
        mask = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
        X, y = X[mask], y[mask]
        if len(X) < 20:
            return self._equal_weights()

        alphas = np.logspace(-3, 3, 13)
        tscv   = TimeSeriesSplit(n_splits=5)
        best_alpha, best_cv_score = alphas[0], -np.inf

        for alpha in alphas:
            cv_scores = []
            for train_idx, val_idx in tscv.split(X):
                X_tr, X_val = X[train_idx], X[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
                try:
                    ridge = Ridge(alpha=alpha, fit_intercept=False, positive=True)
                    ridge.fit(X_tr, y_tr)
                    w    = np.maximum(ridge.coef_, 0)
                    w    = w / w.sum() if w.sum() > 0 else np.ones(X.shape[1]) / X.shape[1]
                    pred = X_val @ w
                    cv_scores.append(-float(np.mean((pred - y_val)**2)))
                except Exception:
                    cv_scores.append(-np.inf)
            mean_cv = np.mean(cv_scores) if cv_scores else -np.inf
            if mean_cv > best_cv_score:
                best_cv_score = mean_cv
                best_alpha    = alpha

        ridge_final = Ridge(alpha=best_alpha, fit_intercept=False, positive=True)
        ridge_final.fit(X, y)
        w = np.maximum(ridge_final.coef_, 0)
        if w.sum() > 0:
            w = w / w.sum()
        else:
            return self._equal_weights()

        # Map back to model names (pivot columns may not be in MODELS order)
        return dict(zip(pivot.columns, w))

    # ==========================================================================
    # COVARIANCE-BASED OPTIMAL WEIGHTS (NNLS)
    # ==========================================================================

    def calculate_weights_covariance(self, df: pd.DataFrame,
                                      forecast_col: str = "forecast",
                                      obs_col: str = "obs",
                                      model_col: str = "Model") -> Dict[str, float]:
        """
        Minimum-variance ensemble weights via error covariance matrix.

        Improvement over v3.x: replaces post-hoc truncation of negative weights
        (which breaks the optimality property silently) with NNLS constrained
        optimisation, which finds the true minimum-variance non-negative weights.

        The unconstrained solution w = Σ⁻¹1 / (1ᵀΣ⁻¹1) is theoretically optimal
        but can produce negative weights when models are correlated. NNLS solves:

            min_w ‖Σ^(1/2) w − e‖²   s.t. w ≥ 0

        followed by normalisation, giving the closest feasible non-negative point
        to the minimum-variance solution.
        """
        errors_by_model: Dict[str, np.ndarray] = {}
        for model in self.models:
            md = df[df[model_col] == model]
            if len(md) == 0:
                errors_by_model[model] = np.array([])
            else:
                errors_by_model[model] = (
                    md[forecast_col].values.astype(float) -
                    md[obs_col].values.astype(float)
                )

        min_len = min((len(e) for e in errors_by_model.values() if len(e) > 0), default=0)
        if min_len < 5:
            return self._equal_weights()

        E     = np.column_stack([errors_by_model[m][:min_len] for m in self.models])
        Sigma = np.cov(E.T) + np.eye(len(self.models)) * 1e-6

        # Cholesky decomposition for NNLS formulation: Σ = LLᵀ
        try:
            L = np.linalg.cholesky(Sigma)
        except np.linalg.LinAlgError:
            # Sigma not positive definite despite regularisation — use equal weights
            return self._equal_weights()

        # Solve NNLS: min ‖L w − L⁻¹ 1‖
        # Equivalent to min ‖Σ^(1/2) w − target‖ where target = Σ^(-1/2) 1
        ones   = np.ones(len(self.models))
        target = np.linalg.solve(L.T, ones)   # = L^{-T} 1
        w_nnls, _ = nnls(L, target)

        if w_nnls.sum() > 0:
            w_nnls = w_nnls / w_nnls.sum()
        else:
            return self._equal_weights()

        return dict(zip(self.models, w_nnls))

    # ==========================================================================
    # CRPS-BASED WEIGHTS (Hersbach 2000 / Gneiting & Raftery 2007)
    # ==========================================================================

    def calculate_weights_crps(self, df: pd.DataFrame,
                                forecast_col: str = "forecast",
                                obs_col: str = "obs",
                                model_col: str = "Model",
                                parameter: str = "Rainfall",
                                is_circular: bool = False) -> Dict[str, float]:
        """
        Derive model weights by minimising CRPS on held-out data.

        Two CRPS variants are available, selected via `config.crps_threshold_weighted`:

        Threshold-weighted CRPS (default for non-circular parameters)
        ─────────────────────────────────────────────────────────────
        Gneiting & Ranjan (2011).  Computes a weighted average of Brier scores
        across the parameter's intensity thresholds, with higher-intensity
        thresholds receiving proportionally more weight (from THRESHOLD_IMPORTANCE).

            twCRPS = Σ_k  v_k · (P_w(fc ≥ z_k) − 1[y ≥ z_k])²

        This form is *convex* in w, so SLSQP always finds the global minimum.
        It is recommended for Rainfall and other threshold-critical parameters
        because it directly rewards skill at detecting high-impact events.

        Standard energy-score CRPS (circular parameters / continuous variables)
        ────────────────────────────────────────────────────────────────────────
        Gneiting & Raftery (2007), eq. 21.  For Wind Direction the pairwise
        absolute differences are replaced by minimum angular distances.

            CRPS = E_w|X − y| − ½ E_w|X − X'|

        Cross-validation
        ─────────────────
        Weights are selected by TimeSeriesSplit (5-fold) cross-validation:
        each fold fits weights on training timestamps and scores them on the
        held-out block; the set with the best average held-out CRPS is kept.

        Shrinkage
        ──────────
        The selected weights are then linearly shrunk toward equal weights
        proportional to sample size, exactly as in the composite path.

        [FIX v5.1.0 — Fold averaging instead of single-fold selection]
        ────────────────────────────────────────────────────────────────
        Previously the loop kept only the single fold whose validation CRPS was
        lowest, discarding all other fold-optimised weight vectors.  This is
        high-variance: one "lucky" validation block (e.g. a dry spell with zero
        skill differentiation) could dominate the final weights.

        Fix: collect all fold-optimal weight vectors and their validation CRPS
        scores, then form a soft-min (Boltzmann) weighted average:

            fold_weight_k  ∝  exp(−score_k / τ)

        where τ = std(scores) (natural temperature scale).  Folds with lower
        validation CRPS receive proportionally more influence, but every fold
        contributes.  A final re-fit on the full dataset using the averaged
        weights as a warm start ensures the final weights honour all the data.
        """
        from scipy.optimize import minimize as sp_minimize

        # Build aligned (T × M) matrix
        pivot = df.pivot_table(index="WITA_Target", columns=model_col,
                               values=forecast_col, aggfunc="first")
        obs_s = df.groupby("WITA_Target")[obs_col].first()
        idx   = pivot.index.intersection(obs_s.index)
        if len(idx) < 20:
            return self._equal_weights()

        X = pivot.loc[idx].values.astype(float)   # (T, M)
        y = obs_s.loc[idx].values.astype(float)   # (T,)
        mask = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
        X, y = X[mask], y[mask]
        if len(X) < 20:
            return self._equal_weights()

        M         = X.shape[1]
        col_names = list(pivot.columns)

        # Threshold importance for twCRPS (ignored for circular / standard CRPS)
        param_cfg  = PARAMETERS.get(parameter, PARAMETERS["Rainfall"])
        thresholds = param_cfg["thresholds"]
        t_weights  = [THRESHOLD_IMPORTANCE.get(parameter, {}).get(t, 1.0 / len(thresholds))
                      for t in thresholds]

        # [F4 fix v5.3.0] — auto-override crps_threshold_weighted for
        # Temperature and Wind Speed: these are continuous variables where
        # threshold decomposition adds no benefit over the energy-score form.
        # Circular parameters (WD) already force use_tw=False.
        force_energy_score = parameter in ("Temperature", "Wind Speed")
        use_tw = (self.config.crps_threshold_weighted
                  and not is_circular
                  and not force_energy_score)

        # Pre-compute numpy arrays for vectorized objective
        thr_arr = np.asarray(thresholds, dtype=float)    # (K,)
        tv_arr  = np.asarray(t_weights,  dtype=float)
        tv_arr  = tv_arr / tv_arr.sum()                   # (K,) normalised

        def _get_objective_fn(X_data: np.ndarray, y_data: np.ndarray):
            if use_tw:
                # Precompute exceedance matrices once for this split/fold
                exc = (X_data[:, None, :] >= thr_arr[None, :, None]).astype(float)
                O_z = (y_data[:, None] >= thr_arr[None, :]).astype(float)
                
                def _obj(w_raw: np.ndarray) -> float:
                    w = np.abs(w_raw)
                    w = w / w.sum()
                    F_z = exc @ w                                     # (T, K)
                    return float(np.dot(((F_z - O_z) ** 2), tv_arr).mean())
                return _obj
            else:
                # Precompute distance to observations and mean pairwise distance once for this split/fold
                if is_circular:
                    dist_to_obs = circular_difference(X_data, y_data[:, None])
                    pair_matrix = circular_difference(X_data[:, :, None], X_data[:, None, :])
                else:
                    dist_to_obs = np.abs(X_data - y_data[:, None])
                    pair_matrix = np.abs(X_data[:, :, None] - X_data[:, None, :])
                
                dist_to_obs_mean = dist_to_obs.mean(axis=0) # (M,)
                P_mean = pair_matrix.mean(axis=0)           # (M, M)
                
                def _obj(w_raw: np.ndarray) -> float:
                    w = np.abs(w_raw)
                    w = w / w.sum()
                    term1 = dist_to_obs_mean.dot(w)
                    term2 = 0.5 * w.dot(P_mean).dot(w)
                    return term1 - term2
                return _obj

        # ── TimeSeriesSplit cross-validation ─────────────────────────────────
        # Collect EVERY fold's optimal weights and its validation CRPS score.
        # We will combine them via soft-min (Boltzmann) weighting rather than
        # selecting the single best fold (which has high variance).
        # ─────────────────────────────────────────────────────────────────────
        tscv         = TimeSeriesSplit(n_splits=5)
        constraints  = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
        bounds       = [(0.0, 1.0)] * M
        w0           = np.ones(M) / M

        fold_weights: List[np.ndarray] = []
        fold_scores:  List[float]      = []

        for train_idx, val_idx in tscv.split(X):
            X_tr, y_tr   = X[train_idx], y[train_idx]
            X_val, y_val = X[val_idx],   y[val_idx]

            if len(X_tr) < 10 or len(X_val) < 5:
                continue   # skip degenerate splits

            obj_tr  = _get_objective_fn(X_tr, y_tr)
            obj_val = _get_objective_fn(X_val, y_val)

            result = sp_minimize(
                obj_tr,
                w0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 300, "ftol": 1e-9},
            )

            w_cand    = np.abs(result.x); w_cand = w_cand / w_cand.sum()
            val_crps  = obj_val(w_cand)

            fold_weights.append(w_cand)
            fold_scores.append(val_crps)

        if not fold_weights:
            # [v5.8.1 fallback] If cross-validation folds fail (due to small sample size),
            # fall back to a full-sample optimization on all available data points
            # instead of collapsing directly to equal weights.
            obj_full = _get_objective_fn(X, y)
            result = sp_minimize(
                obj_full,
                w0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 300, "ftol": 1e-9},
            )
            w_cand = np.abs(result.x)
            if w_cand.sum() > 0:
                return dict(zip(col_names, w_cand / w_cand.sum()))
            return self._equal_weights()

        # ── Soft-min (Boltzmann) weighted average across folds ────────────────
        # Lower validation CRPS → higher fold weight.
        # Temperature τ = std(scores); if all scores are identical τ → ε and
        # the average collapses to a uniform blend (safe fallback).
        scores_arr = np.array(fold_scores)
        tau        = max(float(np.std(scores_arr)), 1e-9)
        log_w      = -scores_arr / tau
        log_w     -= log_w.max()                     # numerical stability
        agg_w      = np.exp(log_w)
        agg_w     /= agg_w.sum()

        # Weighted average of fold model-weight vectors
        averaged_w = np.zeros(M)
        for fw, aw in zip(fold_weights, agg_w):
            averaged_w += aw * fw
        averaged_w = np.abs(averaged_w)
        averaged_w = averaged_w / averaged_w.sum()

        # ── Final refit on full dataset using averaged weights as warm start ──
        # This gives a single coherent solution consistent with all the data,
        # while the CV averaging provides the stable initialisation point.
        obj_full = _get_objective_fn(X, y)
        result_full = sp_minimize(
            obj_full,
            averaged_w,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 500, "ftol": 1e-10},
        )
        best_w = np.abs(result_full.x)
        if best_w.sum() > 0:
            best_w = best_w / best_w.sum()
        else:
            best_w = averaged_w   # fallback to averaged result

        # ── Sample-size shrinkage (mirrors the composite path) ────────────────
        n_total = len(X)
        n_min   = float(self.config.shrinkage_n_min)
        alpha   = min(1.0, n_total / n_min)
        eq_w    = np.ones(M) / M
        best_w  = alpha * best_w + (1.0 - alpha) * eq_w
        best_w  = best_w / best_w.sum()

        return dict(zip(col_names, best_w))

    def calculate_base_weights(self, df: pd.DataFrame,
                                forecast_col: str = "forecast",
                                obs_col: str = "obs",
                                model_col: str = "Model",
                                parameter: str = "Rainfall",
                                is_circular: bool = False,
                                min_samples: int = 10) -> Dict[str, float]:
        """Base weights using the configured method.

        [v5.3.0 G3 fix]
        Ridge and covariance are linear methods.  For circular parameters
        (Wind Direction) they compute X @ w and error covariances from
        linear arithmetic — which is geometrically wrong on angles.  A
        350°/10° pair produces error=340 instead of error=20.

        Guard: when is_circular=True and method is 'ridge' or 'covariance',
        silently fall back to 'crps' (first choice) or 'composite' so that
        only circular-aware methods run on Wind Direction data.
        """
        effective_method = self.config.method
        if is_circular and effective_method in ("ridge", "covariance"):
            effective_method = "crps"   # circular-safe CRPS uses angular distance

        if effective_method == "ridge":
            return self.calculate_weights_ridge(df, forecast_col, obs_col, model_col)
        elif effective_method == "covariance":
            return self.calculate_weights_covariance(df, forecast_col, obs_col, model_col)
        elif effective_method == "crps":
            return self.calculate_weights_crps(
                df, forecast_col, obs_col, model_col,
                parameter=parameter, is_circular=is_circular
            )
        else:   # composite
            metrics = self.calculate_all_metrics(
                df, forecast_col, obs_col, model_col,
                parameter=parameter, is_circular=is_circular,
                min_samples=min_samples
            )
            raw_scores = {
                m: self.calculate_composite_score(metrics[m], parameter)
                for m in self.models
            }
            sample_sizes = {m: metrics[m].sample_size for m in self.models}
            scores = self._apply_shrinkage(raw_scores, sample_sizes)
            return self._normalise(scores)

    # ==========================================================================
    # LEAD-TIME WEIGHTS
    # ==========================================================================

    def calculate_leadtime_weights(self, df: pd.DataFrame,
                                    lead_col: str = "Lead_Hour",
                                    forecast_col: str = "forecast",
                                    obs_col: str = "obs",
                                    model_col: str = "Model",
                                    parameter: str = "Rainfall",
                                    is_circular: bool = False,
                                    min_samples: int = 50) -> Dict[str, Dict[str, float]]:
        lt_weights: Dict[str, Dict[str, float]] = {}
        for bracket, (lo, hi) in LEADTIME_BRACKETS.items():
            sub = df[(df[lead_col] >= lo) & (df[lead_col] < hi)]
            if len(sub) >= min_samples:
                lt_weights[bracket] = self.calculate_base_weights(
                    sub, forecast_col, obs_col, model_col,
                    parameter=parameter, is_circular=is_circular,
                    min_samples=min_samples // 5
                )
        return lt_weights

    # ==========================================================================
    # REGIME WEIGHTS (parameter-specific)
    # ==========================================================================

    def calculate_regime_weights(self, df: pd.DataFrame,
                                  obs_col: str = "obs",
                                  forecast_col: str = "forecast",
                                  model_col: str = "Model",
                                  parameter: str = "Rainfall",
                                  is_circular: bool = False,
                                  min_samples: int = 100,
                                  base_w: Optional[Dict[str, float]] = None,
                                  ) -> Dict[str, Dict[str, float]]:
        """
        Compute per-regime weights with Bayesian shrinkage for data-sparse regimes.

        [v5.3.0 G_REGIME fix]
        Old behaviour: binary pass/fail gate at min_samples×2 (≈100 rows).
        On 30 days of OPERATIONAL data, moderate/heavy/extreme rainfall events
        never crossed this threshold → completely absent from regime_bracket_weights
        → the entire operational purpose of regime weighting (capturing model skill
        during high-impact weather) was defeated.

        New behaviour: every regime with ≥ REGIME_MIN_SAMPLES_FLOOR (=5) rows
        gets trained weights.  Those weights are then shrunk toward base_w via
        the same linear formula already used in _apply_shrinkage:

            α = min(1, n / shrinkage_n_min)
            w_regime = α × w_trained + (1 - α) × w_base

        Regimes with abundant data (n ≫ shrinkage_n_min) behave exactly as before.
        Sparse regimes (extreme events) are conservative — blended toward the full-
        dataset estimate — but they are present and non-zero in the JSON.

        For Wind Direction (no regime partitioning), returns {} as before.

        [v5.4.0 W-4 fix]
        base_w is now an optional parameter.  When the caller already has base_w
        (e.g. calculate_fused_weights computes it before calling this method), it
        should be passed in to avoid a redundant full-dataset optimisation.  If
        None (direct call), base_w is computed internally as before.
        """
        regimes = PARAMETER_REGIMES.get(parameter)
        if regimes is None:
            return {}   # Wind Direction — no regime partitioning

        # [W-4] Compute base weights only if not supplied by the caller.
        if base_w is None:
            base_w = self.calculate_base_weights(
                df, forecast_col, obs_col, model_col,
                parameter=parameter, is_circular=is_circular,
            )

        regime_weights: Dict[str, Dict[str, float]] = {}
        n_min = float(self.config.shrinkage_n_min)

        for regime, (lo, hi) in regimes.items():
            sub = df[(df[obs_col] >= lo) & (df[obs_col] < hi)]
            n   = len(sub)

            if n < REGIME_MIN_SAMPLES_FLOOR:
                # Truly no data — store base_w directly (fully shrunk, α=0)
                regime_weights[regime] = dict(base_w)
                continue

            # Train on available subset
            trained_w = self.calculate_base_weights(
                sub, forecast_col, obs_col, model_col,
                parameter=parameter, is_circular=is_circular,
                min_samples=max(REGIME_MIN_SAMPLES_FLOOR, min_samples // 10),
            )

            # Shrink toward base_w proportionally to sample size
            alpha = min(1.0, n / n_min)
            shrunk: Dict[str, float] = {
                m: alpha * trained_w.get(m, base_w.get(m, 0.0))
                   + (1.0 - alpha) * base_w.get(m, 0.0)
                for m in self.models
            }
            total = sum(shrunk.values())
            regime_weights[regime] = (
                {m: v / total for m, v in shrunk.items()}
                if total > 0 else dict(base_w)
            )

        return regime_weights

    # ==========================================================================
    # TEMPORAL WEIGHTS
    # ==========================================================================
    # TIMING WEIGHTS  [v5.5.1]
    # ==========================================================================

    def calculate_timing_weights(self, df: pd.DataFrame,
                                  forecast_col: str = "Rain",
                                  obs_col:      str = "OBS_Rain",
                                  model_col:    str = "Model",
                                  parameter:    str = "Rainfall",
                                  base_w:       Optional[Dict[str, float]] = None,
                                  ) -> Dict[str, float]:
        """
        Derive per-model weights from rainfall timing accuracy.

        Only active for Rainfall.  Returns base_w unchanged for all other
        parameters (Wind Speed, Temperature, Wind Direction are not affected).

        Algorithm
        ---------
        For each observed rain event (OBS_Rain >= _RAIN_VOTE_THR mm):
            For each model:
                Scan that model's forecast Rain values within the
                ±_RAIN_TOLERANCE_HOURS window around the obs timestamp.
                Record: offset_h = peak_forecast_hour − obs_hour  (signed)

        timing_score(model) = 1 / (1 + mean(|offset_h|))
            • offset = 0 h  →  score = 1.00  (perfect timing)
            • offset = 1 h  →  score = 0.50
            • offset = 2 h  →  score = 0.33  (at tolerance boundary)

        Sample-size shrinkage: α = min(1, n_events / shrinkage_n_min).
        Below MIN_TIMING_EVENTS events the method returns base_w silently.

        Exposes self.timing_weights = {event_count, per_model_offsets} after
        the call for JSON storage and dashboard display.

        Why peak-intensity not _match_dt_h
        -----------------------------------
        _match_dt_h records the offset between the MATCHED forecast slot and
        the obs timestamp.  Because all models share the same hourly grid,
        the matched slot is always the same clock hour regardless of model —
        producing identical _match_dt_h for every model.
        Peak-intensity offset is model-specific: ECMWF may load its heaviest
        rain at 13:00 while GFS loads it at 15:00 for an event observed at
        14:00.  This is the signal that actually drives TAF timing decisions.
        """
        # ── Constant imports from guidance_generator ─────────────────────────
        # Avoid a hard circular import by importing locally.  The constants are
        # module-level scalars — this import is effectively free after first call.
        try:
            from guidance_generator import _RAIN_TOLERANCE_HOURS, _RAIN_VOTE_THR
        except ImportError:
            # Fallback if guidance_generator is not on sys.path
            _RAIN_TOLERANCE_HOURS = 2
            _RAIN_VOTE_THR        = 1.5

        MIN_TIMING_EVENTS = 5   # minimum rain events to trust the timing score

        if base_w is None:
            base_w = self._equal_weights()

        # Only meaningful for Rainfall
        if parameter != "Rainfall":
            return dict(base_w)

        if df.empty or obs_col not in df.columns or forecast_col not in df.columns:
            return dict(base_w)

        # Collect all unique obs rain-event timestamps (deduplicated)
        obs_event_times = (
            df[df[obs_col] >= _RAIN_VOTE_THR]
            .drop_duplicates("WITA_Target")["WITA_Target"]
            .sort_values()
            .tolist()
            if "WITA_Target" in df.columns
            else []
        )

        if len(obs_event_times) < MIN_TIMING_EVENTS:
            self.timing_weights = {"n_events": len(obs_event_times), "scores": {}}
            return dict(base_w)

        tol_td = timedelta(hours=_RAIN_TOLERANCE_HOURS)
        model_offsets: Dict[str, list] = {m: [] for m in self.models}

        for obs_t in obs_event_times:
            for model in self.models:
                md = df[df[model_col] == model]
                window = md[
                    (md["WITA_Target"] >= obs_t - tol_td) &
                    (md["WITA_Target"] <= obs_t + tol_td)
                ]
                if window.empty:
                    continue
                peak_idx  = window[forecast_col].idxmax()
                peak_time = window.loc[peak_idx, "WITA_Target"]
                offset_h  = (peak_time - obs_t).total_seconds() / 3600.0
                model_offsets[model].append(offset_h)

        # Build timing scores
        raw_scores:   Dict[str, float] = {}
        sample_sizes: Dict[str, int]   = {}
        for model in self.models:
            offs = model_offsets[model]
            if len(offs) < MIN_TIMING_EVENTS:
                # Insufficient data for this model — use equal score so it does
                # not get penalised relative to models with more data
                raw_scores[model]   = 1.0 / len(self.models)
                sample_sizes[model] = len(offs)
            else:
                mean_abs = float(np.mean(np.abs(offs)))
                raw_scores[model]   = 1.0 / (1.0 + mean_abs)
                sample_sizes[model] = len(offs)

        # Apply sample-size shrinkage (consistent with all other components)
        shrunk = self._apply_shrinkage(raw_scores, sample_sizes)
        result = self._normalise(shrunk)

        # Store diagnostics for JSON export and dashboard display
        self.timing_weights = {
            "n_events": len(obs_event_times),
            "scores":   result,
            "mean_offset_h": {
                m: round(float(np.mean(model_offsets[m])), 3)
                   if model_offsets[m] else 0.0
                for m in self.models
            },
            "mean_abs_offset_h": {
                m: round(float(np.mean(np.abs(model_offsets[m]))), 3)
                   if model_offsets[m] else 0.0
                for m in self.models
            },
        }

        return result

    # ==========================================================================

    def calculate_temporal_weights(self, df: pd.DataFrame,
                                    time_col: str = "WITA_Target",
                                    forecast_col: str = "forecast",
                                    obs_col: str = "obs",
                                    model_col: str = "Model",
                                    parameter: str = "Rainfall",
                                    is_circular: bool = False,
                                    min_samples: int = 100) -> Dict[str, Dict[str, float]]:
        temporal_weights: Dict[str, Dict[str, float]] = {}
        df2      = df.copy()
        df2["_t"] = pd.to_datetime(df2[time_col])
        max_time  = df2["_t"].max()
        for win_name, days in TEMPORAL_WINDOWS.items():
            cutoff = max_time - timedelta(days=days)
            sub    = df2[df2["_t"] >= cutoff]
            if len(sub) >= min_samples:
                temporal_weights[win_name] = self.calculate_base_weights(
                    sub, forecast_col, obs_col, model_col,
                    parameter=parameter, is_circular=is_circular,
                    min_samples=min_samples // 10
                )
        return temporal_weights

    # ==========================================================================
    # DIURNAL WEIGHTS  [v5.6.0]
    # ==========================================================================

    def _get_weight_for_leadtime(self, lt_weights: Dict, lead_hour: int) -> Dict[str, float]:
        for bracket, (lo, hi) in LEADTIME_BRACKETS.items():
            if lo <= lead_hour < hi:
                return lt_weights.get(bracket, {})
        return lt_weights.get("Day_4+", {})

    def _get_weight_for_regime(self, regime_weights: Dict, obs_value: float,
                                parameter: str = "Rainfall") -> Dict[str, float]:
        regimes = PARAMETER_REGIMES.get(parameter)
        if not regimes:
            return {}
        for regime, (lo, hi) in regimes.items():
            if lo <= obs_value < hi:
                return regime_weights.get(regime, {})
        # [v5.3.0 G10 fix] Fallback to FIRST regime (e.g. "dry" / "cool").
        # Old code returned the LAST regime ("extreme"/"hot") which gave extreme-
        # event weights to anomalous sub-boundary values (e.g. -0.1 mm artifact).
        first_key = list(regimes.keys())[0]
        return regime_weights.get(first_key, {})

    def _average_leadtime_weights(
            self,
            lt_weights: Dict[str, Dict[str, float]],
            df: pd.DataFrame,
            lead_col: str,
    ) -> Dict[str, float]:
        """
        Sample-size-weighted average of all available lead-time bracket weights.

        Used when no specific lead_hour is provided to calculate_fused_weights()
        (i.e. when computing global weights for the dashboard or the TAF guidance
        JSON).  Each bracket's weight vector is averaged proportionally to how many
        matched forecast-observation pairs fall within that bracket.

        Example with OPERATIONAL data (all lead hours 0–23):
          • Only Day_1 bracket has data → average collapses to Day_1 weights exactly.

        Example with RELIABILITY data (lead hours 0–120):
          • All four brackets may be populated.  The average weights Day_1 most
            heavily because it has the most samples (24 h × many cycles).

        Returns an empty dict only if lt_weights is empty or all bracket counts
        are zero — the caller falls back to base_w in that case.
        """
        if not lt_weights or lead_col not in df.columns:
            return {}

        bracket_counts: Dict[str, int] = {}
        for bracket, (lo, hi) in LEADTIME_BRACKETS.items():
            if bracket not in lt_weights:
                continue
            count = int(((df[lead_col] >= lo) & (df[lead_col] < hi)).sum())
            if count > 0:
                bracket_counts[bracket] = count

        total = sum(bracket_counts.values())
        if total == 0:
            return {}

        M       = len(self.models)
        eq_w    = 1.0 / M if M > 0 else 0.0
        averaged = {m: 0.0 for m in self.models}
        for bracket, count in bracket_counts.items():
            share = count / total
            for model in self.models:
                averaged[model] += share * lt_weights[bracket].get(model, eq_w)

        # Re-normalise to guard against floating-point drift
        total_w = sum(averaged.values())
        if total_w > 0:
            averaged = {m: v / total_w for m, v in averaged.items()}
        return averaged

    def _average_regime_weights(
            self,
            reg_weights: Dict[str, Dict[str, float]],
            df: pd.DataFrame,
            obs_col: str,
            parameter: str,
    ) -> Dict[str, float]:
        """
        Sample-count-weighted average of all available per-regime weight vectors.

        Used when current_obs is None in calculate_fused_weights() — i.e. when
        computing global weights for the dashboard or the guidance JSON export,
        where we don't have a single "current" observation to classify into a
        specific regime.

        Each regime's contribution is proportional to how many observations
        from the verification dataset fall within its boundaries.  Regime slices
        are mutually exclusive, so sample-count weighting is semantically correct
        (unlike temporal windows which are nested — see _average_temporal_weights).

        Example for Rainfall with 90 days of hourly data at WAWP:
          • dry  (0–0.1 mm):  ~70% of hours  → weight ≈ 0.70
          • light (0.1–5mm):  ~20% of hours  → weight ≈ 0.20
          • moderate (5–20mm):  ~7%           → weight ≈ 0.07
          • heavy / extreme:    ~3%           → weight ≈ 0.03
        This produces a w_regime that is appropriately dominated by dry/light
        performance but still incorporates heavy-rain skill differences.

        Returns {} for Wind Direction (no PARAMETER_REGIMES entry → caller falls
        back to base_w, identical to previous behaviour).
        """
        regimes = PARAMETER_REGIMES.get(parameter)
        if not regimes or not reg_weights:
            return {}

        # [v5.3.0 G9 fix] Deduplicate by timestamp before counting.
        # The long-format DataFrame has N_models rows per timestamp, so a
        # plain .sum() inflates every regime count by 5×.  Proportions are
        # correct but diagnostic counts are misleading.
        obs_unique = (
            df.drop_duplicates("WITA_Target")[obs_col]
            if "WITA_Target" in df.columns
            else df[obs_col]
        )
        regime_counts: Dict[str, int] = {}
        for regime, (lo, hi) in regimes.items():
            if regime not in reg_weights:
                continue
            count = int(((obs_unique >= lo) & (obs_unique < hi)).sum())
            if count > 0:
                regime_counts[regime] = count

        # Get SOP costs for parameter
        sop_costs = REGIME_SOP_COSTS.get(parameter, {})
        
        regime_factors: Dict[str, float] = {}
        for regime, count in regime_counts.items():
            cost = sop_costs.get(regime, 1.0)
            regime_factors[regime] = count * cost

        total_factor = sum(regime_factors.values())
        if total_factor == 0:
            return {}

        M    = len(self.models)
        eq_w = 1.0 / M if M > 0 else 0.0
        averaged: Dict[str, float] = {m: 0.0 for m in self.models}
        for regime, factor in regime_factors.items():
            share = factor / total_factor
            for model in self.models:
                averaged[model] += share * reg_weights[regime].get(model, eq_w)

        total_w = sum(averaged.values())
        if total_w > 0:
            averaged = {m: v / total_w for m, v in averaged.items()}
        return averaged

    def _average_temporal_weights(
            self,
            tmp_weights: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        """
        Recency-biased weighted average of all available temporal-window weight vectors.

        [v5.6.0] Replaces flat equal-weighting (25/25/25/25) with TEMPORAL_WINDOW_BLEND
        (40/30/20/10).  Flat equal-weighting was noisy because the 24h window (fewest
        rows, highest variance) received the same influence as the stable 30d window.
        Recency-biased weights keep the most-recent lens most influential while still
        giving the monthly lens a 10% anchor contribution.

        Design note — NOT sample-count weighted (unlike regime and diurnal):
        Temporal windows are NESTED: "24 Hours" ⊂ "3 Days" ⊂ "7 Days" ⊂ "Month".
        Sample-count weighting would make "Month" ≈ 30× more influential than "24 Hours"
        because it contains ≈ 30× more rows — collapsing w_temporal ≈ base_w and
        defeating the purpose of a separate temporal component entirely.

        If a window is absent (insufficient rows), its blend share is redistributed
        proportionally among the available windows.

        Returns {} if tmp_weights is empty — the caller falls back to base_w.
        """
        if not tmp_weights:
            return {}

        available = [k for k in TEMPORAL_WINDOW_BLEND if k in tmp_weights]
        if not available:
            return {}

        # Compute normalised blend shares for available windows only
        raw_shares = {k: TEMPORAL_WINDOW_BLEND[k] for k in available}
        total_share = sum(raw_shares.values())
        if total_share <= 0:
            return {}
        norm_shares = {k: v / total_share for k, v in raw_shares.items()}

        M    = len(self.models)
        eq_w = 1.0 / M if M > 0 else 0.0
        averaged: Dict[str, float] = {m: 0.0 for m in self.models}
        for win_name, share in norm_shares.items():
            for model in self.models:
                averaged[model] += share * tmp_weights[win_name].get(model, eq_w)

        total_w = sum(averaged.values())
        if total_w > 0:
            averaged = {m: v / total_w for m, v in averaged.items()}
        return averaged

    # ==========================================================================
    # FUSED WEIGHTS
    # ==========================================================================

    def calculate_fused_weights(self, df: pd.DataFrame,
                                 lead_hour: Optional[int] = None,
                                 current_obs: Optional[float] = None,
                                 temporal_window: Optional[str] = None,
                                 current_utc_hour: Optional[int] = None,
                                 forecast_col: str = "forecast",
                                 obs_col: str = "obs",
                                 model_col: str = "Model",
                                 time_col: str = "WITA_Target",
                                 lead_col: str = "Lead_Hour",
                                 parameter: str = "Rainfall",
                                 is_circular: bool = False,
                                 min_samples: int = 24) -> Dict[str, float]:
        """
        Compute the final fused weight vector by blending four weight components.

        Components
        ----------
        base (30%)
            Trained on all data using the configured method (composite / ridge /
            covariance / crps).  This is the anchor weight — always computed.

        lead-time (0% on OPERATIONAL, 25% when RELIABILITY data available)
            [v5.5.1] Zeroed on OPERATIONAL data — all pairs fall in Day_1,
            so the sample-count-weighted average collapses to base_w exactly.
            Restore leadtime_weight to 0.15 and reduce timing_weight to 0.10
            when SCHEME_2_RELIABILITY data (lead hours 0-120) is loaded.

        timing (25%)  [v5.5.1 — new]
            Derived from the peak-intensity timing offset per rain event.
            For each obs rain event, each model's forecast Rain is scanned
            within ±_RAIN_TOLERANCE_HOURS; the offset between the model's
            peak-Rain hour and the obs hour is recorded.  Models that
            consistently peak early or late receive a lower timing score:
                score = 1 / (1 + mean(|offset_h|))
            Falls back to base_w for non-Rainfall parameters or when fewer
            than MIN_TIMING_EVENTS events exist in the window.
            After this method returns, self.timing_weights contains the
            full diagnostics (n_events, mean_offset_h, mean_abs_offset_h)
            for JSON storage and dashboard display.

        regime (25%)
            Trained separately for each PARAMETER_REGIMES slice.
            • If current_obs is provided: uses the weight vector for the regime
              that the current observation falls into.
            • If current_obs is None (global computation): uses _average_regime_weights()
              — a sample-count-weighted average, proportional to how many observations
              fall in each regime.  For Rainfall at WAWP this heavily weights the dry
              and light regimes (most common), while still incorporating heavy-rain
              skill differences.
            • For Wind Direction (no regime partitioning): silently falls back to base_w.

        temporal (20%)
            Trained separately for each TEMPORAL_WINDOWS slice.
            • If temporal_window is provided: uses that window's weight vector exactly.
            • If temporal_window is None (global computation): uses _average_temporal_weights()
              — an EQUAL-weighted average across all available windows.  Equal (not
              sample-count) weighting is used because windows are NESTED (24h ⊂ 3d ⊂ 7d
              ⊂ Month), so sample-count weighting collapses to ≈ Month ≈ base_w.

        When a component cannot be computed (insufficient data, no regime partitioning
        for the parameter, etc.), base_w fills in silently.  The normalisation step at
        the end always produces valid weights regardless.

        Note: the `threshold` parameter was removed in v5.2.0.  It was accepted in
        previous versions but never used — all sub-methods source their thresholds
        internally from PARAMETERS[parameter]["thresholds"].  Any external callers
        passing threshold= as a keyword argument should simply remove it.

        After this method returns, the following instance attributes are populated
        and available for inspection or JSON export:
            self.leadtime_weights   – Dict[bracket  → Dict[model → weight]]
            self.timing_weights     – Dict with n_events, scores, mean offsets  [v5.5.1]
            self.regime_weights     – Dict[regime   → Dict[model → weight]]
            self.temporal_weights   – Dict[window   → Dict[model → weight]]
        """
        base_w = self.calculate_base_weights(
            df, forecast_col, obs_col, model_col,
            parameter=parameter, is_circular=is_circular,
            min_samples=min_samples
        )

        # ── Lead-time component ──────────────────────────────────────────────
        # v5.1.0 fix: _average_leadtime_weights() used when lead_hour is None
        # instead of silently falling back to base_w.
        lt_dict: Dict[str, Dict[str, float]] = {}
        if lead_col in df.columns:
            lt_dict = self.calculate_leadtime_weights(
                df, lead_col, forecast_col, obs_col, model_col,
                parameter=parameter, is_circular=is_circular,
                min_samples=min_samples
            )
            if lead_hour is not None:
                lt_w = self._get_weight_for_leadtime(lt_dict, lead_hour) or base_w
            else:
                lt_w = self._average_leadtime_weights(lt_dict, df, lead_col) or base_w
        else:
            lt_w = base_w

        # ── Regime component ─────────────────────────────────────────────────
        # v5.2.0 fix: _average_regime_weights() used when current_obs is None
        # instead of silently falling back to base_w.
        # [v5.4.0 W-4] Pass base_w so calculate_regime_weights reuses the
        # already-computed result rather than running a second full optimisation.
        reg_dict: Dict[str, Dict[str, float]] = self.calculate_regime_weights(
            df, obs_col, forecast_col, model_col,
            parameter=parameter, is_circular=is_circular,
            min_samples=min_samples * 2,
            base_w=base_w,
        )
        if current_obs is not None:
            regime_w = (self._get_weight_for_regime(reg_dict, current_obs, parameter)
                        or base_w)
        else:
            regime_w = self._average_regime_weights(reg_dict, df, obs_col, parameter) or base_w

        # ── Temporal component ───────────────────────────────────────────────
        # v5.2.0 fix: _average_temporal_weights() used when temporal_window is None
        # instead of silently falling back to base_w.
        # [v5.6.0] _average_temporal_weights now uses recency-biased blend.
        tmp_dict: Dict[str, Dict[str, float]] = {}
        if time_col in df.columns:
            tmp_dict = self.calculate_temporal_weights(
                df, time_col, forecast_col, obs_col, model_col,
                parameter=parameter, is_circular=is_circular,
                min_samples=min_samples * 2
            )
            if temporal_window is not None:
                temporal_w = tmp_dict.get(temporal_window, base_w)
            else:
                temporal_w = self._average_temporal_weights(tmp_dict) or base_w
        else:
            temporal_w = base_w

        # Expose component dicts for downstream use (JSON storage, diagnostics)
        self.leadtime_weights  = lt_dict
        self.regime_weights    = reg_dict
        self.temporal_weights  = tmp_dict

        # ── Timing component (Rainfall only) ─────────────────────────────────
        # [v5.5.1] Compute timing weights from peak-intensity offset per rain
        # event.  Falls back to base_w silently for non-Rainfall parameters
        # or when fewer than MIN_TIMING_EVENTS events are available.
        # base_w passed in to avoid a redundant full-dataset optimisation
        # (same pattern as W-4 fix for regime weights).
        timing_w = self.calculate_timing_weights(
            df, forecast_col, obs_col, model_col,
            parameter=parameter, base_w=base_w,
        )
        # self.timing_weights is populated inside calculate_timing_weights

        # ── Blend ────────────────────────────────────────────────────────────
        # [v5.7.0] Diurnal component removed. timing_weight only meaningful for
        # Rainfall; for WS/WD its budget is redirected to temporal.
        cfg = self.config
        if parameter == "Rainfall":
            eff_timing   = cfg.timing_weight
            eff_temporal = cfg.temporal_weight
        else:
            eff_timing   = 0.0
            eff_temporal = cfg.temporal_weight + cfg.timing_weight

        fused = {}
        for model in self.models:
            fused[model] = (
                cfg.base_weight    * base_w.get(model,     0.0) +
                cfg.leadtime_weight* lt_w.get(model,       0.0) +
                eff_timing         * timing_w.get(model,   0.0) +
                cfg.regime_weight  * regime_w.get(model,   0.0) +
                eff_temporal       * temporal_w.get(model, 0.0)
            )
        fused = self._normalise(fused)

        # ── Weight Spread Diagnostics ─────────────────────────────────────────
        # [v5.8.0] Track the spread (max - min) across model weights for each active component
        self.weight_diagnostics = {
            "base_spread":     round(max(base_w.values()) - min(base_w.values()), 5) if base_w else 0.0,
            "regime_spread":   round(max(regime_w.values()) - min(regime_w.values()), 5) if regime_w else 0.0,
            "temporal_spread": round(max(temporal_w.values()) - min(temporal_w.values()), 5) if temporal_w else 0.0,
        }
        if parameter == "Rainfall":
            self.weight_diagnostics["timing_spread"] = round(
                max(timing_w.values()) - min(timing_w.values()), 5
            ) if timing_w else 0.0
        else:
            self.weight_diagnostics["timing_spread"] = 0.0

        if cfg.leadtime_weight > 0.0:
            self.weight_diagnostics["leadtime_spread"] = round(
                max(lt_w.values()) - min(lt_w.values()), 5
            ) if lt_w else 0.0

        # Significance test (stored for dashboard retrieval)
        self.significance["fused"] = self.test_significance_vs_equal(
            df, fused, forecast_col, obs_col, model_col, is_circular=is_circular
        )

        # ── Spread calibration ────────────────────────────────────────────────
        # Compute and cache the historical ensemble MAE so that callers can pass
        # self.spread_calibration[parameter] directly to generate_ensemble_forecast
        # for calibrated confidence scores.
        self.spread_calibration[parameter] = self.compute_spread_calibration(
            df, fused,
            forecast_col=forecast_col, obs_col=obs_col,
            model_col=model_col,
            is_circular=is_circular,
            parameter=parameter,
        )

        return fused

    # ==========================================================================
    # SIGNIFICANCE TEST (Diebold-Mariano with Newey-West HAC)
    # ==========================================================================

    def test_significance_vs_equal(self, df: pd.DataFrame,
                                    weights: Dict[str, float],
                                    forecast_col: str = "forecast",
                                    obs_col: str = "obs",
                                    model_col: str = "Model",
                                    is_circular: bool = False) -> Dict:
        """
        Diebold-Mariano test: does the weighted ensemble outperform equal-weighted?

        Uses Newey-West HAC variance (correcting for serial autocorrelation in
        forecast errors) rather than iid variance.  Tests H₀: E[d_t] = 0 where
            d_t = SE(weighted, t) − SE(equal, t)
        with a two-sided alternative.

        [v5.3.0 G2 fix]
        For circular parameters (Wind Direction), the weighted ensemble forecast
        is now computed via circular_weighted_mean (sin/cos decomposition) and
        squared error is computed via circular_difference()**2.  Previously both
        used linear arithmetic: a 350°/10° forecast vs observation gave
        error=340² instead of error=20², making the DM statistic meaningless.
        """
        timestamps = df["WITA_Target"].unique() if "WITA_Target" in df.columns else []
        w_sq_err, e_sq_err = [], []

        for ts in timestamps:
            ts_df   = df[df["WITA_Target"] == ts]
            obs_val = ts_df[obs_col].iloc[0] if len(ts_df) > 0 else np.nan
            if np.isnan(obs_val):
                continue

            if is_circular:
                # Weighted circular mean for the ensemble forecast
                fc_vals, w_vals = [], []
                for model in self.models:
                    row = ts_df[ts_df[model_col] == model][forecast_col].values
                    if len(row) > 0 and not np.isnan(row[0]):
                        fc_vals.append(float(row[0]))
                        w_vals.append(float(weights.get(model, 0.0)))
                if not fc_vals:
                    continue
                w_arr = np.array(w_vals); w_arr = w_arr / w_arr.sum()
                w_fc  = circular_weighted_mean(np.array(fc_vals), w_arr)

                # Equal-weighted circular mean
                all_fc = ts_df[forecast_col].dropna().values.astype(float)
                if len(all_fc) == 0:
                    continue
                e_fc = circular_weighted_mean(all_fc, np.ones(len(all_fc)))

                w_sq_err.append(float(circular_difference(
                    np.array([w_fc]), np.array([obs_val]))[0]) ** 2)
                e_sq_err.append(float(circular_difference(
                    np.array([e_fc]), np.array([obs_val]))[0]) ** 2)

            else:
                # Linear weighted ensemble forecast
                w_sum, w_total = 0.0, 0.0
                for model in self.models:
                    row = ts_df[ts_df[model_col] == model][forecast_col].values
                    if len(row) > 0 and not np.isnan(row[0]):
                        w_sum   += weights.get(model, 0.0) * row[0]
                        w_total += weights.get(model, 0.0)
                w_fc = w_sum / w_total if w_total > 0 else np.nan

                e_fc = ts_df[forecast_col].mean()

                if not (np.isnan(w_fc) or np.isnan(e_fc)):
                    w_sq_err.append((w_fc - obs_val)**2)
                    e_sq_err.append((e_fc - obs_val)**2)

        n = len(w_sq_err)
        if n < 10:
            return {"dm_statistic": np.nan, "p_value": np.nan,
                    "significant": False, "message": "Insufficient data (n<10)"}

        d      = np.array(w_sq_err) - np.array(e_sq_err)
        d_mean = float(np.mean(d))
        nw_var = _newey_west_variance(d)

        if nw_var <= 0:
            return {"dm_statistic": 0.0, "p_value": 1.0, "significant": False,
                    "message": "Zero HAC variance"}

        dm_stat = d_mean / np.sqrt(nw_var / n)
        p_value = float(2.0 * (1.0 - stats.norm.cdf(abs(dm_stat))))

        return {
            "dm_statistic": float(dm_stat),
            "p_value":      p_value,
            "significant":  p_value < 0.05,
            "sample_size":  n,
            "nw_bandwidth": int(max(1, int(np.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))))),
        }

    # ==========================================================================
    # ENSEMBLE FORECAST GENERATION
    # ==========================================================================

    def generate_ensemble_forecast(self, model_forecasts: Dict[str, float],
                                    weights: Dict[str, float],
                                    is_circular: bool = False,
                                    spread_calibration: Optional[float] = None,
                                    ) -> Tuple[float, float, float]:
        """
        Generate weighted ensemble forecast (mean, spread, confidence).

        For circular parameters, uses the correct circular weighted mean
        (sin/cos decomposition) instead of a linear average.

        Confidence (calibrated)
        ───────────────────────
        Previously: confidence = 1 / (1 + spread)
            — dimensionally inconsistent; a 1 mm spread and a 1 kt spread both
              map to 0.50, but those represent very different levels of model
              agreement.

        Fix: when `spread_calibration` (= historical ensemble MAE in the same
        units as `spread`) is provided, the confidence is calculated as the
        probability that the true value lies within ±spread of the ensemble
        mean, assuming Gaussian errors with std = spread_calibration:

            confidence = 2·Φ(spread_calibration / max(spread, ε)) − 1

        Operational interpretation:
            • spread == spread_calibration  →  confidence ≈ 0.683 (1σ interval)
            • spread < spread_calibration   →  confidence > 0.683 (tight models)
            • spread > spread_calibration   →  confidence < 0.683 (wide spread)
            • spread → 0                   →  confidence → 1.0
            • spread → ∞                   →  confidence → 0.0

        When `spread_calibration` is None (backward-compat mode), the old
        unit-naive formula is retained.  Pass `self.spread_calibration[param]`
        (populated by `compute_spread_calibration`) to activate calibration.

        See also: `compute_spread_calibration()`.
        """
        fc_vals, w_vals = [], []
        for model, fc in model_forecasts.items():
            if model in weights and not np.isnan(fc):
                fc_vals.append(float(fc))
                w_vals.append(float(weights[model]))

        if not fc_vals:
            return np.nan, np.nan, 0.0

        fc_arr = np.array(fc_vals)
        w_arr  = np.array(w_vals)
        w_arr  = w_arr / w_arr.sum()

        if is_circular:
            ensemble_mean = circular_weighted_mean(fc_arr, w_arr)
            rad           = np.deg2rad(fc_arr)
            sin_w         = float(np.sum(w_arr * np.sin(rad)))
            cos_w         = float(np.sum(w_arr * np.cos(rad)))
            R             = np.sqrt(sin_w**2 + cos_w**2)
            R             = min(R, 1.0 - 1e-12)
            ensemble_spread = float(np.rad2deg(np.sqrt(-2.0 * np.log(R))))
        else:
            ensemble_mean   = float(np.average(fc_arr, weights=w_arr))
            ensemble_spread = float(np.sqrt(np.average((fc_arr - ensemble_mean)**2,
                                                        weights=w_arr)))

        # ── Confidence ────────────────────────────────────────────────────────
        if spread_calibration is not None and spread_calibration > 0:
            # Calibrated: P(|error| ≤ spread) under N(0, spread_calibration²)
            confidence = float(
                2.0 * stats.norm.cdf(spread_calibration / max(ensemble_spread, 1e-9)) - 1.0
            )
            confidence = float(np.clip(confidence, 0.0, 1.0))
        else:
            # Legacy fallback (unit-naive; kept for backward compatibility)
            confidence = 1.0 / (1.0 + ensemble_spread)

        return ensemble_mean, ensemble_spread, confidence

    # ==========================================================================
    # SPREAD CALIBRATION
    # ==========================================================================

    def compute_spread_calibration(self,
                                    df: pd.DataFrame,
                                    weights: Dict[str, float],
                                    forecast_col: str = "forecast",
                                    obs_col: str = "obs",
                                    model_col: str = "Model",
                                    is_circular: bool = False,
                                    parameter: str = "Rainfall") -> float:
        """
        Derive `spread_calibration` = historical ensemble MAE in parameter units.

        This scalar converts raw ensemble spread into a calibrated confidence
        probability inside `generate_ensemble_forecast`.  Specifically:

            confidence = 2·Φ(spread_calibration / spread) − 1

        So when the current spread equals the historical MAE, the confidence
        reports exactly the 1σ coverage probability (~68.3%).

        Algorithm
        ---------
        1. For every valid timestamp in `df`, compute the weighted ensemble mean
           using the supplied `weights`.
        2. Compute the absolute error vs the corresponding observation.
        3. Return the mean absolute error as the calibration scalar.

        If fewer than 20 timestamps are available, returns a safe default
        derived from the parameter's primary threshold so the formula still
        produces sensible numbers.

        Parameters
        ----------
        df : pd.DataFrame
            Verification data (same format expected by calculate_all_metrics).
        weights : dict
            Model weights (e.g. from calculate_fused_weights).
        is_circular : bool
            Use circular (angular) distance for wind direction.
        parameter : str
            Name used to look up the safe-default threshold.

        Returns
        -------
        float
            Ensemble MAE in parameter units (always > 0).
        """
        # Pivot to (T × M) matrix
        pivot = df.pivot_table(index="WITA_Target", columns=model_col,
                               values=forecast_col, aggfunc="first")
        obs_s = df.groupby("WITA_Target")[obs_col].first()
        idx   = pivot.index.intersection(obs_s.index)

        # Safe default: use primary threshold as a rough spread scale
        default_scale = float(
            PARAMETERS.get(parameter, PARAMETERS["Rainfall"]).get("primary_threshold", 1.0)
        )

        if len(idx) < 20:
            return default_scale

        X = pivot.loc[idx].values.astype(float)
        y = obs_s.loc[idx].values.astype(float)
        mask = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
        X, y = X[mask], y[mask]
        if len(X) < 20:
            return default_scale

        # Compute weighted ensemble mean at every timestamp
        col_names = list(pivot.columns)
        w_arr = np.array([weights.get(m, 1.0 / len(col_names)) for m in col_names],
                         dtype=float)
        w_arr = w_arr / w_arr.sum()

        if is_circular:
            # Circular weighted mean across models at every timestamp (T,)
            rad      = np.deg2rad(X)                                   # (T, M)
            sin_mean = np.sum(np.sin(rad) * w_arr, axis=1)             # (T,)
            cos_mean = np.sum(np.cos(rad) * w_arr, axis=1)             # (T,)
            ens_mean = np.rad2deg(np.arctan2(sin_mean, cos_mean)) % 360.0
            errors   = circular_difference(ens_mean, y)                # unsigned ∈[0°,180°]
        else:
            ens_mean = X @ w_arr                                       # (T,)
            errors   = np.abs(ens_mean - y)

        mae = float(np.mean(errors))
        return max(mae, 1e-9)   # guard against zero MAE on tiny/perfect datasets

    # ==========================================================================
    # TAF GUIDANCE JSON INTEGRATION
    # ==========================================================================

    def load_taf_guidance(self, filepath: str) -> Optional[Dict]:
        """Load and validate a TAF guidance JSON file."""
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"Cannot load guidance JSON: {e}") from e

        is_valid, errors = validate_guidance_json(data)
        if not is_valid:
            raise ValueError(
                f"Guidance JSON schema errors ({len(errors)}):\n" +
                "\n".join(f"  • {e}" for e in errors)
            )
        return data

    def extract_metrics_from_taf(self, taf_data: Dict,
                                  parameter: str = "Rainfall") -> Dict[str, SkillMetrics]:
        """Extract SkillMetrics for each model from the TAF guidance JSON."""
        param_key = parameter.lower().replace(" ", "_")
        param_block = taf_data.get("parameters", {}).get(param_key, {})
        lm = param_block.get("lookback_metrics", {})
        # Use 7-day window as reference; fall back to any available window
        window_data = lm.get("7 Days", lm.get("Month", {}))

        out: Dict[str, SkillMetrics] = {}
        for model in self.models:
            md = window_data.get(model, {})
            if md:
                out[model] = SkillMetrics(
                    model_name=model,
                    rmse=md.get("RMSE", 999.0),
                    mae=md.get("MAE", 999.0),
                    bias=md.get("Bias", 0.0),
                    pod=md.get("POD", 0.0),
                    far=md.get("FAR", 1.0),
                    csi=md.get("CSI", 0.0),
                    hss=md.get("HSS", 0.0),
                    sample_size=int(md.get("Sample_Size", 0)),
                )
        return out

    def calculate_weights_from_taf(self, taf_data: Dict,
                                    parameter: str = "Rainfall") -> Dict[str, float]:
        """
        Derive weights from a pre-computed TAF guidance JSON.
        Uses the full composite scoring (all metrics) rather than the
        v3.x simplified RMSE+Bias-only formula.
        """
        metrics = self.extract_metrics_from_taf(taf_data, parameter)
        if not metrics:
            return self._equal_weights()

        raw_scores   = {m: self.calculate_composite_score(m_obj, parameter)
                        for m, m_obj in metrics.items()}
        sample_sizes = {m: metrics[m].sample_size for m in metrics}
        shrunk       = self._apply_shrinkage(raw_scores, sample_sizes)
        return self._normalise({m: shrunk.get(m, 1.0/len(self.models))
                                for m in self.models})

    # ==========================================================================
    # UTILITIES
    # ==========================================================================

    def _equal_weights(self) -> Dict[str, float]:
        n = len(self.models)
        return {m: 1.0 / n for m in self.models}

    def _normalise(self, scores: Dict[str, float]) -> Dict[str, float]:
        total = sum(scores.values())
        if total > 0:
            return {m: v / total for m, v in scores.items()}
        return self._equal_weights()


# ==============================================================================
# DASHBOARD HELPER (thin wrapper kept for API compatibility)
# ==============================================================================

def calculate_optimal_ensemble(df: pd.DataFrame, cfg: Dict, models: List[str],
                                taf_data: Optional[Dict] = None,
                                method: str = "composite",
                                is_circular: bool = False,
                                parameter: str = "Rainfall"
                                ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict, Dict]:
    """
    Calculate optimal ensemble for dashboard integration.
    Thin wrapper around AdvancedEnsembleWeighter.
    """
    config   = WeightConfiguration(method=method)
    weighter = AdvancedEnsembleWeighter(models, config)

    param_cfg    = PARAMETERS.get(parameter, PARAMETERS["Rainfall"])
    threshold    = param_cfg["primary_threshold"]
    forecast_col = {"Rainfall": "Rain", "Temperature": "Temp",
                    "Wind Speed": "WS", "Wind Direction": "WD"}.get(parameter, "Rain")
    obs_col      = {"Rainfall": "OBS_Rain", "Temperature": "OBS_Temp",
                    "Wind Speed": "OBS_Wind", "Wind Direction": "OBS_WindDir"}.get(parameter, "OBS_Rain")

    if taf_data is not None:
        weights      = weighter.calculate_weights_from_taf(taf_data, parameter)
        significance = {}
        spread_cal   = weighter.compute_spread_calibration(
            df, weights, forecast_col, obs_col, "Model", is_circular, parameter
        )
    else:
        weights      = weighter.calculate_fused_weights(
            df, forecast_col=forecast_col, obs_col=obs_col,
            model_col="Model", time_col="WITA_Target",
            lead_col="Lead_Hour", parameter=parameter,
            is_circular=is_circular
        )
        significance = weighter.significance.get("fused", {})
        spread_cal   = weighter.spread_calibration.get(parameter)

    ensemble_results = []
    for timestamp, group in df.groupby("WITA_Target"):
        lead_hour     = group["Lead_Hour"].iloc[0] if "Lead_Hour" in group.columns else None
        model_fcs     = dict(zip(group["Model"], group[forecast_col]))
        mean, spread, conf = weighter.generate_ensemble_forecast(
            model_fcs, weights, is_circular, spread_calibration=spread_cal
        )
        ensemble_results.append({
            "WITA_Target": timestamp,
            "Mean":        mean,
            "Std":         spread,
            "Confidence":  conf,
            "Obs":         group[obs_col].iloc[0],
            "Lead_Hour":   lead_hour,
        })

    ens_calc = pd.DataFrame(ensemble_results)
    ens_row  = pd.DataFrame({
        forecast_col: ens_calc["Mean"],
        obs_col:      ens_calc["Obs"],
        "Model":      "ENSEMBLE (OPTIMAL)",
    })
    return ens_calc, ens_row, weights, significance


# ==============================================================================
# QUICK TEST
# ==============================================================================

def quick_test():
    print("Testing Advanced Ensemble Weighter v5.4.0 ...")
    np.random.seed(42)
    n = 600

    df = pd.DataFrame({
        "WITA_Target": pd.date_range("2024-01-01", periods=n, freq="h"),
        "Model":       np.random.choice(MODELS, n),
        "forecast":    np.random.randn(n) * 5 + 10,
        "obs":         np.random.randn(n) * 4 + 10,
        "Lead_Hour":   np.random.randint(0, 120, n),
    })

    for method in ("composite", "ridge", "covariance", "crps"):
        config   = WeightConfiguration(method=method)
        weighter = AdvancedEnsembleWeighter(MODELS, config)
        weights  = weighter.calculate_base_weights(df, parameter="Rainfall")
        print(f"\n[{method}]")
        for m, w in sorted(weights.items(), key=lambda x: -x[1]):
            print(f"  {m}: {w:.4f}")

    print("\n[circular / Wind Direction]")
    df_circ = df.copy()
    df_circ["forecast"] = np.random.uniform(0, 360, n)
    df_circ["obs"]      = np.random.uniform(0, 360, n)
    config   = WeightConfiguration(method="crps")
    weighter = AdvancedEnsembleWeighter(MODELS, config)
    weights  = weighter.calculate_base_weights(df_circ, parameter="Wind Direction",
                                                is_circular=True)
    for m, w in sorted(weights.items(), key=lambda x: -x[1]):
        print(f"  {m}: {w:.4f}")

    # [G3 test] Ridge/covariance on circular data should silently fall back to crps
    print("\n[G3: ridge on circular — must not crash and weights must sum to 1.0]")
    config_ridge = WeightConfiguration(method="ridge")
    w_ridge_circ = AdvancedEnsembleWeighter(MODELS, config_ridge).calculate_base_weights(
        df_circ, parameter="Wind Direction", is_circular=True
    )
    assert abs(sum(w_ridge_circ.values()) - 1.0) < 0.01, "ridge→circ fallback broken"
    print("  OK — ridge redirected to crps for WD, weights sum to 1.0")

    # [G15 test] Full fused-weight path with correct column names
    print("\n[G15: calculate_fused_weights — full fused path]")
    config_f   = WeightConfiguration(method="composite")
    weighter_f = AdvancedEnsembleWeighter(MODELS, config_f)
    fused_w    = weighter_f.calculate_fused_weights(
        df,
        forecast_col="forecast", obs_col="obs",
        model_col="Model", time_col="WITA_Target", lead_col="Lead_Hour",
        parameter="Rainfall", is_circular=False,
    )
    assert abs(sum(fused_w.values()) - 1.0) < 0.01, "fused weights don't sum to 1"
    sig = weighter_f.significance.get("fused", {})
    print(f"  Fused weights OK, DM p-value={sig.get('p_value', 'n/a')}")

    # [G2 test] DM test circular-aware
    print("\n[G2: DM test on circular WD data — must produce finite result]")
    config_c   = WeightConfiguration(method="crps")
    weighter_c = AdvancedEnsembleWeighter(MODELS, config_c)
    fused_wd   = weighter_c.calculate_fused_weights(
        df_circ,
        forecast_col="forecast", obs_col="obs",
        model_col="Model", time_col="WITA_Target", lead_col="Lead_Hour",
        parameter="Wind Direction", is_circular=True,
    )
    sig_wd = weighter_c.significance.get("fused", {})
    dm = sig_wd.get("dm_statistic")
    assert dm is None or np.isfinite(dm), f"DM statistic not finite for WD: {dm}"
    print(f"  WD DM statistic = {dm}, p = {sig_wd.get('p_value', 'n/a')}")

    print("\n[JSON schema validation]")
    bad_json = {"station": "X", "generated": "now"}
    ok, errs = validate_guidance_json(bad_json)
    print(f"  bad JSON → valid={ok}, {len(errs)} error(s)")
    assert not ok

    print("\n✅ All tests passed!")


if __name__ == "__main__":
    quick_test()
