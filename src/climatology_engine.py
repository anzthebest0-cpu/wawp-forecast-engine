"""
WAWP Climatology Engine  v2.0
==============================
Produces wawp_climatology.json consumed by tafor_generator.py and
(optionally) dashboard_updated.py.

Changes over v1.0
----------------─
[BUG FIXES]
  - wind_dir_dominant now uses inline circular mean (sin/cos) instead of
    linear mode rounding - eliminates the 0°/360° wrap artefact.
  - seasonal["wind_dir_freq"] now stores NUMERIC degree string keys ("0",
    "22", "45", ...) matching 16-sector bin centres, instead of compass-
    string keys ("N", "NNE", ...).  This makes the data directly usable by
    Chart.js polarArea and tafor_generator without a key-type translation.
  - Wind rose data is now derived from the full per-observation distribution
    (not from counting dominant-direction codes across months/hours).

[NEW DATA BLOCKS]
  - seasonal["wind_speed_by_sector"]   - per-sector median wind speed (kt)
  - seasonal["dominant_dir_deg"]       - circular mean direction (numeric °)
  - seasonal["conditional_rain_median_mm"] / ["conditional_rain_p90_mm"]
      Rain-amount statistics conditioned on rain > 0.1 mm.  Useful for
      deciding RA vs TSRA and for intensity language in the briefing.
  - seasonal_hourly[season][hour_utc]  - intersection of season x UTC hour,
      giving tafor_generator a climatological reference for every validity
      step (direction, speed, rain frequency, conditional rain amount).
  - interannual_variability[season]    - std-dev of annual seasonal means,
      so the dashboard can flag when model bias exceeds natural variability.

[STRUCTURE]
  - All circular arithmetic uses a single local helper `_circ_mean()` so
    the module has zero external dependencies beyond pandas / numpy.
  - A data-quality report is printed at the end summarising coverage per
    season and any months/hours with < 30 observations.
"""

import pandas as pd
import numpy as np
import os
import glob
import json
from datetime import timedelta

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

AWOS_DIR    = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "awos")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "wawp_climatology.json")

SEASONS: dict[str, list[int]] = {
    "DJF": [12, 1, 2],
    "MAM": [3,  4, 5],
    "JJA": [6,  7, 8],
    "SON": [9, 10, 11],
}

# 16-sector bins - 22.5° wide - stored with their centre degree as the key.
# Centre degrees: N=0, NNE=22, NE=45, ENE=67, E=90, ESE=112, SE=135,
#                 SSE=157, S=180, SSW=202, SW=225, WSW=247, W=270,
#                 WNW=292, NW=315, NNW=337
SECTOR_DEFS: dict[str, tuple[float, float, int]] = {
    # name: (low_bound, high_bound, centre_deg)
    "N":   (348.75,  11.25,   0),
    "NNE": ( 11.25,  33.75,  22),
    "NE":  ( 33.75,  56.25,  45),
    "ENE": ( 56.25,  78.75,  67),
    "E":   ( 78.75, 101.25,  90),
    "ESE": (101.25, 123.75, 112),
    "SE":  (123.75, 146.25, 135),
    "SSE": (146.25, 168.75, 157),
    "S":   (168.75, 191.25, 180),
    "SSW": (191.25, 213.75, 202),
    "SW":  (213.75, 236.25, 225),
    "WSW": (236.25, 258.75, 247),
    "W":   (258.75, 281.25, 270),
    "WNW": (281.25, 303.75, 292),
    "NW":  (303.75, 326.25, 315),
    "NNW": (326.25, 348.75, 337),
}

# Ordered list of (centre_deg, low, high) for vectorised assignment
_SECTOR_CENTRES: list[int] = [v[2] for v in SECTOR_DEFS.values()]
_SECTOR_LOWS:    list[float] = [v[0] for v in SECTOR_DEFS.values()]
_SECTOR_HIGHS:   list[float] = [v[1] for v in SECTOR_DEFS.values()]

RAIN_THRESHOLD = 0.1   # mm - minimum to count as "raining"


# ==============================================================================
# 2. CIRCULAR STATISTICS HELPERS
# ==============================================================================

def _circ_mean(angles_deg: np.ndarray) -> float:
    """
    Circular mean of an array of angles in degrees.
    Handles the 0°/360° boundary correctly via sin/cos unit-vector
    decomposition.  Returns a value in [0, 360).
    """
    valid = angles_deg[~np.isnan(angles_deg)]
    if len(valid) == 0:
        return float("nan")
    rad = np.deg2rad(valid)
    mean_rad = np.arctan2(np.sum(np.sin(rad)), np.sum(np.cos(rad)))
    return float(np.rad2deg(mean_rad) % 360)


def _circ_std(angles_deg: np.ndarray) -> float:
    """
    Circular standard deviation in degrees (Mardia & Jupp).
    Returns 0 for a perfectly concentrated distribution, ~180 for uniform.
    """
    valid = angles_deg[~np.isnan(angles_deg)]
    if len(valid) == 0:
        return float("nan")
    rad = np.deg2rad(valid)
    R = np.sqrt(np.sum(np.sin(rad))**2 + np.sum(np.cos(rad))**2) / len(valid)
    R = min(R, 1.0 - 1e-12)
    return float(np.rad2deg(np.sqrt(-2.0 * np.log(R))))


def _assign_sector(wd_series: pd.Series) -> pd.Series:
    """
    Map each wind-direction observation (0-360°) to its 16-sector centre
    degree.  Returns a Series of integers.
    """
    wd = wd_series.values % 360.0
    result = np.full(len(wd), -1, dtype=int)

    for ctr, lo, hi in zip(_SECTOR_CENTRES, _SECTOR_LOWS, _SECTOR_HIGHS):
        if lo > hi:  # N sector wraps around 0°
            mask = (wd >= lo) | (wd < hi)
        else:
            mask = (wd >= lo) & (wd < hi)
        result[mask] = ctr

    # Fallback: anything not assigned (shouldn't happen) -> nearest centre
    unassigned = result == -1
    if unassigned.any():
        for idx in np.where(unassigned)[0]:
            diffs = [min(abs(wd[idx] - c), 360 - abs(wd[idx] - c))
                     for c in _SECTOR_CENTRES]
            result[idx] = _SECTOR_CENTRES[int(np.argmin(diffs))]

    return pd.Series(result, index=wd_series.index)


# ==============================================================================
# 3. DATA HARVESTING
# ==============================================================================

def _load_awos_data() -> pd.DataFrame | None:
    """
    Read all AWOS .dat files and return a single cleaned DataFrame with
    columns [UTC, WITA, Month, Year, Hour_WITA, Hour_UTC, WD, WS, Rain, Temp].
    Returns None if no files are found.
    """
    search_path = os.path.join(AWOS_DIR, "**", "*.dat")
    files = glob.glob(search_path, recursive=True)

    if not files:
        print("[FAIL]  No AWOS .dat files found. Check AWOS_DIR path.")
        return None

    frames: list[pd.DataFrame] = []
    skipped = 0

    for fp in files:
        try:
            df = pd.read_csv(
                fp,
                sep=r"\s+",
                skiprows=4,
                header=None,
                usecols=[1, 2, 5, 8, 9, 12],
                names=["Date", "Hour", "Temp", "WD", "WS", "Rain"],
            )
            # Unit scaling (AWOS stores 1/10th units for Temp and Rain)
            df["Temp"] = pd.to_numeric(df["Temp"], errors="coerce") / 10.0
            df["Rain"] = pd.to_numeric(df["Rain"], errors="coerce") / 10.0
            df["WS"]   = pd.to_numeric(df["WS"],   errors="coerce")   # knots
            df["WD"]   = pd.to_numeric(df["WD"],   errors="coerce")   # degrees

            # Build UTC timestamp
            df["UTC"] = pd.to_datetime(
                df["Date"].astype(str) + df["Hour"].astype(str).str.zfill(2),
                format="%Y%m%d%H",
                errors="coerce",
            )
            df.dropna(subset=["UTC"], inplace=True)

            # WITA = UTC + 8 h
            df["WITA"]      = df["UTC"] + timedelta(hours=8)
            df["Month"]     = df["WITA"].dt.month
            df["Year"]      = df["WITA"].dt.year
            df["Hour_WITA"] = df["WITA"].dt.hour
            df["Hour_UTC"]  = df["UTC"].dt.hour

            frames.append(
                df[["UTC", "WITA", "Month", "Year",
                    "Hour_WITA", "Hour_UTC", "WD", "WS", "Rain", "Temp"]]
            )
        except Exception as exc:
            skipped += 1
            # Uncomment for debugging: print(f"  Skipped {fp}: {exc}")

    if not frames:
        print("[FAIL]  Data ingestion produced zero valid rows.")
        return None

    master = (
        pd.concat(frames)
        .drop_duplicates(subset=["UTC"])
        .sort_values("UTC")
        .reset_index(drop=True)
    )

    # Remove physically impossible values
    master = master[
        (master["WD"].between(0, 360) | master["WD"].isna()) &
        (master["WS"].between(0, 100) | master["WS"].isna()) &
        (master["Rain"].between(0, 500) | master["Rain"].isna()) &
        (master["Temp"].between(0, 50)  | master["Temp"].isna())
    ].copy()

    print(f"[OK]  Loaded {len(master):,} valid hourly observations "
          f"({skipped} files skipped).")
    return master


# ==============================================================================
# 4. STATISTICAL HELPERS
# ==============================================================================

def _safe_quantile(series: pd.Series, q: float, default=0.0) -> float:
    clean = series.dropna()
    if clean.empty:
        return default
    return round(float(clean.quantile(q)), 2)


def _safe_median(series: pd.Series, default=0.0) -> float:
    clean = series.dropna()
    if clean.empty:
        return default
    return round(float(clean.median()), 2)


def _safe_circ_mean(series: pd.Series) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return round(_circ_mean(clean.values), 1)


def _safe_circ_std(series: pd.Series) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return round(_circ_std(clean.values), 1)


def _conditional_rain_stats(rain: pd.Series) -> dict:
    """Statistics for rain rate given that it is raining (>= RAIN_THRESHOLD)."""
    raining = rain[rain >= RAIN_THRESHOLD].dropna()
    if raining.empty:
        return {"median_mm": 0.0, "p90_mm": 0.0, "n_events": 0}
    return {
        "median_mm": round(float(raining.median()), 2),
        "p90_mm":    round(float(raining.quantile(0.90)), 2),
        "n_events":  int(len(raining)),
    }


# ==============================================================================
# 5. MONTHLY x HOURLY CLIMATOLOGY
# ==============================================================================

def _build_monthly_hourly(master: pd.DataFrame) -> dict:
    """
    Returns monthly[str(month)][str(hour_WITA)] with per-variable stats.
    Wind direction uses circular mean for dominant direction.
    """
    db: dict = {}
    quality_warnings: list[str] = []

    for month in range(1, 13):
        db[str(month)] = {}
        month_df = master[master["Month"] == month]

        for hour in range(24):
            hour_df = month_df[month_df["Hour_WITA"] == hour]
            n = len(hour_df)

            if n == 0:
                continue

            if n < 30:
                quality_warnings.append(
                    f"  ⚠ Month={month:02d} Hour_WITA={hour:02d}: only {n} obs"
                )

            # Wind direction - circular mean
            wd_clean = hour_df["WD"].dropna()
            dom_dir  = round(_circ_mean(wd_clean.values)) if not wd_clean.empty else None
            circ_sd  = _safe_circ_std(wd_clean)

            rain_stats = _conditional_rain_stats(hour_df["Rain"])

            db[str(month)][str(hour)] = {
                "n_obs": n,
                "temperature": {
                    "p10":    _safe_quantile(hour_df["Temp"], 0.10),
                    "median": _safe_median(hour_df["Temp"]),
                    "p90":    _safe_quantile(hour_df["Temp"], 0.90),
                },
                "wind_speed": {
                    "p10":         _safe_quantile(hour_df["WS"], 0.10),
                    "median":      _safe_median(hour_df["WS"]),
                    "p90":         _safe_quantile(hour_df["WS"], 0.90),
                    "max_recorded":round(float(hour_df["WS"].max()), 1)
                            if not hour_df["WS"].dropna().empty else 0.0,
                },
                "wind_dir_dominant_deg": dom_dir,   # numeric, circular mean
                "wind_dir_circ_std_deg": circ_sd,
                "rain_frequency_pct":    round(float(
                    (hour_df["Rain"] >= RAIN_THRESHOLD).mean() * 100), 1),
                "conditional_rain":      rain_stats,
            }

    if quality_warnings:
        print("\n".join(quality_warnings))

    return db


# ==============================================================================
# 6. SEASONAL CLIMATOLOGY
# ==============================================================================

def _build_seasonal(master: pd.DataFrame) -> dict:
    """
    Returns seasonal[season_name] with:
      wind_dir_freq           - dict {str(centre_deg): pct}   ← numeric keys
      wind_speed_by_sector    - dict {str(centre_deg): median_kt}
      wind_speed_median/p90
      dominant_dir_deg        - circular mean of all obs (numeric)
      rain_frequency_pct
      conditional_rain_*
      temperature_median
    """
    db: dict = {}

    for season, months in SEASONS.items():
        s_df = master[master["Month"].isin(months)].copy()
        if s_df.empty:
            print(f"⚠ Season {season}: no data.")
            continue

        n_total = len(s_df)

        # -- Wind direction frequency distribution (numeric-keyed) ------------─
        wd_valid = s_df["WD"].dropna()
        if wd_valid.empty:
            dir_freq: dict[str, float] = {str(c): 0.0 for c in _SECTOR_CENTRES}
            speed_by_sector: dict[str, float] = {str(c): 0.0 for c in _SECTOR_CENTRES}
            dominant_deg = None
        else:
            sectors = _assign_sector(wd_valid.rename("WD"))
            # Merge back wind speed onto the same index
            ws_indexed = s_df["WS"].reindex(wd_valid.index)

            counts = sectors.value_counts()
            total_count = counts.sum()

            dir_freq = {}
            speed_by_sector = {}
            for ctr in _SECTOR_CENTRES:
                cnt = int(counts.get(ctr, 0))
                dir_freq[str(ctr)] = round(cnt / total_count * 100, 2)

                # Per-sector median wind speed
                mask = (sectors == ctr)
                ws_sector = ws_indexed[mask].dropna()
                speed_by_sector[str(ctr)] = (
                    round(float(ws_sector.median()), 2) if not ws_sector.empty else 0.0
                )

            # Circular mean dominant direction
            dominant_deg = round(_circ_mean(wd_valid.values), 1)

        # -- Wind speed overall ------------------------------------------------
        ws_series = s_df["WS"].dropna()
        ws_median = _safe_median(ws_series)
        ws_p90    = _safe_quantile(ws_series, 0.90)

        # -- Rain ------------------------------------------------------------─
        rain_series = s_df["Rain"].dropna()
        rain_freq   = round(float((rain_series >= RAIN_THRESHOLD).mean() * 100), 1)
        cond_rain   = _conditional_rain_stats(rain_series)

        # -- Temperature ------------------------------------------------------
        temp_median = _safe_median(s_df["Temp"].dropna())

        db[season] = {
            "n_obs":                   n_total,
            "wind_dir_freq":           dir_freq,           # numeric str keys!
            "wind_speed_by_sector":    speed_by_sector,    # numeric str keys!
            "dominant_dir_deg":        dominant_deg,
            "wind_speed_median":       ws_median,
            "wind_speed_p90":          ws_p90,
            "rain_frequency_pct":      rain_freq,
            "conditional_rain_median_mm": cond_rain["median_mm"],
            "conditional_rain_p90_mm":    cond_rain["p90_mm"],
            "conditional_rain_n_events":  cond_rain["n_events"],
            "temperature_median":      temp_median,
        }

    return db


# ==============================================================================
# 7. SEASONAL x UTC-HOUR CLIMATOLOGY  (new block)
# ==============================================================================

def _build_seasonal_hourly(master: pd.DataFrame) -> dict:
    """
    Returns seasonal_hourly[season][str(utc_hour)] with per-variable stats.
    This is the block used by tafor_generator to do per-hour sanity checks:
    for each validity hour, compare the consensus to the climatological
    expectation at that season + UTC hour combination.
    """
    db: dict = {}

    for season, months in SEASONS.items():
        s_df = master[master["Month"].isin(months)].copy()
        if s_df.empty:
            continue

        db[season] = {}

        for utc_h in range(24):
            h_df = s_df[s_df["Hour_UTC"] == utc_h]
            n = len(h_df)

            if n < 10:
                # Too few obs to compute reliable statistics
                db[season][str(utc_h)] = {"n_obs": n, "insufficient_data": True}
                continue

            wd_valid = h_df["WD"].dropna()
            dom_dir  = round(_circ_mean(wd_valid.values), 1) if not wd_valid.empty else None
            circ_sd  = _safe_circ_std(wd_valid)
            rain_stats = _conditional_rain_stats(h_df["Rain"])

            db[season][str(utc_h)] = {
                "n_obs":                     n,
                "wind_dir_circular_mean_deg": dom_dir,
                "wind_dir_circ_std_deg":     circ_sd,
                "wind_speed_median_kt":      _safe_median(h_df["WS"]),
                "wind_speed_p90_kt":         _safe_quantile(h_df["WS"], 0.90),
                "rain_frequency_pct":        round(float(
                    (h_df["Rain"] >= RAIN_THRESHOLD).mean() * 100), 1),
                "conditional_rain_median_mm": rain_stats["median_mm"],
                "conditional_rain_p90_mm":    rain_stats["p90_mm"],
                "conditional_rain_n_events":  rain_stats["n_events"],
            }

    return db


# ==============================================================================
# 8. INTER-ANNUAL VARIABILITY  (new block)
# ==============================================================================

def _build_interannual(master: pd.DataFrame) -> dict:
    """
    Returns interannual_variability[season] with the std-dev of each
    season's annual means.  Useful for calibrating "is this model bias
    large relative to natural variability?"
    """
    db: dict = {}

    for season, months in SEASONS.items():
        s_df = master[master["Month"].isin(months)].copy()
        if s_df.empty:
            continue

        annual_rows: list[dict] = []
        for year, y_df in s_df.groupby("Year"):
            wd_clean = y_df["WD"].dropna()
            annual_rows.append({
                "year":           year,
                "rain_freq_pct":  round(float(
                    (y_df["Rain"] >= RAIN_THRESHOLD).mean() * 100), 2),
                "ws_mean":        round(float(y_df["WS"].mean()), 2),
                "temp_mean":      round(float(y_df["Temp"].mean()), 2),
                "wd_circ_mean":   round(_circ_mean(wd_clean.values), 1)
                                  if not wd_clean.empty else None,
            })

        if len(annual_rows) < 2:
            continue

        ann_df = pd.DataFrame(annual_rows)
        db[season] = {
            "n_years":          len(ann_df),
            "rain_freq_std":    round(float(ann_df["rain_freq_pct"].std()), 2),
            "ws_std":           round(float(ann_df["ws_mean"].std()), 2),
            "temp_std":         round(float(ann_df["temp_mean"].std()), 2),
            "annual_summary":   ann_df.to_dict(orient="records"),
        }

    return db


# ==============================================================================
# 9. DATA-QUALITY REPORT
# ==============================================================================

def _quality_report(master: pd.DataFrame, seasonal: dict) -> None:
    print("\n📋  DATA QUALITY REPORT")
    print("─" * 50)
    print(f"  Total observations : {len(master):,}")
    print(f"  Date range         : {master['UTC'].min().date()} -> {master['UTC'].max().date()}")
    print(f"  Years covered      : {sorted(master['Year'].unique())}")
    print(f"  WD missing         : {master['WD'].isna().sum():,} rows")
    print(f"  WS missing         : {master['WS'].isna().sum():,} rows")
    print(f"  Rain missing       : {master['Rain'].isna().sum():,} rows")
    print()

    for season, data in seasonal.items():
        months = SEASONS[season]
        n = data.get("n_obs", 0)
        dom = data.get("dominant_dir_deg", "?")
        ws  = data.get("wind_speed_median", 0)
        rf  = data.get("rain_frequency_pct", 0)
        print(f"  {season} (months {months}):")
        print(f"    n={n:,}  dominant={dom}°  ws_med={ws}kt  rain_freq={rf}%")

    print("─" * 50)


# ==============================================================================
# 10. MAIN ENTRY POINT
# ==============================================================================

def generate_wawp_climatology() -> None:
    print("🚀  WAWP Climatology Engine v2.0 - starting…")

    master = _load_awos_data()
    if master is None:
        return

    print("   Building monthly x hourly climatology…")
    monthly_db = _build_monthly_hourly(master)

    print("   Building seasonal climatology…")
    seasonal_db = _build_seasonal(master)

    print("   Building seasonal x UTC-hour climatology…")
    seasonal_hourly_db = _build_seasonal_hourly(master)

    print("   Computing inter-annual variability…")
    interannual_db = _build_interannual(master)

    _quality_report(master, seasonal_db)

    climatology_db = {
        "engine_version":       "2.0",
        "generated":            pd.Timestamp.now().isoformat(),
        "station":              "WAWP (Bandara Sangia Ni Bandera)",
        "rain_threshold_mm":    RAIN_THRESHOLD,
        "monthly":              monthly_db,
        "seasonal":             seasonal_db,
        "seasonal_hourly":      seasonal_hourly_db,
        "interannual_variability": interannual_db,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(climatology_db, f, indent=2, ensure_ascii=False)

    print(f"\n📦  Climatology locked -> {OUTPUT_FILE}")
    print("   Blocks: monthly, seasonal, seasonal_hourly, interannual_variability")
    print("   Wind dir uses circular mean  |  Sector keys are numeric degrees")


if __name__ == "__main__":
    generate_wawp_climatology()
