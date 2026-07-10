"""
vis_cloud_proxy.py

Physics-based algorithms to dynamically estimate Visibility and Cloud Amount/Base
using temperature, dewpoint, pressure, and rainfall, specifically tuned for
aviation forecasts (TAF).
"""

import json
import math
import os

MONTHLY_V_DRY = None


def load_monthly_v_dry(json_path=None):
    global MONTHLY_V_DRY
    if json_path is None:
        json_path = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "diurnal_climatology.json")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        MONTHLY_V_DRY = data.get("monthly_v_dry", {}).get("v_dry_medians", [9999] * 12)
    except Exception:
        MONTHLY_V_DRY = [9999] * 12
    return MONTHLY_V_DRY


def get_v_dry_for_month(month: int | None) -> float:
    if MONTHLY_V_DRY is None:
        load_monthly_v_dry()
    if month and 1 <= int(month) <= 12:
        return float(MONTHLY_V_DRY[int(month) - 1])
    return 9999.0

def estimate_visibility(rain_mmh, rh_pct, temp_c, dewpoint_c,
                        wind_kt, pressure_hpa, pressure_trend_hpa_3h,
                        baseline_dry_vis_m=9999, current_month=None):
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
    if R >= 0.5:
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
    vis = float(get_v_dry_for_month(current_month) if current_month else baseline_dry_vis_m)
    if wind_kt > 10.0:
        vis *= 1.2
    elif wind_kt < 3.0:
        vis *= 0.90
    vis *= pressure_factor
    if pressure_hpa > 1025 and pressure_trend_hpa_3h > 0:
        vis *= 0.9
    return int(min(vis, 9999.0))


def detect_tsra(cape=None, lifted_index=None, cin=None, weather_code=None,
                month=None, rain_mmh=0.0, rh_pct=0.0, local_hour_wita=None):
    """
    CAPE-aware TSRA proxy tuned for maritime tropics.
    CIN threshold is 100 J/kg per the overhaul brief.
    """
    if weather_code is not None:
        try:
            if int(weather_code) in {95, 96, 99}:
                return "TSRA"
        except (TypeError, ValueError):
            pass

    wet_months = {11, 12, 1, 2, 3, 4}
    cape_thr = 500 if month is None or int(month) in wet_months else 800
    cin_thr = 100

    if cape is not None and lifted_index is not None and cin is not None:
        try:
            if float(cape) >= cape_thr and float(lifted_index) <= -2 and float(cin) <= cin_thr:
                if rain_mmh >= 5 or (local_hour_wita is not None and 11 <= int(local_hour_wita) <= 21):
                    return "TSRA"
        except (TypeError, ValueError):
            pass

    if local_hour_wita is not None and rain_mmh >= 7.5 and 11 <= int(local_hour_wita) <= 21 and rh_pct >= 85:
        return "TSRA"
    return None


def classify_rain_type(rain_mmh, sunshine_min=None, low_cloud_pct=None, mid_cloud_pct=None, prev_rain_mmh=None):
    onset_rate = float(rain_mmh or 0.0) - float(prev_rain_mmh or 0.0)
    if (rain_mmh > 2.5 and sunshine_min is not None and sunshine_min < 30
            and low_cloud_pct is not None and low_cloud_pct > 60 and onset_rate > 2.0):
        return "convective"
    if (rain_mmh > 0.5 and mid_cloud_pct is not None and mid_cloud_pct > 50
            and onset_rate < 1.0):
        return "stratiform"
    return "convective"


def estimate_visibility_rain_typed(rain_mmh, rain_type="convective", **kwargs):
    if rain_type == "stratiform":
        if rain_mmh < 0.5:
            return 9999
        vis = 4500.0 * (rain_mmh ** -0.65)
        return int(max(200.0, min(vis, 9999.0)))
    return estimate_visibility(rain_mmh=rain_mmh, **kwargs)


def get_weather_phenomenon(rain_mmh, rh_pct, temp_c, dewpoint_c, vis_m, local_hour_wita,
                           cape=None, lifted_index=None, cin=None, weather_code=None, month=None):
    """
    Returns the appropriate ICAO weather phenomenon code for the TAF change group.
    Ensures that if vis_m < 5000m, a weather phenomenon is provided (unless conditions fall through).
    """
    dd = temp_c - dewpoint_c

    # Precipitation
    if rain_mmh > 0.1:
        tsra = detect_tsra(
            cape=cape,
            lifted_index=lifted_index,
            cin=cin,
            weather_code=weather_code,
            month=month,
            rain_mmh=rain_mmh,
            rh_pct=rh_pct,
            local_hour_wita=local_hour_wita,
        )
        if tsra:
            return tsra
        if rain_mmh >= 10.0:
            return "+RA"
        elif rain_mmh >= 2.5:
            return "RA"
        elif vis_m < 5000:
            return "-RA"
        else:
            return ""

    # Fog / Mist / Haze only when no active precipitation.
    if vis_m < 1000.0 and rh_pct >= 95.0 and dd <= 1.5:
        return "FG"
    if vis_m < 5000.0 and rh_pct >= 75.0:
        return "BR"
    if vis_m < 5000.0 and rh_pct < 75.0 and temp_c >= 28.0:
        return "HZ"
    if vis_m < 5000.0:
        return "BR"

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

    # Base amount from actual consensus percentages if available
    low_pct = consensus_hour.get('low_clouds', 0.0) if consensus_hour else 0.0
    mid_pct = consensus_hour.get('mid_clouds', 0.0) if consensus_hour else 0.0
    high_pct = consensus_hour.get('high_clouds', 0.0) if consensus_hour else 0.0
    
    # Decide which cloud layer is dominant. Only low cloud should use the LCL as
    # a ceiling; mid/high layers need representative aviation bases.
    layer_candidates = [("low", low_pct), ("mid", mid_pct), ("high", high_pct)]
    dominant_layer, target_pct = max(layer_candidates, key=lambda item: item[1])
    if low_pct >= 15.0:
        dominant_layer, target_pct = "low", low_pct
    elif max(mid_pct, high_pct) < 5.0:
        dominant_layer, target_pct = "low", low_pct
    
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

    if dominant_layer == "mid":
        base_ft = max(6500.0, min(12000.0, lcl_ft))
    elif dominant_layer == "high":
        base_ft = max(18000.0, min(25000.0, lcl_ft))
    else:
        base_ft = lcl_ft
        # We have cloud amount but no direct cloud-base forecast. Do not create
        # a sub-1000 ft prevailing ceiling from the LCL proxy alone unless rain
        # or genuinely restricted visibility independently supports it.
        proxy_only_low_ceiling = rain_mmh <= 0.1 and vis_m >= 800.0
        if proxy_only_low_ceiling:
            base_ft = max(base_ft, 1000.0)

    base_hundreds = int(round(max(100.0, base_ft) / 100.0))
    base_str = f"{base_hundreds:03d}"
    return f"{amount}{base_str}"


def build_hourly_vis_cloud(consensus_hour, pressure_history):
    T  = consensus_hour["temp_c"]
    Td = consensus_hour["dewpoint_c"]
    P  = consensus_hour.get("pressure_hpa", 1013.25)
    R  = consensus_hour.get("rain", 0.0)
    U  = consensus_hour.get("spd", 0.0)
    RH = consensus_hour.get("relative_humidity_pct", 80.0) or 80.0

    if len(pressure_history) >= 3:
        pressure_trend_3h = P - pressure_history[-3]
    else:
        pressure_trend_3h = 0.0

    # WAWP baseline dry visibility
    baseline_dry_vis = 9999

    proxy_vis_m = estimate_visibility(R, RH, T, Td, U, P, pressure_trend_3h,
                                      baseline_dry_vis_m=baseline_dry_vis,
                                      current_month=consensus_hour.get("month"))
    model_vis_m = consensus_hour.get("model_visibility_m")
    try:
        model_vis_m = max(50.0, min(9999.0, float(model_vis_m)))
    except (TypeError, ValueError):
        model_vis_m = None

    fog_risk = RH >= 90.0 and (T - Td) <= 3.0
    if model_vis_m is None:
        # A thermodynamic proxy can flag fog risk, but cannot alone support a
        # prevailing sub-800 m restriction when no model visibility exists.
        vis_m = proxy_vis_m if R > 0.1 else max(proxy_vis_m, 800.0)
    elif R > 0.1:
        vis_m = min(model_vis_m, proxy_vis_m)
    elif fog_risk and model_vis_m < 5000.0:
        # Retain a model-supported fog restriction, but reserve sub-800 m
        # values for direct model visibility support rather than the proxy.
        vis_m = min(model_vis_m, max(proxy_vis_m, 800.0))
    else:
        vis_m = model_vis_m

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
