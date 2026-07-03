# WAWP Forecast Engine Calculation Reference for Auto-Tuning Research

This document converts the main deterministic/proxy calculation code into a Markdown reference for external research. It is intended to be given to Apodex.ai together with the auto-tuning research prompt.

The goal is not to replace the aviation logic with a black box. The goal is to identify which constants can be empirically tuned using historical forecast data, live operational forecasts, and AWOS observations while keeping the system explainable and operationally conservative.

## Source Files Covered

- `src/export_dashboard_data.py`
  - thunderstorm risk score
  - aviation visibility consensus
  - aviation cloud-cover consensus
  - rainfall consensus restoration
  - related-parameter weight blending
- `src/vis_cloud_proxy.py`
  - visibility proxy
  - TSRA proxy
  - weather phenomenon selection
  - LCL/cloud-base proxy
  - visibility/cloud TAF formatting
- `src/taf_core.py`
  - TAF rain thresholds and change-group constants
- `src/advanced_ensemble_weighter.py`
  - model skill metrics
  - ensemble weight fusion constants
  - parameter regimes
  - temporal blend
- `src/quantile_mapper.py`
  - QM sample thresholds
  - rain event threshold
  - lead buckets
- `src/diurnal_analysis.py`
  - climatological rain/gust/fog/season thresholds
- `src/climatology_engine.py`
  - rain-climatology threshold

## 1. Thunderstorm Risk Score

Location: `src/export_dashboard_data.py::_compute_ts_risk`

Purpose: Dashboard risk indicator. TAF weather-code selection still happens separately in `vis_cloud_proxy.py` and `taf_core.py`.

Inputs:

- `Precip Probability` in percent
- `Rain` in mm/h
- `CAPE`
- `Lifted Index`
- `Convective Inhibition`
- `Weather Code`
- local/display forecast hour

Current formula:

```text
score = 0.35 * precipitation_probability_pct

if rain >= 0.1 mm: score += 8
if rain >= 1.0 mm: score += 10
if rain >= 5.0 mm: score += 15

if CAPE >= 500:  score += 12
if CAPE >= 1000: score += 8

if Lifted_Index <= -2: score += 10
if Lifted_Index <= -4: score += 5

if CIN <= 100: score += 8
if CIN <= 50:  score += 4
if CIN > 200:  score -= 10

if hour in 12-19: score += 10
elif hour in 10-21: score += 5

if weather_code in {95, 96, 99}: score = max(score, 85)

score = clamp(score, 0, 100)
```

Tunable candidates:

- precipitation probability multiplier: `0.35`
- rain thresholds: `0.1`, `1.0`, `5.0`
- rain score bonuses: `8`, `10`, `15`
- CAPE thresholds: `500`, `1000`
- CAPE bonuses: `12`, `8`
- lifted-index thresholds: `-2`, `-4`
- lifted-index bonuses: `10`, `5`
- CIN thresholds: `50`, `100`, `200`
- CIN bonuses/penalty: `8`, `4`, `-10`
- convective windows: `12-19`, `10-21`
- convective bonuses: `10`, `5`
- thunderstorm weather-code override floor: `85`

Suggested validation targets:

- observed TSRA if available
- heavy rain occurrence proxy
- rain + gust occurrence proxy
- lightning/thunder reports if later available
- false alarm rate during dry hours
- peak-hour calibration around the local convective window

## 2. Aviation Visibility Consensus

Location: `src/export_dashboard_data.py::_aviation_visibility_consensus`

Purpose: Convert model visibility fields into a conservative but not overreactive aviation visibility consensus.

Inputs:

- per-model `visibility`
- model weights, currently borrowed/blended from related parameters when direct visibility skill is not trained

Current logic:

```text
values = clamp(model_visibility_m, 50, 9999)

p800  = weighted_fraction(values < 800)
p1500 = weighted_fraction(values < 1500)
p3000 = weighted_fraction(values < 3000)
p5000 = weighted_fraction(values < 5000)

restricted_consensus(limit, probability_threshold):
    if number_of_models_below_limit >= 2:
        return True
    if number_of_models >= 5 and weighted_probability_below_limit >= probability_threshold:
        return True
    return False

if restricted_consensus(800, 0.50):
    return min(weighted_quantile(values, 0.25), 800)
if restricted_consensus(1500, 0.50):
    return min(weighted_quantile(values, 0.30), 1500)
if restricted_consensus(3000, 0.55):
    return min(weighted_quantile(values, 0.35), 3000)
if restricted_consensus(5000, 0.55):
    return min(weighted_quantile(values, 0.40), 5000)

return min(9999, weighted_quantile(values, 0.50))
```

Tunable candidates:

- visibility clamp lower bound: `50 m`
- visibility clamp upper bound: `9999 m`
- aviation threshold set: `800`, `1500`, `3000`, `5000 m`
- required supporting models: `2`
- probability thresholds: `0.50`, `0.50`, `0.55`, `0.55`
- restricted-visibility quantiles: `0.25`, `0.30`, `0.35`, `0.40`
- normal visibility quantile: `0.50`
- minimum model count for weighted-probability-only trigger: `5`

Suggested validation targets:

- AWOS visibility when available
- proxy low-visibility events when AWOS visibility is missing
- categorical hit/false alarm at `5000`, `3000`, `1500`, `800 m`
- severe penalty for missing low visibility
- false alarm limit during known good-visibility dry hours

## 3. Aviation Cloud-Cover Consensus

Location: `src/export_dashboard_data.py::_aviation_cloud_cover_consensus`

Purpose: Convert per-model cloud-cover percentages into aviation amount categories.

Current logic:

```text
values = clamp(model_cloud_cover_pct, 0, 100)

p_few = weighted_fraction(values >= 5)
p_sct = weighted_fraction(values >= 26)
p_bkn = weighted_fraction(values >= 51)
p_ovc = weighted_fraction(values >= 88)

if p_ovc >= 0.45: return 95
if p_bkn >= 0.45: return 70
if p_sct >= 0.40: return 38
if p_few >= 0.30: return 15
return 0
```

The returned numeric values are representative cloud amounts:

- `0` means no significant cover
- `15` means FEW-like
- `38` means SCT-like
- `70` means BKN-like
- `95` means OVC-like

Tunable candidates:

- FEW/SCT/BKN/OVC raw cloud thresholds: `5`, `26`, `51`, `88%`
- consensus probability thresholds: `0.30`, `0.40`, `0.45`, `0.45`
- representative output values: `15`, `38`, `70`, `95`

Fixed or semi-fixed considerations:

- FEW/SCT/BKN/OVC thresholds are tied to aviation oktas/categories and should not drift too far.
- Probability thresholds and representative output values are safer to tune than category definitions.

## 4. Related-Parameter Weight Blending

Location: `src/export_dashboard_data.py::_blend_related_weights`

Purpose: When a parameter does not have direct trained weights, borrow model weights from physically related parameters.

Current related-parameter mapping:

```text
Humidity:              Dewpoint + Temperature
Visibility:            Rainfall + Dewpoint + Wind Speed
Precip Probability:    Rainfall
Sunshine:              Rainfall
Low Clouds:            Dewpoint + Rainfall + Pressure
Mid Clouds:            Dewpoint + Rainfall + Pressure
High Clouds:           Dewpoint + Rainfall + Pressure
CAPE:                  Rainfall
Lifted Index:          Rainfall
Convective Inhibition: Rainfall
Weather Code:          Rainfall
```

Current formula:

```text
for each related parameter:
    add that parameter's model weights

normalize summed weights to 1

if no usable related weights:
    use equal weights
```

Tunable candidates:

- related-parameter mapping
- per-related-parameter blend coefficients
- equal fallback behavior
- minimum skill threshold required before a related parameter can contribute

## 5. Rainfall Consensus Restoration

Location: `src/export_dashboard_data.py`, internal `apply_rain_consensus`

Purpose: Weighted rainfall mean with limited restoration when model spread suggests a possible high-end event.

Current logic:

```text
r_mean = weighted_average(model_rain)

if r_mean >= 1.0:
    r_std = weighted_standard_deviation(model_rain)
    if r_std >= 2.0 * r_mean:
        restoration = min(1.0 + 0.10 * (r_std / r_mean), 1.30)
        return r_mean * restoration

return r_mean
```

Tunable candidates:

- rain-mean activation threshold: `1.0 mm`
- spread ratio trigger: `2.0 * mean`
- restoration slope: `0.10`
- restoration cap: `1.30`

Suggested validation targets:

- wet-hour rainfall MAE
- heavy-rain hit rate
- false heavy-rain creation rate
- dry-hour false alarm rate
- event timing tolerance

## 6. Visibility Proxy

Location: `src/vis_cloud_proxy.py::estimate_visibility`

Purpose: Estimate visibility from rain, humidity, temperature-dewpoint spread, wind, pressure, and climatological dry visibility.

Inputs:

- `rain_mmh`
- `rh_pct`
- `temp_c`
- `dewpoint_c`
- `wind_kt`
- `pressure_hpa`
- `pressure_trend_hpa_3h`
- `baseline_dry_vis_m`
- `current_month`

Derived:

```text
dd = temp_c - dewpoint_c
R = rain_mmh
```

### Pressure Factor

```text
if R <= 0.1 and RH < 85:
    pressure_factor = 1.0
else:
    if pressure_trend_3h <= -2.5: pressure_factor = 0.92
    elif pressure_trend_3h <= -1.5: pressure_factor = 0.96
    elif pressure_trend_3h >= 1.5: pressure_factor = 1.05
    else: pressure_factor = 1.0
```

Tunable candidates:

- dry/moist activation threshold: `R <= 0.1`, `RH < 85`
- pressure-trend thresholds: `-2.5`, `-1.5`, `+1.5 hPa/3h`
- pressure factors: `0.92`, `0.96`, `1.05`

### Rain Visibility Branch

```text
if R >= 0.5:
    if R < 7.5:
        vis = 6500 * R^-0.55
    else:
        vis = 7581 * R^-0.63
    vis *= pressure_factor
    return clamp(vis, 200, 9999)
```

Tunable candidates:

- rain activation threshold: `0.5 mm/h`
- heavy/intense split: `7.5 mm/h`
- coefficient light/moderate rain: `6500`
- exponent light/moderate rain: `-0.55`
- coefficient heavy rain: `7581`
- exponent heavy rain: `-0.63`
- clamp lower/upper: `200`, `9999 m`

### Drizzle Visibility Branch

```text
if R > 0.1:
    vis = 7000 * R^-0.25
    vis *= pressure_factor
    return clamp(vis, 200, 9999)
```

Tunable candidates:

- drizzle threshold: `0.1 mm/h`
- coefficient: `7000`
- exponent: `-0.25`
- clamp lower/upper: `200`, `9999 m`

### Fog/Mist Branches

```text
if RH >= 95 and dd <= 1.5:
    x = 100 - RH
    vis = 120*x + 0.5*x^2
    vis = clamp(vis, 50, 3000)
    vis *= pressure_factor
    return clamp(vis, 50, 5000)

if RH >= 90 and dd <= 3.0:
    vis = 6000
    vis *= pressure_factor
    return clamp(vis, 3000, 8000)
```

Tunable candidates:

- fog RH threshold: `95%`
- fog dewpoint spread threshold: `1.5 C`
- fog polynomial coefficients: `120`, `0.5`
- fog clamps: `50`, `3000`, `5000 m`
- mist/haze RH threshold: `90%`
- mist/haze dewpoint spread threshold: `3.0 C`
- mist/haze baseline visibility: `6000 m`
- mist/haze clamps: `3000`, `8000 m`

### Dry/Haze Branch

```text
vis = monthly_dry_visibility_median or baseline_dry_vis_m

if wind_kt > 10:
    vis *= 1.2
elif wind_kt < 3:
    vis *= 0.90

vis *= pressure_factor

if pressure_hpa > 1025 and pressure_trend_3h > 0:
    vis *= 0.9

return min(vis, 9999)
```

Tunable candidates:

- wind ventilation threshold: `10 kt`
- ventilation factor: `1.2`
- calm threshold: `3 kt`
- calm reduction factor: `0.90`
- high-pressure threshold: `1025 hPa`
- high-pressure reduction factor: `0.9`
- maximum visibility cap: `9999 m`

## 7. Direct Model Visibility + Proxy Blending

Location: `src/vis_cloud_proxy.py::build_hourly_vis_cloud`

Current logic:

```text
proxy_vis_m = estimate_visibility(...)
model_vis_m = clamp(model_visibility_m, 50, 9999), if available

fog_or_rain_risk = rain > 0.1 or (RH >= 90 and temp_dewpoint_spread <= 3)

if model_vis_m is missing:
    vis_m = proxy_vis_m
elif fog_or_rain_risk:
    vis_m = min(model_vis_m, proxy_vis_m)
else:
    vis_m = model_vis_m
```

Tunable candidates:

- fog/rain rain threshold: `0.1 mm/h`
- fog/rain RH threshold: `90%`
- fog/rain spread threshold: `3 C`
- blend function: currently hard `min`
- model/proxy trust weighting
- conditions under which model visibility can override proxy

## 8. Aviation Visibility Formatting

Location: `src/vis_cloud_proxy.py::build_hourly_vis_cloud`

Current rounding:

```text
if vis_m >= 9999: vis_code = "9999"
elif vis_m >= 5000: round down to nearest 1000 m
elif vis_m >= 800: round down to nearest 100 m
else: round down to nearest 50 m
```

Mostly fixed:

- aviation visibility formatting should follow operational conventions.

Tunable only with caution:

- transition thresholds: `9999`, `5000`, `800`
- rounding unit below each threshold

## 9. TSRA Proxy for TAF Weather Phenomena

Location: `src/vis_cloud_proxy.py::detect_tsra`

Current logic:

```text
if weather_code in {95, 96, 99}:
    return TSRA

wet_months = {11, 12, 1, 2, 3, 4}
cape_threshold = 500 if month in wet_months or month missing else 800
cin_threshold = 100

if CAPE >= cape_threshold and Lifted_Index <= -2 and CIN <= 100:
    if rain >= 5 or local_hour_wita in 11-21:
        return TSRA

if local_hour_wita in 11-21 and rain >= 7.5 and RH >= 85:
    return TSRA

return None
```

Tunable candidates:

- wet-season months
- wet-season CAPE threshold: `500`
- dry-season CAPE threshold: `800`
- lifted-index threshold: `-2`
- CIN threshold: `100`
- rain threshold with CAPE support: `5 mm/h`
- fallback rain threshold: `7.5 mm/h`
- fallback RH threshold: `85%`
- TSRA time window: `11-21 WITA`
- weather-code override set: `{95, 96, 99}`

## 10. Rain Type Classification

Location: `src/vis_cloud_proxy.py::classify_rain_type`

Current logic:

```text
onset_rate = rain_mmh - prev_rain_mmh

if rain > 2.5 and sunshine < 30 and low_cloud_pct > 60 and onset_rate > 2.0:
    return convective

if rain > 0.5 and mid_cloud_pct > 50 and onset_rate < 1.0:
    return stratiform

return convective
```

Tunable candidates:

- convective rain threshold: `2.5 mm/h`
- sunshine threshold: `30 min`
- low-cloud threshold: `60%`
- onset-rate threshold: `2.0 mm/h`
- stratiform rain threshold: `0.5 mm/h`
- mid-cloud threshold: `50%`
- stratiform onset-rate threshold: `1.0 mm/h`

## 11. Stratiform Rain Visibility

Location: `src/vis_cloud_proxy.py::estimate_visibility_rain_typed`

Current logic:

```text
if rain_type == stratiform:
    if rain < 0.5:
        return 9999
    vis = 4500 * rain^-0.65
    return clamp(vis, 200, 9999)
else:
    use normal estimate_visibility()
```

Tunable candidates:

- stratiform activation threshold: `0.5 mm/h`
- coefficient: `4500`
- exponent: `-0.65`
- clamps: `200`, `9999 m`

## 12. Weather Phenomenon Selection

Location: `src/vis_cloud_proxy.py::get_weather_phenomenon`

Current logic:

```text
dd = temp_c - dewpoint_c

if rain > 0.1:
    if detect_tsra(...): return "TSRA"
    if rain >= 10.0: return "+RA"
    elif rain >= 2.5: return "RA"
    elif visibility < 5000: return "-RA"
    else: return ""

if visibility < 1000 and RH >= 95 and dd <= 1.5:
    return "FG"
if visibility < 5000 and RH >= 80:
    return "BR"
if visibility < 5000 and RH < 75 and temp >= 28:
    return "HZ"

return ""
```

Tunable candidates:

- rain/no-rain threshold: `0.1 mm/h`
- heavy rain threshold: `10.0 mm/h`
- rain threshold: `2.5 mm/h`
- light rain visibility threshold: `5000 m`
- fog visibility/RH/spread thresholds: `1000 m`, `95%`, `1.5 C`
- mist visibility/RH thresholds: `5000 m`, `80%`
- haze visibility/RH/temp thresholds: `5000 m`, `75%`, `28 C`

Mostly fixed:

- ICAO weather codes themselves.
- Direction of classification logic should remain conservative.

## 13. LCL / Cloud-Base Proxy

Location: `src/vis_cloud_proxy.py::estimate_lcl_ft_pressure`

Current formula:

```text
dd = max(0, temp_c - dewpoint_c)

if dd < 0.5:
    return 0 ft

pressure_ratio = 1013.25 / pressure_hpa
factor = 400 * pressure_ratio^0.15
lcl_ft = factor * dd

return clamp(lcl_ft, 0, 25000)
```

Tunable candidates:

- saturation spread threshold: `0.5 C`
- base LCL factor: `400 ft/C`
- pressure exponent: `0.15`
- reference pressure: `1013.25 hPa`
- LCL clamp upper: `25000 ft`

## 14. Cloud Group Selection

Location: `src/vis_cloud_proxy.py::estimate_cloud_group_with_pressure`

### Surface Fog/Low Cloud

```text
if visibility < 1000 and RH >= 95 and dd <= 1.5:
    return OVC000
```

Tunable candidates:

- visibility threshold: `1000 m`
- RH threshold: `95%`
- dewpoint spread threshold: `1.5 C`

### Dominant Layer Selection

Current logic:

```text
dominant_layer = layer with max(low_pct, mid_pct, high_pct)

if low_pct >= 15:
    dominant_layer = low
elif max(mid_pct, high_pct) < 5:
    dominant_layer = low
```

Tunable candidates:

- low-cloud priority threshold: `15%`
- no-mid/high fallback threshold: `5%`

### Cloud Amount From Model Cloud Percentage

```text
if target_pct >= 88: amount = OVC
elif target_pct >= 51: amount = BKN
elif target_pct >= 26: amount = SCT
elif target_pct >= 5: amount = FEW
else:
    amount = NSC
    if rain > 0.1: amount = FEW
```

Tunable with caution:

- category percentage thresholds: `5`, `26`, `51`, `88`
- rain cloud forcing threshold: `0.1 mm/h`

### Fallback Cloud Amount From RH

If no cloud percentage data is available:

```text
if rain > 0.1:
    if RH >= 90: amount = OVC
    elif RH >= 80: amount = BKN
    else: amount = SCT
else:
    if RH >= 95: amount = BKN
    elif RH >= 85: amount = SCT
    elif RH >= 70: amount = FEW
    else: amount = NSC
```

Tunable candidates:

- rain activation threshold: `0.1 mm/h`
- rainy RH thresholds: `90`, `80%`
- dry RH thresholds: `95`, `85`, `70%`

### Pressure Trend Cloud Adjustment

```text
if pressure_trend_3h < -2.0:
    FEW -> SCT
    SCT -> BKN
    BKN -> OVC
```

Tunable candidates:

- pressure fall threshold: `-2.0 hPa/3h`
- promotion strength

### Cloud Base by Layer

```text
if dominant_layer == mid:
    base_ft = clamp(lcl_ft, lower=6500, upper=12000)
elif dominant_layer == high:
    base_ft = clamp(lcl_ft, lower=18000, upper=25000)
else:
    base_ft = lcl_ft
```

Tunable candidates:

- mid-cloud base lower/upper: `6500`, `12000 ft`
- high-cloud base lower/upper: `18000`, `25000 ft`

### Weak Low Cloud Floor

```text
weak_low_cloud =
    amount == FEW
    and low_pct < 26
    and rain <= 0.1
    and visibility >= 9999
    and RH < 92

if weak_low_cloud:
    base_ft = max(base_ft, 1000)
```

Tunable candidates:

- weak amount: `FEW`
- low-cloud threshold: `26%`
- rain threshold: `0.1 mm/h`
- visibility threshold: `9999 m`
- RH threshold: `92%`
- minimum base: `1000 ft`

## 15. TAF Rain and Change-Group Constants

Location: `src/taf_core.py::RainConfig`

Current constants:

```text
CONSENSUS_THR   = 1.0    # mm, weighted mean gate for rainy hour
VOTE_THR        = 1.0    # mm, per-model rain vote gate
SPREAD_FACTOR   = 0.1    # spread penalty scalar
BRIDGE_THR      = 0.20   # mm, dry-gap bridge threshold
HEAVY_RAIN_THR  = 4.0    # mm, peak consensus for +RA
TEMPO_CUT       = 0.18   # agreement floor for TEMPO
PROB40_CUT      = 0.10   # agreement floor for PROB40 TEMPO
PROB30_CUT      = 0.02   # agreement floor for PROB30 TEMPO
HEAVY_AGR_THR   = 0.40   # weighted agreement for heavy-rain warning
HEAVY_VOTE_THR  = 3.0    # per-model heavy rain vote threshold
MAX_TEMPO_HOURS = 4      # max TEMPO window length
MAX_GROUPS      = 5      # max TAF change groups
```

Tunable candidates:

- rainy-hour gate: `1.0 mm`
- model rain-vote gate: `1.0 mm`
- spread penalty: `0.1`
- bridge threshold: `0.20 mm`
- heavy rain TAF threshold: `4.0 mm`
- TEMPO/PROB40/PROB30 agreement cuts: `0.18`, `0.10`, `0.02`
- heavy rain warning thresholds: `0.40`, `3.0 mm`

Mostly fixed or constrained:

- `MAX_GROUPS = 5` is operational/SOP-like and should not be freely optimized.
- `MAX_TEMPO_HOURS = 4` should be tuned only if local TAF SOP permits.

## 16. Ensemble Weight Configuration

Location: `src/advanced_ensemble_weighter.py::WeightConfiguration`

### Metric Weights

Current composite metric weights:

```text
RMSE = 0.20
MAE  = 0.15
Bias = 0.05
POD  = 0.20
FAR  = 0.15
CSI  = 0.10
HSS  = 0.10
F1   = 0.00
MCC  = 0.05
```

Constraint:

```text
sum(metric_weights) ~= 1.0
```

Tunable candidates:

- all metric weights, with simplex constraint
- different metric weights by parameter
- stronger rare-event weighting for rain, gust, visibility, ceiling

### Strategy Fusion Weights

Current fusion weights:

```text
base_weight     = 0.375
leadtime_weight = 0.00
timing_weight   = 0.10
regime_weight   = 0.325
temporal_weight = 0.20
```

Constraint:

```text
sum(strategy_weights) ~= 1.0
```

Notes:

- `leadtime_weight` is currently zero because operational data is still limited.
- `timing_weight` mainly applies to rainfall.
- non-rain parameters redirect timing influence toward base/temporal behavior.

Tunable candidates:

- strategy weights
- parameter-specific fusion weights
- when to activate lead-time weights
- rain-specific timing weight

### Other Weighting Constants

```text
method = "crps"
shrinkage_n_min = 100
crps_threshold_weighted = True by default, but disabled for some continuous/circular parameters
```

Tunable candidates:

- shrinkage sample threshold: `100`
- method choice by parameter
- threshold-weighted vs standard CRPS

## 17. Temporal Weighting

Location: `src/advanced_ensemble_weighter.py`

Temporal windows:

```text
24 Hours = 1 day
3 Days   = 3 days
7 Days   = 7 days
Month    = 30 days
```

Blend weights:

```text
24 Hours = 0.40
3 Days   = 0.30
7 Days   = 0.20
Month    = 0.10
```

Tunable candidates:

- window lengths
- blend weights
- parameter-specific temporal weighting
- wet/dry season temporal weighting

## 18. Lead-Time Brackets

Location: `src/advanced_ensemble_weighter.py`

Current brackets:

```text
Day_1  = 0-24 h
Day_2  = 24-48 h
Day_3  = 48-72 h
Day_4+ = 72-120 h
```

Tunable candidates:

- bucket boundaries
- separate short aviation buckets, e.g. `0-6`, `6-12`, `12-24`, `24-48`, `48+`
- parameter-specific buckets for gust/rain/visibility

## 19. Parameter Regimes

Location: `src/advanced_ensemble_weighter.py::PARAMETER_REGIMES`

Current regimes:

```text
Rainfall:
  dry      = 0.0-0.1 mm
  light    = 0.1-5.0 mm
  moderate = 5.0-20.0 mm
  heavy    = 20.0-50.0 mm
  extreme  = 50.0+ mm

Temperature:
  cool = 0-20 C
  mild = 20-25 C
  warm = 25-30 C
  hot  = 30+ C

Dewpoint:
  dry       = 0-18 C
  moist     = 18-23 C
  humid     = 23-26 C
  saturated = 26+ C

Wind Speed:
  calm     = 0-5 kt
  light    = 5-15 kt
  moderate = 15-25 kt
  strong   = 25+ kt

Wind Gust:
  calm     = 0-10 kt
  moderate = 10-20 kt
  strong   = 20-35 kt
  severe   = 35+ kt
```

Tunable candidates:

- regime boundaries
- regime-specific weights
- shrinkage strength from regime weights back to base weights
- whether rainfall needs airport-specific event thresholds instead of generic hydrometeorological thresholds

## 20. QM / Historical Prior Constants

Location: `src/quantile_mapper.py`

Current constants:

```text
VOTE_THR        = 1.0 mm
QM_N_QUANTILES  = 20
QM_MIN_SAMPLES  = 10

LEAD_BUCKETS_DEFAULT:
  L1_0_6h
  L2_6_12h
  L3_12_24h
  L4_24_48h
  L5_48plus

LEAD_BUCKETS_GUST:
  L1_0_6h
  L2_6_18h
  L3_18h_plus

HISTORICAL_PRIOR_BUCKET = GLOBAL
SOURCE_CONTINUOUS       = continuous_historical
SOURCE_OPERATIONAL      = operational_multiinit
LAYER_HISTORICAL        = historical_prior
LAYER_OPERATIONAL       = operational_residual
```

Multi-parameter QM sample gates:

```text
temperature: min_samples = 100, type = linear
dewpoint:    min_samples = 100, type = linear
pressure:    min_samples = 100, type = linear
wind_speed:  min_samples = 100, type = non-negative
wind_gust:   min_samples = 50, stable = 200, type = gamma_parametric
wind_dir:    min_samples = 100, type = circular
rain:        min_samples = 50, type = zero_inflated
```

Tunable candidates:

- number of quantile anchors: `20`
- per-parameter minimum samples
- stable sample threshold for gust
- rain event threshold: `1.0 mm`
- lead bucket boundaries
- operational residual promotion thresholds
- low-confidence flags

## 21. Seed Rainfall QM Anchors

Location: `src/quantile_mapper.py::_SEED_ANCHORS`

Purpose: fallback anchor pairs when empirical rain samples are sparse.

Examples:

```text
ECMWF: forecast 2.5 -> observed 5.0, forecast 3.7 -> observed 10.0, forecast 5.0 -> observed 20.0
GFS:   forecast 2.3 -> observed 5.0, forecast 2.9 -> observed 10.0, forecast 3.7 -> observed 20.0
ICON:  forecast 1.4 -> observed 5.0, forecast 1.7 -> observed 10.0, forecast 1.9 -> observed 20.0
```

Tunable candidates:

- whether seed anchors should remain active
- model-specific fallback anchors
- interpolation/extrapolation shape
- when empirical data overrides seed anchors

## 22. Diurnal and Climatology Thresholds

Locations:

- `src/diurnal_analysis.py`
- `src/climatology_engine.py`

Season definitions:

```text
wet season = Nov, Dec, Jan, Feb, Mar, Apr
dry season = May, Jun, Jul, Aug, Sep, Oct
```

Rain event threshold:

```text
rain_event = rain_1h > 0.1 mm
```

Gust event threshold:

```text
gust_event = wind_gust_max >= 15 kt OR (wind_gust_max - wind_speed) >= 5 kt
```

Temperature-dewpoint spread low-spread proxy:

```text
low_spread_event = temperature - dewpoint <= 2.0 C
```

Hourly climatology sample gate:

```text
if observations_in_hour < 10:
    hourly statistic = insufficient/null
```

Monthly/hour matrix sample gate:

```text
if observations_in_month_hour >= 5:
    compute mean
```

Climatology engine notes mention:

```text
season/month/hour cells with < 30 observations are treated as insufficient in some seasonal products.
```

Tunable candidates:

- wet/dry season month definitions
- rain event threshold: `0.1 mm`
- gust event threshold: `15 kt`
- gust spread threshold: `5 kt`
- low spread threshold: `2.0 C`
- minimum sample gates: `5`, `10`, `30`
- composite convective window construction

## 23. Recommended Auto-Tuning Framing

For Apodex research, treat constants in three groups.

### A. Mostly Fixed Aviation Rules

These should be changed only if local SOP explicitly requires it:

- ICAO weather phenomenon codes
- TAF group maximums where SOP-driven
- FEW/SCT/BKN/OVC category meanings
- visibility formatting/rounding conventions
- CAVOK/9999 aviation reporting conventions

### B. Tunable Physical Proxy Constants

These are good auto-tuning candidates:

- rain-to-visibility extinction coefficients/exponents
- fog/mist RH and dewpoint-spread thresholds
- pressure-trend visibility/cloud factors
- cloud-base floors for weak low cloud, mid cloud, high cloud
- direct model visibility vs proxy blending
- thunderstorm risk score weights and thresholds
- rainfall consensus spread-restoration constants

### C. Tunable Statistical System Constants

These should be tuned using validation and promotion/rollback logic:

- model weight metric weights
- strategy fusion weights
- temporal-window blend weights
- regime boundaries
- QM sample thresholds
- lead bucket boundaries
- low-confidence/promote/disable gates
- related-parameter weight mapping

## 24. Suggested Objective Functions by Subsystem

### Visibility

Optimize a weighted categorical + continuous score:

```text
loss_visibility =
  w_mae * MAE(log_visibility)
  + w_cat * categorical_penalty_at_{5000,3000,1500,800}
  + w_miss_low * missed_low_visibility_penalty
  + w_false_low * false_low_visibility_penalty
```

### Cloud/Ceiling

If direct ceiling observation is limited, use proxy validation:

```text
loss_cloud =
  categorical_amount_loss(FEW/SCT/BKN/OVC)
  + ceiling_category_loss(<500, <1000, <1500, <3000 ft)
  + false_low_ceiling_penalty
  + missed_low_ceiling_penalty
```

### Thunderstorm Risk

Use probabilistic calibration:

```text
loss_ts =
  Brier_score(event_proxy)
  + calibration_error
  + high_false_alarm_penalty
  + missed_severe_event_penalty
```

Event proxy can be TSRA report if available, otherwise rain + gust + convective-hour proxy.

### Rainfall

Separate occurrence and amount:

```text
occurrence_score = CSI/POD/FAR/HSS at thresholds
amount_score = wet-hour MAE + heavy-rain tail penalty
```

### TAF Change Groups

Optimize operational utility, not raw fit:

```text
taf_loss =
  missed_operational_change_penalty
  + false_change_group_penalty
  + excessive_group_count_penalty
  + timing_error_penalty
  + instability_between_runs_penalty
```

## 25. Practical Auto-Tuning Recommendation

Recommended workflow:

```text
1. Define constants in a central tunable config table/file.
2. Give every constant:
   - default value
   - allowed range
   - hard safety bounds
   - subsystem owner
   - validation metric
   - promotion threshold

3. Tune offline using walk-forward validation:
   - monthly folds
   - wet season/dry season folds
   - diurnal regime folds
   - rare-event holdout folds

4. Start with small search:
   - grid/random search for low-dimensional groups
   - Bayesian optimization for visibility and TS risk
   - constrained simplex optimization for ensemble weights

5. Promote only if:
   - skill improves on holdout
   - false alarms do not exceed allowed increase
   - misses do not increase for safety-critical categories
   - output remains stable between adjacent runs

6. Store tuned constants with:
   - version
   - active flag
   - validation period
   - validation metrics
   - confidence label
   - rollback pointer
```

## 26. Example Tunable Constant Schema

Suggested table:

```sql
CREATE TABLE tuned_constants (
    id INTEGER PRIMARY KEY,
    subsystem TEXT NOT NULL,
    constant_name TEXT NOT NULL,
    value REAL NOT NULL,
    default_value REAL,
    min_value REAL,
    max_value REAL,
    unit TEXT,
    active INTEGER DEFAULT 0,
    confidence TEXT,
    validation_method TEXT,
    validation_start TEXT,
    validation_end TEXT,
    metric_primary TEXT,
    metric_before REAL,
    metric_after REAL,
    false_alarm_before REAL,
    false_alarm_after REAL,
    miss_rate_before REAL,
    miss_rate_after REAL,
    promoted_at TEXT,
    deprecated_at TEXT,
    notes TEXT
);
```

Suggested companion table:

```sql
CREATE TABLE tuned_constant_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT,
    train_period_start TEXT,
    train_period_end TEXT,
    validation_period_start TEXT,
    validation_period_end TEXT,
    optimizer TEXT,
    objective TEXT,
    status TEXT,
    summary_json TEXT
);
```

## 27. Key Question for Apodex

Given the above deterministic logic and constants, what is the best conservative auto-tuning framework that:

- improves local WAWP calibration,
- avoids overfitting rare events,
- preserves aviation explainability,
- supports versioned rollback,
- separates physical constants from SOP constants,
- works with historical continuous data now,
- and gradually incorporates operational multi-init lead-aware residual data?

