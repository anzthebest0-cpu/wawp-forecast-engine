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
    dd = temp_c - dewpoint_c

    # Pressure factor
    if pressure_trend_hpa_3h > 1.0:
        pressure_factor = 1.15
    elif pressure_trend_hpa_3h < -1.0:
        pressure_factor = 0.80
    else:
        pressure_factor = 1.0

    if abs(pressure_hpa - 1013.25) > 15:
        pressure_factor *= 1.05

    # Rain branch
    if rain_mmh > 0.1:
        if rain_mmh < 0.5:
            vis = 2000.0 / (rain_mmh ** 0.4)
        else:
            vis = 1000.0 / (rain_mmh ** 0.6)
        if rh_pct >= 90.0:
            vis *= 0.85
        vis *= pressure_factor
        return int(max(100.0, min(vis, 9999.0)))

    # No-rain branch
    if rh_pct >= 95.0 and dd <= 1.5:
        x = 100.0 - rh_pct
        vis = 120.0 * x + 0.5 * x**2
        vis = max(50.0, min(vis, 1000.0))
        vis *= pressure_factor
        return int(max(50.0, min(vis, 1000.0)))

    if rh_pct >= 80.0 and dd <= 4.0:
        x = 100.0 - rh_pct
        vis = 150.0 * x + 0.3 * x**2
        vis = max(1000.0, min(vis, 5000.0))
        vis *= pressure_factor
        return int(max(1000.0, min(vis, 5000.0)))

    # Dry/haze regime
    vis = float(baseline_dry_vis_m)
    if wind_kt > 10.0:
        vis *= 1.2
    elif wind_kt < 3.0:
        vis *= 0.85
    vis *= pressure_factor
    if pressure_hpa > 1025 and pressure_trend_hpa_3h > 0:
        vis *= 0.9
    return int(min(vis, 9999.0))


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
                                       lcl_ft=None):
    dd = temp_c - dewpoint_c

    if vis_m < 1000.0 and rh_pct >= 95.0 and dd <= 1.5:
        return "OVC000"

    if lcl_ft is None:
        lcl_ft = estimate_lcl_ft_pressure(temp_c, dewpoint_c, pressure_hpa)

    lcl_ft = max(100.0, lcl_ft)
    base_hundreds = int(round(lcl_ft / 100.0))
    base_str = f"{base_hundreds:03d}"

    # Base amount from RH and rain
    if rain_mmh > 0.1:
        if rh_pct >= 90.0:
            amount = "OVC"
        elif rh_pct >= 80.0:
            amount = "BKN"
        else:
            amount = "SCT"
    else:
        if rh_pct >= 90.0:
            amount = "OVC"
        elif rh_pct >= 80.0:
            amount = "BKN"
        elif rh_pct >= 70.0:
            amount = "SCT"
        elif rh_pct >= 50.0:
            amount = "FEW"
        else:
            return "NSC"

    # Pressure-trend adjustments
    if pressure_trend_hpa_3h < -2.0:
        if amount == "FEW":
            amount = "SCT"
        elif amount == "SCT":
            amount = "BKN"
        elif amount == "BKN":
            amount = "OVC"
    elif pressure_trend_hpa_3h > 2.0:
        if amount == "OVC":
            amount = "BKN"
        elif amount == "BKN":
            amount = "SCT"

    if pressure_hpa > 1025 and rain_mmh <= 0.1 and amount == "OVC":
        amount = "BKN"

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

    vis_code = "9999" if vis_m >= 9999 else f"{int(vis_m):04d}"

    lcl_ft = estimate_lcl_ft_pressure(T, Td, P)
    cloud_group = estimate_cloud_group_with_pressure(
        rh_pct=RH,
        temp_c=T,
        dewpoint_c=Td,
        rain_mmh=R,
        vis_m=vis_m,
        pressure_hpa=P,
        pressure_trend_hpa_3h=pressure_trend_3h,
        lcl_ft=lcl_ft
    )
    
    return vis_code, cloud_group
