"""
Diurnal analysis of WAWP observational data.

Outputs:
  - docs/data/diurnal_climatology.json
  - docs/data/diurnal_plots/*.png
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd
try:
    from scipy import stats as scipy_stats
except ModuleNotFoundError:
    scipy_stats = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ModuleNotFoundError:
    plt = None

LOCATION = "Bandara_Sangia_Ni_Bandera"
WITA_OFFSET_HOURS = 8
WET_SEASON_MONTHS = [11, 12, 1, 2, 3, 4]
DRY_SEASON_MONTHS = [5, 6, 7, 8, 9, 10]
PARAMETERS = ["temperature", "dewpoint", "pressure", "humidity", "wind_speed", "wind_gust_max", "wind_dir", "rain_1h"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diurnal")


def load_observations(db_path: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("""
            SELECT obs_time, temperature, dewpoint, humidity, pressure,
                   wind_speed, wind_gust_max, wind_dir, rain_1h, visibility
            FROM awos_observations
            WHERE temperature IS NOT NULL
            ORDER BY obs_time
        """, conn)
    df["datetime_utc"] = pd.to_datetime(df["obs_time"])
    df["datetime_wita"] = df["datetime_utc"] + pd.Timedelta(hours=WITA_OFFSET_HOURS)
    df["hour_wita"] = df["datetime_wita"].dt.hour
    df["month"] = df["datetime_wita"].dt.month
    df["year"] = df["datetime_wita"].dt.year
    df["season"] = df["month"].apply(lambda m: "wet" if m in WET_SEASON_MONTHS else "dry")
    return df


def compute_hourly_climatology(df: pd.DataFrame, param: str) -> dict:
    result = {"hours": list(range(24)), "stats": {}}
    for h in range(24):
        subset = df[df["hour_wita"] == h][param].dropna()
        if len(subset) < 10:
            result["stats"][str(h)] = None
            continue
        result["stats"][str(h)] = {
            "mean": float(subset.mean()),
            "std": float(subset.std()),
            "median": float(subset.median()),
            "p10": float(subset.quantile(0.10)),
            "p90": float(subset.quantile(0.90)),
            "n": int(len(subset)),
        }
    return result


def compute_monthly_hourly_matrix(df: pd.DataFrame, param: str) -> dict:
    matrix = np.full((12, 24), np.nan)
    for m in range(1, 13):
        for h in range(24):
            subset = df[(df["month"] == m) & (df["hour_wita"] == h)][param].dropna()
            if len(subset) >= 5:
                matrix[m - 1, h] = subset.mean()
    return {"matrix": matrix.tolist(), "months": list(range(1, 13)), "hours": list(range(24))}


def compute_rain_diurnal_cycle(df: pd.DataFrame) -> dict:
    freq, intensity = [], []
    for h in range(24):
        subset = df[df["hour_wita"] == h]["rain_1h"].dropna()
        rain_hours = subset[subset > 0.1]
        freq.append(float(len(rain_hours) / len(subset) * 100) if len(subset) else 0.0)
        intensity.append(float(rain_hours.mean()) if len(rain_hours) else 0.0)
    return {"hours": list(range(24)), "frequency_pct": freq, "intensity_mmh": intensity}


def compute_gust_diurnal_cycle(df: pd.DataFrame) -> dict:
    freq, intensity = [], []
    for h in range(24):
        subset = df[df["hour_wita"] == h]
        gust_values = subset["wind_gust_max"].dropna()
        freq.append(float(len(gust_values) / len(subset) * 100) if len(subset) else 0.0)
        intensity.append(float(gust_values.mean()) if len(gust_values) else 0.0)
    return {"hours": list(range(24)), "frequency_pct": freq, "intensity_kt": intensity}


def compute_t_td_spread_cycle(df: pd.DataFrame) -> dict:
    spread = (df["temperature"] - df["dewpoint"]).clip(lower=0)
    tmp = df.copy()
    tmp["t_td_spread"] = spread
    result = {"hours": list(range(24)), "mean_c": [], "p10_c": [], "low_spread_frequency_pct": []}
    for h in range(24):
        subset = tmp[tmp["hour_wita"] == h]["t_td_spread"].dropna()
        if len(subset) == 0:
            result["mean_c"].append(0.0)
            result["p10_c"].append(0.0)
            result["low_spread_frequency_pct"].append(0.0)
            continue
        result["mean_c"].append(float(subset.mean()))
        result["p10_c"].append(float(subset.quantile(0.10)))
        result["low_spread_frequency_pct"].append(float((subset <= 2.0).mean() * 100.0))
    return result


def compute_seasonal_profiles(df: pd.DataFrame) -> dict:
    profiles = {}
    for season in ["wet", "dry"]:
        season_df = df[df["season"] == season]
        if season_df.empty:
            profiles[season] = {}
            continue
        profiles[season] = {
            "n": int(len(season_df)),
            "temperature_mean_c": float(season_df["temperature"].mean()),
            "dewpoint_mean_c": float(season_df["dewpoint"].mean()),
            "humidity_mean_pct": float(season_df["humidity"].mean()),
            "pressure_mean_hpa": float(season_df["pressure"].mean()),
            "wind_speed_mean_kt": float(season_df["wind_speed"].mean()),
            "wind_gust_mean_kt": float(season_df["wind_gust_max"].mean()) if season_df["wind_gust_max"].notna().any() else None,
            "rain_frequency_pct": float((season_df["rain_1h"].fillna(0) > 0.1).mean() * 100.0),
            "rain_intensity_when_wet_mmh": float(season_df.loc[season_df["rain_1h"] > 0.1, "rain_1h"].mean()) if (season_df["rain_1h"] > 0.1).any() else 0.0,
            "peak_rain_hour_wita": int(np.argmax(compute_rain_diurnal_cycle(season_df)["frequency_pct"])),
            "peak_gust_hour_wita": int(np.argmax(compute_gust_diurnal_cycle(season_df)["frequency_pct"])),
            "hourly": {
                "temperature": compute_hourly_climatology(season_df, "temperature"),
                "humidity": compute_hourly_climatology(season_df, "humidity"),
                "wind_speed": compute_hourly_climatology(season_df, "wind_speed"),
                "rain": compute_rain_diurnal_cycle(season_df),
                "gust": compute_gust_diurnal_cycle(season_df),
                "t_td_spread": compute_t_td_spread_cycle(season_df),
            },
        }
    return profiles


def compute_fog_low_cloud_proxy(df: pd.DataFrame) -> dict:
    tmp = df.copy()
    tmp["t_td_spread"] = (tmp["temperature"] - tmp["dewpoint"]).clip(lower=0)
    tmp["risk_score"] = 0.0
    tmp.loc[tmp["humidity"] >= 95, "risk_score"] += 35.0
    tmp.loc[tmp["humidity"] >= 90, "risk_score"] += 20.0
    tmp.loc[tmp["t_td_spread"] <= 1.0, "risk_score"] += 30.0
    tmp.loc[(tmp["t_td_spread"] > 1.0) & (tmp["t_td_spread"] <= 2.0), "risk_score"] += 15.0
    tmp.loc[tmp["wind_speed"] <= 3.0, "risk_score"] += 15.0
    tmp.loc[tmp["hour_wita"].between(4, 8), "risk_score"] += 10.0
    tmp["risk_score"] = tmp["risk_score"].clip(upper=100.0)

    hourly = []
    for h in range(24):
        subset = tmp[tmp["hour_wita"] == h]
        if subset.empty:
            hourly.append({"hour": h, "mean_score": 0.0, "high_risk_frequency_pct": 0.0})
            continue
        hourly.append({
            "hour": h,
            "mean_score": float(subset["risk_score"].mean()),
            "high_risk_frequency_pct": float((subset["risk_score"] >= 60.0).mean() * 100.0),
        })
    peak = max(hourly, key=lambda x: x["mean_score"]) if hourly else {"hour": None, "mean_score": 0.0}
    return {
        "hourly": hourly,
        "peak_hour_wita": peak["hour"],
        "peak_score": peak["mean_score"],
        "inputs": ["relative_humidity", "temperature_dewpoint_spread", "wind_speed", "hour_wita"],
        "note": "Proxy only; AWOS hourly visibility/cloud-base history is sparse in the current archive.",
    }


def compute_wind_rose_data(df: pd.DataFrame) -> dict:
    sectors = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    result = {"sectors": sectors, "wet": [], "dry": []}
    for season in ["wet", "dry"]:
        season_df = df[df["season"] == season]
        for i in range(16):
            low = i * 22.5
            high = low + 22.5
            sector = season_df[(season_df["wind_dir"] >= low) & (season_df["wind_dir"] < high)]
            result[season].append({
                "freq_pct": float(len(sector) / len(season_df) * 100) if len(season_df) else 0.0,
                "mean_speed_kt": float(sector["wind_speed"].mean()) if len(sector) else 0.0,
                "n": int(len(sector)),
            })
    return result


def _circular_mean(series: pd.Series):
    angles = np.deg2rad(series.dropna())
    if len(angles) == 0:
        return None
    return float(np.rad2deg(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) % 360)


def identify_sea_breeze(df: pd.DataFrame) -> dict:
    daytime = df[(df["hour_wita"] >= 10) & (df["hour_wita"] <= 16)]
    nighttime = df[(df["hour_wita"] >= 22) | (df["hour_wita"] <= 4)]
    day_dir = _circular_mean(daytime["wind_dir"])
    night_dir = _circular_mean(nighttime["wind_dir"])
    diff = None
    if day_dir is not None and night_dir is not None:
        diff = abs((day_dir - night_dir + 180) % 360 - 180)
    return {
        "daytime_mean_dir": day_dir,
        "nighttime_mean_dir": night_dir,
        "daytime_mean_speed": float(daytime["wind_speed"].mean()),
        "nighttime_mean_speed": float(nighttime["wind_speed"].mean()),
        "direction_difference_deg": diff,
        "confidence": "high" if diff is not None and diff >= 60 and len(daytime) >= 100 and len(nighttime) >= 100 else ("medium" if diff is not None and diff >= 30 else "low"),
        "daytime_n": int(len(daytime)),
        "nighttime_n": int(len(nighttime)),
    }


def statistical_tests(df: pd.DataFrame) -> dict:
    tests = {}
    rain_by_hour = []
    for h in range(24):
        subset = df[df["hour_wita"] == h]["rain_1h"].dropna()
        rain_by_hour.append([(subset > 0.1).sum(), len(subset) - (subset > 0.1).sum()])
    rain_table = np.array(rain_by_hour, dtype=float)
    if scipy_stats is None:
        tests["rain_chi_square"] = {
            "status": "scipy_unavailable",
            "statistic": None,
            "p_value": None,
            "dof": None,
            "significant": False,
        }
    elif rain_table.sum() == 0 or np.any(rain_table.sum(axis=0) == 0) or np.any(rain_table.sum(axis=1) == 0):
        tests["rain_chi_square"] = {
            "status": "insufficient_variation",
            "statistic": None,
            "p_value": None,
            "dof": None,
            "significant": False,
        }
    else:
        chi2, p_val, dof, _ = scipy_stats.chi2_contingency(rain_table)
        tests["rain_chi_square"] = {"statistic": float(chi2), "p_value": float(p_val), "dof": int(dof), "significant": bool(p_val < 0.05)}

    temp_by_hour = [df[df["hour_wita"] == h]["temperature"].dropna().values for h in range(24)]
    temp_by_hour = [x for x in temp_by_hour if len(x) >= 5]
    if scipy_stats is None or len(temp_by_hour) < 2:
        tests["temp_anova"] = {"status": "scipy_unavailable" if scipy_stats is None else "insufficient_data", "f_statistic": None, "p_value": None, "significant": False}
    else:
        f_stat, p_val = scipy_stats.f_oneway(*temp_by_hour)
        tests["temp_anova"] = {"f_statistic": float(f_stat), "p_value": float(p_val), "significant": bool(p_val < 0.05)}

    wind_by_hour = [df[df["hour_wita"] == h]["wind_speed"].dropna().values for h in range(24)]
    wind_by_hour = [x for x in wind_by_hour if len(x) >= 5]
    if scipy_stats is None or len(wind_by_hour) < 2:
        tests["wind_speed_kruskal"] = {"status": "scipy_unavailable" if scipy_stats is None else "insufficient_data", "h_statistic": None, "p_value": None, "significant": False}
    else:
        h_stat, p_val = scipy_stats.kruskal(*wind_by_hour)
        tests["wind_speed_kruskal"] = {"h_statistic": float(h_stat), "p_value": float(p_val), "significant": bool(p_val < 0.05)}
    return tests


def identify_peak_convective_window(df: pd.DataFrame) -> dict:
    rain_cycle = compute_rain_diurnal_cycle(df)
    gust_cycle = compute_gust_diurnal_cycle(df)
    scores = [rain_cycle["frequency_pct"][h] * 0.5 + gust_cycle["frequency_pct"][h] * 0.5 for h in range(24)]
    best_start, best_score = 0, -1.0
    for start in range(24):
        score = sum(scores[(start + i) % 24] for i in range(6))
        if score > best_score:
            best_start, best_score = start, score
    return {
        "hourly_convective_scores": scores,
        "peak_window_start_wita": best_start,
        "peak_window_end_wita": (best_start + 5) % 24,
        "peak_window_score": float(best_score),
        "peak_window_hours_wita": [(best_start + i) % 24 for i in range(6)],
    }


def build_operational_briefing(df: pd.DataFrame, convective_window: dict, sea_breeze: dict, fog_proxy: dict) -> dict:
    rain_cycle = compute_rain_diurnal_cycle(df)
    gust_cycle = compute_gust_diurnal_cycle(df)
    wet_freq = float((df["rain_1h"].fillna(0) > 0.1).mean() * 100.0)
    peak_rain_hour = int(np.argmax(rain_cycle["frequency_pct"]))
    peak_gust_hour = int(np.argmax(gust_cycle["frequency_pct"]))
    peak_hours = convective_window.get("peak_window_hours_wita", [])
    peak_window = f"{peak_hours[0]:02d}-{peak_hours[-1]:02d} WITA" if peak_hours else "N/A"
    bullets = [
        f"Rain is observed in {wet_freq:.1f}% of hourly records, with the highest rain frequency near {peak_rain_hour:02d} WITA.",
        f"The composite convective window is {peak_window}, combining rain frequency and gust occurrence.",
        f"Peak gust occurrence is near {peak_gust_hour:02d} WITA.",
        f"Sea-breeze signal confidence is {sea_breeze.get('confidence', 'unknown')} with day/night direction separation of {sea_breeze.get('direction_difference_deg'):.1f} degrees." if sea_breeze.get("direction_difference_deg") is not None else "Sea-breeze direction separation could not be estimated.",
        f"Fog/low-cloud proxy peaks near {fog_proxy.get('peak_hour_wita'):02d} WITA." if fog_proxy.get("peak_hour_wita") is not None else "Fog/low-cloud proxy peak is unavailable.",
    ]
    return {
        "rain_frequency_pct": wet_freq,
        "peak_rain_hour_wita": peak_rain_hour,
        "peak_gust_hour_wita": peak_gust_hour,
        "convective_window_label": peak_window,
        "bullets": bullets,
    }


def compute_monthly_v_dry(db_path: str) -> dict:
    try:
        with sqlite3.connect(db_path) as conn:
            check = conn.execute("""
                SELECT COUNT(*) FROM awos_observations
                WHERE visibility IS NOT NULL AND rain_1h < 0.1
            """).fetchone()[0]
            if check == 0:
                return {
                    "status": "no_visibility_data",
                    "note": "AWOS hourly files do not include populated visibility; using 9999 m fallback.",
                    "months": list(range(1, 13)),
                    "v_dry_medians": [9999] * 12,
                }
            df = pd.read_sql("""
                SELECT strftime('%m', obs_time) AS month, visibility
                FROM awos_observations
                WHERE rain_1h < 0.1 AND visibility IS NOT NULL
            """, conn)
        monthly = df.groupby("month")["visibility"].median()
        v_dry = [float(monthly.get(f"{m:02d}", 9999)) for m in range(1, 13)]
        return {
            "status": "computed",
            "months": list(range(1, 13)),
            "v_dry_medians": v_dry,
            "smoke_affected_months": [m for m, v in zip(range(1, 13), v_dry) if v < 9999],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def compute_lcl_seasonal_calibration(df: pd.DataFrame) -> dict:
    if "cloud_base_ft" not in df.columns:
        return {
            "status": "no_cloud_base_data",
            "wet_season": None,
            "dry_season": None,
            "current_multiplier": 400.0,
        }
    return {"status": "not_implemented_for_current_schema", "current_multiplier": 400.0}


def plot_diurnal_climatology(df: pd.DataFrame, output_dir: str) -> None:
    if plt is None:
        log.warning("matplotlib is unavailable; skipping diurnal PNG plots")
        return
    plots_dir = os.path.join(output_dir, "diurnal_plots")
    os.makedirs(plots_dir, exist_ok=True)
    params = [
        ("temperature", "Temperature (C)", "red"),
        ("dewpoint", "Dewpoint (C)", "blue"),
        ("pressure", "Pressure (hPa)", "green"),
        ("humidity", "Humidity (%)", "purple"),
        ("wind_speed", "Wind Speed (kt)", "orange"),
        ("rain_1h", "Rain (mm)", "cyan"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(15, 10), constrained_layout=True)
    for ax, (param, label, color) in zip(axes.flatten(), params):
        hours = list(range(24))
        grouped = [df[df["hour_wita"] == h][param].dropna() for h in hours]
        means = [g.mean() if len(g) else np.nan for g in grouped]
        p10 = [g.quantile(0.10) if len(g) else np.nan for g in grouped]
        p90 = [g.quantile(0.90) if len(g) else np.nan for g in grouped]
        ax.plot(hours, means, color=color, linewidth=2)
        ax.fill_between(hours, p10, p90, color=color, alpha=0.2)
        ax.set_title(label)
        ax.set_xticks(range(0, 24, 3))
        ax.grid(True, alpha=0.3)
    fig.suptitle("WAWP Diurnal Climatology")
    fig.savefig(os.path.join(plots_dir, "diurnal_overview.png"), dpi=150)
    plt.close(fig)

    rain = compute_rain_diurnal_cycle(df)
    fig, ax1 = plt.subplots(figsize=(12, 6), constrained_layout=True)
    ax1.bar(rain["hours"], rain["frequency_pct"], alpha=0.6)
    ax2 = ax1.twinx()
    ax2.plot(rain["hours"], rain["intensity_mmh"], color="red", marker="o")
    ax1.set_title("Rain Diurnal Cycle")
    fig.savefig(os.path.join(plots_dir, "rain_diurnal.png"), dpi=150)
    plt.close(fig)

    wind_rose = compute_wind_rose_data(df)
    fig, axes = plt.subplots(1, 2, figsize=(14, 7), constrained_layout=True, subplot_kw={"projection": "polar"})
    theta = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    for ax, season in zip(axes, ["wet", "dry"]):
        ax.bar(theta, [d["freq_pct"] for d in wind_rose[season]], width=2 * np.pi / 16 * 0.9)
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_title(f"{season.title()} Wind Rose")
    fig.savefig(os.path.join(plots_dir, "wind_rose_seasonal.png"), dpi=150)
    plt.close(fig)

    matrix = np.array(compute_monthly_hourly_matrix(df, "temperature")["matrix"])
    fig, ax = plt.subplots(figsize=(14, 6), constrained_layout=True)
    image = ax.imshow(matrix, aspect="auto", cmap="RdYlBu_r", origin="lower")
    fig.colorbar(image, ax=ax, label="Temperature (C)")
    ax.set_title("Temperature Monthly x Hourly")
    fig.savefig(os.path.join(plots_dir, "temp_monthly_hourly.png"), dpi=150)
    plt.close(fig)


def generate_diurnal_analysis(db_path: str, output_dir: str) -> dict:
    df = load_observations(db_path)
    climatology = {param: compute_hourly_climatology(df, param) for param in PARAMETERS if param in df.columns}
    rain_cycle = compute_rain_diurnal_cycle(df)
    gust_cycle = compute_gust_diurnal_cycle(df)
    sea_breeze = identify_sea_breeze(df)
    convective_window = identify_peak_convective_window(df)
    fog_proxy = compute_fog_low_cloud_proxy(df)
    payload = {
        "metadata": {
            "station": LOCATION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_period": {
                "start": df["datetime_wita"].min().isoformat() if not df.empty else None,
                "end": df["datetime_wita"].max().isoformat() if not df.empty else None,
            },
            "total_observations": int(len(df)),
        },
        "climatology": climatology,
        "rain_diurnal_cycle": rain_cycle,
        "gust_diurnal_cycle": gust_cycle,
        "t_td_spread_cycle": compute_t_td_spread_cycle(df),
        "wind_rose": compute_wind_rose_data(df),
        "sea_breeze_regime": sea_breeze,
        "statistical_tests": statistical_tests(df),
        "convective_window": convective_window,
        "fog_low_cloud_proxy": fog_proxy,
        "seasonal_profiles": compute_seasonal_profiles(df),
        "operational_briefing": build_operational_briefing(df, convective_window, sea_breeze, fog_proxy),
        "monthly_hourly_matrices": {
            param: compute_monthly_hourly_matrix(df, param)
            for param in ["temperature", "dewpoint", "rain_1h", "wind_speed", "wind_gust_max", "humidity", "pressure"]
            if param in df.columns
        },
        "monthly_v_dry": compute_monthly_v_dry(db_path),
        "lcl_seasonal_calibration": compute_lcl_seasonal_calibration(df),
        "season_definitions": {
            "wet_season_months": WET_SEASON_MONTHS,
            "dry_season_months": DRY_SEASON_MONTHS,
        },
    }
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "diurnal_climatology.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    plot_diurnal_climatology(df, output_dir)
    return payload


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(root, "wawp_forecasts.db")
    output_dir = os.path.join(root, "docs", "data")
    payload = generate_diurnal_analysis(db_path, output_dir)
    log.info(f"Generated diurnal analysis for {payload['metadata']['total_observations']} observations")


if __name__ == "__main__":
    main()
