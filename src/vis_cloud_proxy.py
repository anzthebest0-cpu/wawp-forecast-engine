"""
vis_cloud_proxy.py

Physics-based algorithms to dynamically estimate Visibility and Cloud Amount/Base
using temperature, dewpoint, pressure, and rainfall, specifically tuned for
aviation forecasts (TAF).
"""

import math

def estimate_visibility(rain_mmh, rh_pct, temp_c, dewpoint_c,
                        wind_kt, pressure_hpa, pressure_trend_hpa_3h,
                        baseline_dry_vis_m=9999):
    """
    Estimate horizontal visibility [m] from meteorological parameters.
    Rain branch coefficients calibrated for tropical maritime convective rain.
    """
    dd = temp_c - dewpoint_c
    R = rain_mmh

    # Pressure factor (only active in moist/precip regime)
    if R <= 0.1 and rh_pct < 85.0:
        pressure_factor = 1.0
    else:
        if pressure_trend_hpa_3h <= -2.5:
            pressure_factor = 0.92
        elif pressure_trend_hpa_3h <= -1.5:
            pressure_factor = 0.96
        elif pressure_trend_hpa_3h >= 1.5:
            pressure_factor = 1.05
        else:
            pressure_factor = 1.0

    # Rain branch - tropical convective DSD calibration (continuous 2-tier model)
    if R >= 0.4:
        if R < 7.5:
            vis = 6500.0 * (R ** -0.55)
        else:
            vis = 7581.0 * (R ** -0.63)
        vis *= pressure_factor
        return int(max(200.0, min(vis, 9999.0)))
    
    if R > 0.1:
        # Drizzle: minimal extinction
        vis = 7000.0 * (R ** -0.25)
        vis *= pressure_factor
        return int(max(200.0, min(vis, 9999.0)))

    # No-rain branches
    if rh_pct >= 95.0 and dd <= 1.5:
        # Fog/Mist conditions
        x = 100.0 - rh_pct
        vis = 120.0 * x + 0.5 * x**2
        vis = max(50.0, min(vis, 3000.0))
        vis *= pressure_factor
        return int(max(50.0, min(vis, 5000.0)))

    if rh_pct >= 90.0 and dd <= 3.0:
        # Hazy morning conditions
        vis = 6000.0
        vis *= pressure_factor
        return int(max(3000.0, min(vis, 8000.0)))

    # Dry/haze regime
    vis = float(baseline_dry_vis_m)
    if wind_kt > 10.0:
        vis *= 1.2
    elif wind_kt < 3.0:
        vis *= 0.90
    vis *= pressure_factor
    if pressure_hpa > 1025 and pressure_trend_hpa_3h > 0:
        vis *= 0.9
    return int(min(vis, 9999.0))


def get_weather_phenomenon(rain_mmh, rh_pct, temp_c, dewpoint_c, vis_m, local_hour_wita):
    """
    Returns the appropriate ICAO weather phenomenon code for the TAF change group.
    Ensures that if vis_m < 5000m, a weather phenomenon is provided (unless conditions fall through).
    """
    dd = temp_c - dewpoint_c

    # Precipitation
    if rain_mmh > 0.1:
        if rain_mmh >= 10.0:
            # TSRA proxy: daytime/afternoon + heavy rain + saturated air
            if 11 <= local_hour_wita <= 21 and rh_pct >= 90.0:
                return "TSRA"
            else:
                return "+RA"
        elif rain_mmh >= 2.5:
            return "RA"
        else:
            # Light rain (-RA) is not a significant weather criteria for change groups
            return ""

    # Fog / Mist / Haze
    if vis_m < 1000.0 and rh_pct >= 95.0 and dd <= 1.5:
        return "FG"
    if vis_m < 5000.0 and rh_pct >= 80.0:
        return "BR"
    if vis_m < 5000.0 and rh_pct < 75.0 and temp_c >= 28.0:
        return "HZ"

    return ""


def estimate_lcl_ft_pressure(temp_c, dewpoint_c, pressure_hpa):
    dd = max(0.0, temp_c - dewpoint_c)
    if dd < 0.5:
        return 0.0  # treat as fog at surface

    pressure_ratio = 1013.25 / pressure_hpa
    factor = 400.0 * (pressure_ratio ** 0.15)
    lcl_ft_simple = factor * dd

    return max(0.0, min(lcl_ft_simple, 25000.0))


def estimate_cloud_group_with_pressure(rh_pct, temp_c, dewpoint_c,
                                       rain_mmh, vis_m, pressure_hpa,
                                       pressure_trend_hpa_3h,
                                       lcl_ft=None, consensus_hour=None):
    dd = temp_c - dewpoint_c

    if vis_m < 1000.0 and rh_pct >= 95.0 and dd <= 1.5:
        return "OVC000"

    if lcl_ft is None:
        lcl_ft = estimate_lcl_ft_pressure(temp_c, dewpoint_c, pressure_hpa)

    lcl_ft = max(100.0, lcl_ft)
    base_hundreds = int(round(lcl_ft / 100.0))
    base_str = f"{base_hundreds:03d}"

    # Base amount from actual consensus percentages if available
    low_pct = consensus_hour.get('low_clouds', 0.0) if consensus_hour else 0.0
    mid_pct = consensus_hour.get('mid_clouds', 0.0) if consensus_hour else 0.0
    
    # Decide which cloud layer is dominant for the ceiling
    target_pct = low_pct if low_pct > 15.0 else max(low_pct, mid_pct)
    
    if target_pct > 0:
        if target_pct >= 88.0:
            amount = "OVC"
        elif target_pct >= 51.0:
            amount = "BKN"
        elif target_pct >= 26.0:
            amount = "SCT"
        elif target_pct >= 5.0:
            amount = "FEW"
        else:
            amount = "NSC"
            if rain_mmh > 0.1: amount = "FEW" # force cloud if raining
    else:
        # Fallback to RH if no percentage data
        if rain_mmh > 0.1:
            if rh_pct >= 90.0: amount = "OVC"
            elif rh_pct >= 80.0: amount = "BKN"
            else: amount = "SCT"
        else:
            if rh_pct >= 95.0: amount = "BKN"
            elif rh_pct >= 85.0: amount = "SCT"
            elif rh_pct >= 70.0: amount = "FEW"
            else: amount = "NSC"

    # Pressure-trend adjustments
    if pressure_trend_hpa_3h < -2.0:
        if amount == "FEW": amount = "SCT"
        elif amount == "SCT": amount = "BKN"
        elif amount == "BKN": amount = "OVC"

    if amount == "NSC":
        return "NSC"
        
    return f"{amount}{base_str}"


def build_hourly_vis_cloud(consensus_hour, pressure_history):
    T  = consensus_hour["temp_c"]
    Td = consensus_hour["dewpoint_c"]
    P  = consensus_hour.get("pressure_hpa", 1013.25)
    R  = consensus_hour.get("rain", 0.0)
    U  = consensus_hour.get("spd", 0.0)
    RH = consensus_hour.get("relative_humidity_pct", 80.0)

    if len(pressure_history) >= 3:
        pressure_trend_3h = P - pressure_history[-3]
    else:
        pressure_trend_3h = 0.0

    # WAWP baseline dry visibility
    baseline_dry_vis = 9999

    vis_m = estimate_visibility(R, RH, T, Td, U, P, pressure_trend_3h,
                                baseline_dry_vis_m=baseline_dry_vis)

    # Apply standard aviation visibility rounding
    if vis_m >= 9999:
        vis_code = "9999"
    elif vis_m >= 5000:
        vis_code = f"{int((vis_m // 1000) * 1000):04d}"
    elif vis_m >= 800:
        vis_code = f"{int((vis_m // 100) * 100):04d}"
    else:
        vis_code = f"{int((vis_m // 50) * 50):04d}"

    cloud_group = estimate_cloud_group_with_pressure(
        rh_pct=RH,
        temp_c=T,
        dewpoint_c=Td,
        rain_mmh=R,
        vis_m=vis_m,
        pressure_hpa=P,
        pressure_trend_hpa_3h=pressure_trend_3h,
        consensus_hour=consensus_hour
    )

    return vis_code, cloud_group
