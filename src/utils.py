import numpy as np
import pandas as pd

def tetens_es(T: float) -> float:
    """Saturation vapor pressure (hPa) using Tetens formula."""
    return 6.1078 * 10 ** (7.5 * T / (237.3 + T))

def derive_rh(T: float, Td: float) -> float:
    """
    Calculate RH from T and Td.
    Enforces Td <= T before calculation. Clamps result to [0, 100].
    """
    if pd.isna(T) or pd.isna(Td):
        return np.nan
    Td_safe = min(Td, T)
    rh = 100.0 * tetens_es(Td_safe) / tetens_es(T)
    return max(0.0, min(100.0, rh))

def interpolate_3h_to_1h(series_3h: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """
    Linearly interpolate a 3-hourly series onto a 1-hourly grid.
    Used for GEM and Multi-Model smooth fields (T, Td, P, WS, WD).
    NOT used for Rain or Gusts (those use dedicated downscaling).
    """
    combined = series_3h.reindex(target_index)
    # The first value might be NaN if it starts later, so we backfill/forward fill conditionally
    # or just use interpolation. `limit_direction='both'` helps.
    interpolated = combined.interpolate(method='time', limit_direction='both')
    return interpolated.reindex(target_index)

def circular_weighted_mean(directions: np.ndarray, weights: np.ndarray) -> float:
    """Weighted circular mean for wind direction (degrees)."""
    # Filter out NaNs
    valid = ~(np.isnan(directions) | np.isnan(weights))
    if not np.any(valid):
        return np.nan
        
    directions = np.array(directions)[valid]
    weights = np.array(weights)[valid]
    
    # Normalize weights
    if np.sum(weights) == 0:
        return np.nan
    weights = weights / np.sum(weights)
    
    rad = np.deg2rad(directions)
    x = np.sum(weights * np.cos(rad))
    y = np.sum(weights * np.sin(rad))
    
    # Check if x and y are very close to 0 (all directions cancel out)
    if np.abs(x) < 1e-10 and np.abs(y) < 1e-10:
        return 0.0 # calm
        
    res = np.rad2deg(np.arctan2(y, x)) % 360
    return float(res)
