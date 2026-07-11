import os
import json
import logging
import shutil
import math
from datetime import datetime, timezone, timedelta
import pandas as pd

from src.db_manager import ForecastDB
from src.advanced_ensemble_weighter import AdvancedEnsembleWeighter, MODELS, PARAMETERS
from src.guidance_generator import generate_consensus
from src.quantile_mapper import QuantileMapper, apply_qm_with_layers, operational_residual_qm_enabled
from src.model_registry import freshness_status, model_metadata_dict, registry_payload
from src.event_window_verification import event_window_metrics, event_window_weight_scores
from src.operational_residuals import build_operational_residual_state
from src.tafor_generator import generate_tafor

log = logging.getLogger("exporter")

WMO_WEATHER_CODES = [
    {"code": 0, "description": "Clear sky"},
    {"code": 1, "description": "Mainly clear"},
    {"code": 2, "description": "Partly cloudy"},
    {"code": 3, "description": "Overcast"},
    {"code": 45, "description": "Fog"},
    {"code": 48, "description": "Depositing rime fog"},
    {"code": 51, "description": "Light drizzle"},
    {"code": 53, "description": "Moderate drizzle"},
    {"code": 55, "description": "Dense drizzle"},
    {"code": 56, "description": "Light freezing drizzle"},
    {"code": 57, "description": "Dense freezing drizzle"},
    {"code": 61, "description": "Slight rain"},
    {"code": 63, "description": "Moderate rain"},
    {"code": 65, "description": "Heavy rain"},
    {"code": 66, "description": "Light freezing rain"},
    {"code": 67, "description": "Heavy freezing rain"},
    {"code": 71, "description": "Slight snow fall"},
    {"code": 73, "description": "Moderate snow fall"},
    {"code": 75, "description": "Heavy snow fall"},
    {"code": 77, "description": "Snow grains"},
    {"code": 80, "description": "Slight rain showers"},
    {"code": 81, "description": "Moderate rain showers"},
    {"code": 82, "description": "Violent rain showers"},
    {"code": 85, "description": "Slight snow showers"},
    {"code": 86, "description": "Heavy snow showers"},
    {"code": 95, "description": "Slight or moderate thunderstorm"},
    {"code": 96, "description": "Thunderstorm with slight hail"},
    {"code": 99, "description": "Thunderstorm with heavy hail"},
]
WMO_WEATHER_LABELS = {entry["code"]: entry["description"] for entry in WMO_WEATHER_CODES}
WMO_WEATHER_SEVERITY = {
    0: 0, 1: 1, 2: 1, 3: 2, 45: 3, 48: 3,
    51: 4, 53: 4, 55: 4, 56: 4, 57: 4,
    61: 5, 63: 5, 65: 5, 66: 5, 67: 5,
    71: 5, 73: 5, 75: 5, 77: 5,
    80: 6, 81: 6, 82: 6, 85: 6, 86: 6,
    95: 7, 96: 8, 99: 9,
}

def sanitize_for_json(obj):
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj

def _load_json_file(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _metrics_have_skill(metrics: dict) -> bool:
    for lookback in (metrics or {}).values():
        for param_payload in (lookback or {}).values():
            overall = (param_payload or {}).get("overall", {})
            if overall and any(v for v in overall.values()):
                return True
    return False


def _weights_have_signal(weights: dict) -> bool:
    for model_weights in (weights or {}).values():
        if model_weights and not _weights_effectively_equal(model_weights):
            return True
    return False


def _event_diagnostics_have_signal(diagnostics: dict) -> bool:
    for param in ("Rainfall", "Wind Gust"):
        diag = (diagnostics or {}).get(param) or {}
        if diag.get("applied") and diag.get("event_weights"):
            return True
    return False


def _residual_state_sample_count(payload: dict) -> int:
    try:
        return int((payload or {}).get("metadata", {}).get("total_pairs") or 0)
    except (TypeError, ValueError):
        return 0


def _diurnal_total_observations(path: str) -> int:
    payload = _load_json_file(path) or {}
    try:
        return int(payload.get("metadata", {}).get("total_observations") or 0)
    except (TypeError, ValueError):
        return 0

def _long_to_wide(df_long: pd.DataFrame, param: str) -> pd.DataFrame:
    """Convert long DB output to wide format for QuantileMapper and Consensus."""
    if df_long.empty:
        return pd.DataFrame()
        
    df_long = df_long.copy()
    # Need to keep Datetime and OBS
    obs_col = f"OBS_{param.replace(' ', '_')}"
    
    # Extract unique observations
    obs_df = df_long.drop_duplicates(subset=["Datetime"])[["Datetime", "obs"]].rename(columns={"obs": obs_col})
    
    # Pivot forecasts
    wide_df = df_long.pivot_table(index="Datetime", columns="Model", values="forecast", aggfunc="last").reset_index()
    
    # Merge observations back
    return pd.merge(wide_df, obs_df, on="Datetime", how="left")


def _weights_effectively_equal(weights: dict[str, float]) -> bool:
    vals = [float(v) for v in weights.values() if v is not None]
    return bool(vals) and max(vals) - min(vals) < 1e-6


def _skill_fallback_weights(df_long: pd.DataFrame, param: str, is_circular: bool) -> dict[str, float]:
    """Dependency-free inverse-error weights from historical verification pairs."""
    equal = {m: 1.0 / len(MODELS) for m in MODELS}
    if df_long.empty:
        return equal

    scores = {}
    for model in MODELS:
        sub = df_long[df_long["Model"] == model][["forecast", "obs"]].dropna()
        if len(sub) < 20:
            scores[model] = 0.0
            continue

        fc = sub["forecast"].astype(float)
        ob = sub["obs"].astype(float)
        if is_circular:
            err = ((fc - ob + 180.0) % 360.0) - 180.0
            score = 1.0 / (float(err.abs().mean()) + 1.0)
        elif param == "Rainfall":
            mae = float((fc - ob).abs().mean())
            fc_wet = fc > 0.1
            ob_wet = ob > 0.1
            occurrence_error = float((fc_wet != ob_wet).mean())
            score = (0.65 / (mae + 0.1)) + (0.35 / (occurrence_error + 0.05))
        else:
            mae = float((fc - ob).abs().mean())
            score = 1.0 / (mae + 0.05)

        scores[model] = score * min(1.0, len(sub) / 200.0)

    total = sum(v for v in scores.values() if v > 0)
    if total <= 0:
        return equal

    weights = {m: max(0.0, scores.get(m, 0.0)) / total for m in MODELS}
    weights = {m: max(0.02, min(0.35, w)) for m, w in weights.items()}
    total = sum(weights.values())
    return {m: weights[m] / total for m in MODELS}


def _blend_event_window_weights(
    base_weights: dict[str, float],
    event_weights: dict[str, float],
    blend_factor: float = 0.30,
) -> dict[str, float]:
    """Nudge rain/gust weights with event timing skill while preserving baseline skill."""
    base = {m: max(0.0, float(base_weights.get(m, 0.0) or 0.0)) for m in MODELS}
    event = {m: max(0.0, float(event_weights.get(m, 0.0) or 0.0)) for m in MODELS}
    base_total = sum(base.values())
    event_total = sum(event.values())
    if base_total <= 0 or event_total <= 0:
        return base_weights

    base = {m: base[m] / base_total for m in MODELS}
    event = {m: event[m] / event_total for m in MODELS}
    blend_factor = max(0.0, min(1.0, float(blend_factor)))
    blended = {m: ((1.0 - blend_factor) * base[m]) + (blend_factor * event[m]) for m in MODELS}
    total = sum(blended.values())
    return {m: blended[m] / total for m in MODELS}


def _number(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalise_weather_code(value) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _weighted_weather_code_consensus(row: pd.Series, weights: dict[str, float]) -> float:
    """Select a valid WMO category; categorical codes must never be averaged."""
    votes: dict[int, float] = {}
    for model, raw_code in row.dropna().items():
        code = _normalise_weather_code(raw_code)
        if code is None:
            continue
        votes[code] = votes.get(code, 0.0) + max(0.0, float(weights.get(model, 0.0) or 0.0))
    if not votes:
        return float("nan")
    return float(max(votes, key=lambda code: (votes[code], WMO_WEATHER_SEVERITY.get(code, 0), code)))


def _ts_proxy_components(row: pd.Series) -> dict:
    """Return auditable components for the dashboard convective-support proxy."""
    prob = max(0.0, min(100.0, _number(row.get("Precip Probability"), 0.0)))
    rain = max(0.0, _number(row.get("Rain"), 0.0))
    cape = _number(row.get("CAPE"), 0.0)
    li = _number(row.get("Lifted Index"), 0.0)
    cin = _number(row.get("Convective Inhibition"), 0.0)
    weather_code = _normalise_weather_code(row.get("Weather Code"))
    hour = int(getattr(row.get("Datetime"), "hour", 0))

    rain_score = (8.0 if rain >= 0.1 else 0.0) + (10.0 if rain >= 1.0 else 0.0) + (15.0 if rain >= 5.0 else 0.0)
    cape_score = (12.0 if cape >= 500 else 0.0) + (8.0 if cape >= 1000 else 0.0)
    li_score = (10.0 if li <= -2 else 0.0) + (5.0 if li <= -4 else 0.0)
    cin_score = (8.0 if cin <= 100 else 0.0) + (4.0 if cin <= 50 else 0.0) - (10.0 if cin > 200 else 0.0)
    diurnal_score = 10.0 if 12 <= hour <= 19 else (5.0 if 10 <= hour <= 21 else 0.0)
    precipitation_score = prob * 0.35
    raw_score = precipitation_score + rain_score + cape_score + li_score + cin_score + diurnal_score
    thunderstorm_override = weather_code in {95, 96, 99}
    total = max(raw_score, 85.0) if thunderstorm_override else raw_score
    total = max(0.0, min(100.0, total))

    return {
        "total": round(float(total), 2),
        "raw_score": round(float(raw_score), 2),
        "weather_code": weather_code,
        "weather_description": WMO_WEATHER_LABELS.get(weather_code, "Unknown or unavailable WMO code"),
        "weather_code_override": thunderstorm_override,
        "components": [
            {"label": "Precipitation probability", "input": round(prob, 2), "unit": "%", "rule": "x 0.35", "contribution": round(precipitation_score, 2)},
            {"label": "Rain", "input": round(rain, 2), "unit": "mm", "rule": "+8 at 0.1, +10 at 1.0, +15 at 5.0", "contribution": round(rain_score, 2)},
            {"label": "CAPE", "input": round(cape, 1), "unit": "J/kg", "rule": "+12 at 500, +8 at 1000", "contribution": round(cape_score, 2)},
            {"label": "Lifted Index", "input": round(li, 2), "unit": "", "rule": "+10 at -2, +5 at -4", "contribution": round(li_score, 2)},
            {"label": "CIN", "input": round(cin, 2), "unit": "J/kg", "rule": "+8 at <=100, +4 at <=50, -10 above 200", "contribution": round(cin_score, 2)},
            {"label": "Convective window", "input": hour, "unit": "WITA", "rule": "+10 at 12-19, +5 at 10-21", "contribution": round(diurnal_score, 2)},
            {"label": "Weather code", "input": weather_code if weather_code is not None else "-", "unit": "", "rule": "floor 85 for 95, 96, 99", "contribution": 0.0},
        ],
    }


def _compute_ts_risk(row: pd.Series) -> float:
    """Dashboard TS proxy; TAF phenomenon selection still uses taf_core/vis_cloud_proxy."""
    return float(_ts_proxy_components(row)["total"])


def _ts_proxy_metadata(consensus: pd.DataFrame) -> dict:
    rows = []
    for _, row in consensus.iterrows():
        components = _ts_proxy_components(row)
        rows.append((components["total"], row, components))
    if not rows:
        return {"kind": "convective_support_proxy", "weather_code_legend": WMO_WEATHER_CODES, "peak": None}
    _, peak_row, peak = max(rows, key=lambda item: item[0])
    peak["datetime"] = pd.to_datetime(peak_row["Datetime"]).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "kind": "convective_support_proxy",
        "not_calibrated_probability": True,
        "formula": "0.35 x precipitation probability plus threshold contributions from rain, CAPE, lifted index, CIN, and local convective timing; WMO thunderstorm codes apply an 85-point floor.",
        "weather_code_legend": WMO_WEATHER_CODES,
        "peak": peak,
    }


def _blend_related_weights(global_weights: dict, related_params: list[str]) -> dict[str, float]:
    usable = [global_weights[p] for p in related_params if p in global_weights and global_weights[p]]
    if not usable:
        return {m: 1.0 / len(MODELS) for m in MODELS}
    blended = {m: 0.0 for m in MODELS}
    for weights in usable:
        for model in MODELS:
            blended[model] += float(weights.get(model, 0.0) or 0.0)
    total = sum(blended.values())
    if total <= 0:
        return {m: 1.0 / len(MODELS) for m in MODELS}
    return {m: blended[m] / total for m in MODELS}


def _weighted_quantile(values: list[float], weights: list[float], quantile: float) -> float:
    pairs = sorted((float(v), float(w)) for v, w in zip(values, weights) if pd.notna(v) and float(w) > 0)
    if not pairs:
        return float("nan")
    total = sum(w for _, w in pairs)
    threshold = max(0.0, min(1.0, quantile)) * total
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= threshold:
            return value
    return pairs[-1][0]


def _aviation_visibility_consensus(row: pd.Series, weights: dict[str, float]) -> float:
    valid = row.dropna()
    if valid.empty:
        return float("nan")
    values = [max(50.0, min(9999.0, float(v))) for v in valid.values]
    w = [float(weights.get(model, 1.0 / len(valid.index)) or 0.0) for model in valid.index]
    if sum(w) <= 0:
        w = [1.0 / len(values)] * len(values)
    total = sum(w)
    probs = {
        800: sum(wi for v, wi in zip(values, w) if v < 800.0) / total,
        1500: sum(wi for v, wi in zip(values, w) if v < 1500.0) / total,
        3000: sum(wi for v, wi in zip(values, w) if v < 3000.0) / total,
        5000: sum(wi for v, wi in zip(values, w) if v < 5000.0) / total,
    }
    def has_restricted_consensus(limit: float, probability_threshold: float) -> bool:
        supporting_models = sum(1 for v in values if v < limit)
        # Two outlier models must not create a prevailing aviation restriction.
        # With the usual eight-model ensemble, require three models as well as
        # the configured weighted support. Small ensembles still require every
        # available source to agree through the weighted threshold.
        minimum_models = 2 if len(values) <= 3 else 3
        return supporting_models >= minimum_models and probs[int(limit)] >= probability_threshold

    if has_restricted_consensus(800.0, 0.50):
        return min(_weighted_quantile(values, w, 0.25), 800.0)
    if has_restricted_consensus(1500.0, 0.50):
        return min(_weighted_quantile(values, w, 0.30), 1500.0)
    if has_restricted_consensus(3000.0, 0.55):
        return min(_weighted_quantile(values, w, 0.35), 3000.0)
    if has_restricted_consensus(5000.0, 0.55):
        return min(_weighted_quantile(values, w, 0.40), 5000.0)
    return min(9999.0, _weighted_quantile(values, w, 0.50))


def _aviation_cloud_cover_consensus(row: pd.Series, weights: dict[str, float]) -> float:
    valid = row.dropna()
    if valid.empty:
        return float("nan")
    values = [max(0.0, min(100.0, float(v))) for v in valid.values]
    w = [float(weights.get(model, 1.0 / len(valid.index)) or 0.0) for model in valid.index]
    if sum(w) <= 0:
        w = [1.0 / len(values)] * len(values)
    total = sum(w)
    p_few = sum(wi for v, wi in zip(values, w) if v >= 5.0) / total
    p_sct = sum(wi for v, wi in zip(values, w) if v >= 26.0) / total
    p_bkn = sum(wi for v, wi in zip(values, w) if v >= 51.0) / total
    p_ovc = sum(wi for v, wi in zip(values, w) if v >= 88.0) / total
    if p_ovc >= 0.45:
        return 95.0
    if p_bkn >= 0.45:
        return 70.0
    if p_sct >= 0.40:
        return 38.0
    if p_few >= 0.30:
        return 15.0
    return 0.0

def calculate_advanced_metrics(weighter, df_long: pd.DataFrame, param: str, is_circular: bool):
    """Calculate standard metrics, lead-time metrics, and diurnal bias."""
    metrics_payload = {
        "overall": {},
        "lead_time": {},
        "diurnal_bias": {},
        "significance": {},
        "event_windows": {}
    }
    
    if df_long.empty:
        return metrics_payload
        
    # Calculate Lead Hour
    df_long["Datetime"] = pd.to_datetime(df_long["Datetime"])
    if "Run_Init_UTC" in df_long.columns:
        df_long["Run_Init_UTC"] = pd.to_datetime(df_long["Run_Init_UTC"], format="mixed", utc=True, errors="coerce").dt.tz_localize(None)
        df_long["Lead_Hour"] = (df_long["Datetime"] - df_long["Run_Init_UTC"]).dt.total_seconds() / 3600.0
        df_long["Lead_Hour"] = df_long["Lead_Hour"].fillna(0)
    else:
        df_long["Lead_Hour"] = 0
        
    # Set expected WITA_Target for weighter
    df_w = df_long.copy()
    df_w["WITA_Target"] = df_w["Datetime"]
    
    # Overall Metrics
    overall = weighter.calculate_all_metrics(df_w, parameter=param, is_circular=is_circular)
    for m, sk in overall.items():
        if sk.rmse < 900:
            metrics_payload["overall"][m] = {
                "RMSE": round(sk.rmse, 3), "Bias": round(sk.bias, 3), "MAE": round(sk.mae, 3),
                "HSS": round(sk.hss, 3) if hasattr(sk, 'hss') else None,
                "CSI": round(sk.csi, 3) if hasattr(sk, 'csi') else None
            }
            
    # Lead-Time Analysis
    brackets = {"Day 1": (0, 24), "Day 2": (24, 48), "Day 3": (48, 72), "Day 4+": (72, 999)}
    for b_name, (lo, hi) in brackets.items():
        sub = df_w[(df_w["Lead_Hour"] >= lo) & (df_w["Lead_Hour"] < hi)]
        if len(sub) > 0:
            sub_metrics = weighter.calculate_all_metrics(sub, parameter=param, is_circular=is_circular, min_samples=3)
            b_data = {}
            for m, sk in sub_metrics.items():
                if sk.rmse < 900:
                    b_data[m] = {"RMSE": round(sk.rmse, 3)}
            metrics_payload["lead_time"][b_name] = b_data
            
    # Diurnal Bias (UTC -> Local +8 conceptually)
    df_w["Hour"] = (df_w["Datetime"] + pd.Timedelta(hours=8)).dt.hour
    diurnal = {}
    for m in MODELS:
        m_df = df_w[df_w["Model"] == m]
        if len(m_df) > 0:
            if is_circular:
                # Circular mean error not implemented linearly, skip
                diurnal[m] = {str(h): 0.0 for h in range(24)}
            else:
                bias_by_hour = m_df.groupby("Hour").apply(lambda x: (x["forecast"] - x["obs"]).mean()).to_dict()
                diurnal[m] = {str(h): round(bias_by_hour.get(h, 0.0), 3) for h in range(24)}
    metrics_payload["diurnal_bias"] = diurnal
    
    # Extract Significancy from weighter if it exists
    if hasattr(weighter, 'significance') and param == "Rainfall":
        metrics_payload["significance"] = weighter.significance

    if param == "Rainfall":
        metrics_payload["event_windows"] = {
            "description": "Rain event verification with exact, +/-1h, +/-2h, and 3h block tolerance. Used to separate timing displacement from true misses/false alarms.",
            "threshold": 1.5,
            "models": event_window_metrics(df_w, threshold=1.5, windows=(0, 1, 2), block_hours=3),
        }
    elif param == "Wind Gust":
        metrics_payload["event_windows"] = {
            "description": "Gust event verification with exact, +/-1h, +/-2h, and 3h block tolerance. Peak error uses the strongest forecast gust inside +/-2h around observed gust events.",
            "threshold": 15.0,
            "models": event_window_metrics(df_w, threshold=15.0, windows=(0, 1, 2), block_hours=3),
        }

    return metrics_payload


def _safe_dt(value):
    if value is None or pd.isna(value):
        return None
    dt = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(dt):
        return None
    return dt.to_pydatetime()


TAF_ISSUANCE_HOURS_UTC = {"2300": 23, "0500": 5, "1100": 11, "1700": 17}
TAF_MIN_FRESH_MODELS = 5
TAF_COVERAGE_HOURS = 6


def _observation_freshness(db: ForecastDB, now: datetime | None = None) -> dict:
    """Report source freshness separately from the count of archived observations."""
    now = now or datetime.now(timezone.utc)

    def summarize(table: str) -> dict:
        latest = db.conn.execute(f"SELECT MAX(obs_time) FROM {table}").fetchone()[0]
        latest_dt = _safe_dt(latest)
        if latest_dt is None:
            return {"latest_obs_utc": None, "age_hours": None, "status": "missing"}
        age_hours = max(0.0, (now - latest_dt).total_seconds() / 3600.0)
        if age_hours <= 6.0:
            status = "fresh"
        elif age_hours <= 24.0:
            status = "aging"
        else:
            status = "stale"
        return {
            "latest_obs_utc": latest_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "age_hours": round(age_hours, 2),
            "status": status,
        }

    hourly = summarize("awos_observations")
    minute = summarize("awos_observations_1min")
    verification_status = "ready" if hourly["status"] == "fresh" else "frozen"
    return {
        "hourly": hourly,
        "minute": minute,
        "verification_status": verification_status,
        "note": (
            "Verification-driven weights and residual promotion are frozen until hourly AWOS is fresh."
            if verification_status == "frozen"
            else "Hourly AWOS is fresh enough for verification-driven updates."
        ),
    }


def _taf_window_coverage(
    df_fcst: pd.DataFrame,
    fresh_models: set[str],
    valid_start: pd.Timestamp,
    hours: int = TAF_COVERAGE_HOURS,
) -> dict:
    """Require a common fresh-model cohort across the opening TAF period."""
    expected_times = pd.date_range(valid_start, periods=hours, freq="h")
    frame = df_fcst.copy()
    frame["_forecast_dt"] = pd.to_datetime(frame["forecast_time"])
    common_models = set(fresh_models)
    missing_times = []
    for timestamp in expected_times:
        available = set(frame.loc[frame["_forecast_dt"] == timestamp, "model"].dropna())
        if not available:
            missing_times.append(timestamp.strftime("%Y-%m-%d %H:%M:%S"))
        common_models &= available

    first_hour = frame.loc[frame["_forecast_dt"] == valid_start]
    visibility_models = sorted(
        set(first_hour.loc[first_hour["visibility"].notna(), "model"].dropna()) & common_models
    ) if "visibility" in first_hour.columns else []
    return {
        "coverage_hours": hours,
        "fresh_models_covering_window": sorted(common_models),
        "fresh_model_count": len(common_models),
        "direct_visibility_models": visibility_models,
        "direct_visibility_model_count": len(visibility_models),
        "missing_window_hours": missing_times,
        "coverage_status": "sufficient" if len(common_models) >= TAF_MIN_FRESH_MODELS else "degraded",
    }


def _select_default_taf_window(
    df_fcst: pd.DataFrame,
    fresh_models: set[str],
    reference_utc: datetime,
) -> dict:
    """Choose the next official issuance with six hours of quorum coverage."""
    candidates = []
    for day_offset in range(3):
        day = (reference_utc + timedelta(days=day_offset)).date()
        for issuance, issue_hour in TAF_ISSUANCE_HOURS_UTC.items():
            issue_dt = datetime(day.year, day.month, day.day, issue_hour, tzinfo=timezone.utc)
            if issue_dt < reference_utc:
                continue
            valid_start_wita = pd.Timestamp((issue_dt + timedelta(hours=9)).replace(tzinfo=None))
            coverage = _taf_window_coverage(df_fcst, fresh_models, valid_start_wita)
            candidates.append({
                "issuance": issuance,
                "issue_utc": issue_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "valid_start_wita": valid_start_wita.strftime("%Y-%m-%d %H:%M:%S"),
                **coverage,
            })

    candidates.sort(key=lambda item: item["issue_utc"])
    selected = next((item for item in candidates if item["coverage_status"] == "sufficient"), None)
    return {
        "selection_status": "selected" if selected else "suppressed",
        "selected_issuance": selected["issuance"] if selected else None,
        "selected_valid_start_wita": selected["valid_start_wita"] if selected else None,
        "reference_utc": reference_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "minimum_fresh_models": TAF_MIN_FRESH_MODELS,
        "coverage_hours": TAF_COVERAGE_HOURS,
        "candidates": candidates,
        "reason": (
            "Next official issuance has fresh-model quorum coverage."
            if selected else
            "No upcoming official issuance has fresh-model quorum coverage; default TAF suppressed."
        ),
    }


def _taf_provenance(
    df_fcst: pd.DataFrame,
    fresh_models: set[str],
    model_freshness: list[dict],
    valid_start: str | None,
    observation_freshness: dict,
) -> dict:
    if not valid_start:
        return {"coverage_status": "unavailable"}
    coverage = _taf_window_coverage(df_fcst, fresh_models, pd.Timestamp(valid_start))
    model_ages = {
        row["model"]: row.get("age_hours")
        for row in model_freshness
        if row.get("model") in coverage["fresh_models_covering_window"]
    }
    return {
        **coverage,
        "model_run_age_hours": model_ages,
        "cloud_base_source": "proxy",
        "observation_verification_status": observation_freshness.get("verification_status"),
        "observation_hourly_age_hours": (observation_freshness.get("hourly") or {}).get("age_hours"),
    }


def _model_placeholders() -> str:
    return ",".join("?" for _ in MODELS)


def _model_params(multiplier: int = 1) -> tuple[str, ...]:
    return tuple(MODELS) * multiplier


def export_system_workflow(
    db: ForecastDB,
    output_dir: str,
    db_health: dict,
    latest_time: str | None = None,
    observation_freshness: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    model_filter = _model_placeholders()
    current_rows = pd.read_sql_query(f"""
        WITH latest AS (
            SELECT model, MAX(scraped_at) AS max_scraped
            FROM openmeteo_forecasts
            WHERE run_init_utc <> 'historical_forecast_api'
              AND lead_hours >= 0
              AND model IN ({model_filter})
            GROUP BY model
        )
        SELECT
            f.model,
            COUNT(*) AS row_count,
            MAX(f.scraped_at) AS latest_scraped_at,
            MAX(f.run_init_utc) AS latest_run_init_utc,
            MIN(f.forecast_time) AS first_forecast_time,
            MAX(f.forecast_time) AS last_forecast_time,
            MIN(f.lead_hours) AS min_lead_hours,
            MAX(f.lead_hours) AS max_lead_hours
        FROM openmeteo_forecasts f
        INNER JOIN latest
            ON f.model = latest.model
           AND f.scraped_at = latest.max_scraped
        WHERE f.run_init_utc <> 'historical_forecast_api'
          AND f.model IN ({model_filter})
          AND f.lead_hours >= 0
        GROUP BY f.model
        ORDER BY f.model
    """, db.conn, params=_model_params(2))

    try:
        audit_rows = pd.read_sql_query(f"""
            WITH latest AS (
                SELECT model, MAX(scraped_at) AS max_scraped
                FROM openmeteo_model_runs
                WHERE model IN ({model_filter})
                GROUP BY model
            )
            SELECT r.*
            FROM openmeteo_model_runs r
            INNER JOIN latest
                ON r.model = latest.model
               AND r.scraped_at = latest.max_scraped
            WHERE r.model IN ({model_filter})
            ORDER BY r.model
        """, db.conn, params=_model_params(2))
    except Exception:
        audit_rows = pd.DataFrame()

    audit_by_model = {}
    if not audit_rows.empty:
        for _, audit in audit_rows.iterrows():
            missing = []
            null_ratios = {}
            try:
                missing = json.loads(audit.get("missing_parameters_json") or "[]")
            except Exception:
                missing = []
            try:
                null_ratios = json.loads(audit.get("null_ratio_json") or "{}")
            except Exception:
                null_ratios = {}
            audit_by_model[audit["model"]] = {
                "openmeteo_model_id": audit.get("openmeteo_model_id"),
                "provider": audit.get("provider"),
                "detected_interval_hours": audit.get("detected_interval_hours"),
                "expected_output_interval_hours": audit.get("expected_output_interval_hours"),
                "provider_update_frequency_hours": audit.get("provider_update_frequency_hours"),
                "forecast_horizon_hours": audit.get("forecast_horizon_hours"),
                "missing_parameters": missing,
                "null_ratio": null_ratios,
                "quality_status": audit.get("quality_status") or "unknown",
                "quality_notes": audit.get("quality_notes") or "",
                "hourly_output_note": audit.get("hourly_output_note") or "",
            }

    freshness = []
    for _, row in current_rows.iterrows():
        model = row["model"]
        meta = model_metadata_dict(model)
        audit = audit_by_model.get(model, {})
        scraped_dt = _safe_dt(row["latest_scraped_at"])
        age_hours = None
        if scraped_dt:
            age_hours = max(0.0, (now - scraped_dt).total_seconds() / 3600.0)
        status = freshness_status(age_hours, audit.get("provider_update_frequency_hours") or meta.get("provider_update_frequency_hours"))
        freshness.append({
            "model": model,
            "openmeteo_model_id": audit.get("openmeteo_model_id") or meta.get("openmeteo_id"),
            "provider": audit.get("provider") or meta.get("provider"),
            "row_count": int(row["row_count"]),
            "latest_scraped_at": row["latest_scraped_at"],
            "latest_run_init_utc": row["latest_run_init_utc"],
            "first_forecast_time": row["first_forecast_time"],
            "last_forecast_time": row["last_forecast_time"],
            "min_lead_hours": None if pd.isna(row["min_lead_hours"]) else float(row["min_lead_hours"]),
            "max_lead_hours": None if pd.isna(row["max_lead_hours"]) else float(row["max_lead_hours"]),
            "age_hours": None if age_hours is None else round(age_hours, 2),
            "status": status,
            "detected_interval_hours": audit.get("detected_interval_hours"),
            "expected_output_interval_hours": audit.get("expected_output_interval_hours") or meta.get("expected_output_interval_hours"),
            "provider_update_frequency_hours": audit.get("provider_update_frequency_hours") or meta.get("provider_update_frequency_hours"),
            "forecast_horizon_hours": audit.get("forecast_horizon_hours") or meta.get("forecast_horizon_hours"),
            "quality_status": audit.get("quality_status") or "unknown",
            "quality_notes": audit.get("quality_notes") or "",
            "missing_parameters": audit.get("missing_parameters", []),
            "temporal_confidence": meta.get("temporal_confidence"),
            "hourly_output_note": audit.get("hourly_output_note") or meta.get("hourly_output_note"),
        })

    historical_rows = pd.read_sql_query(f"""
        SELECT model, COUNT(*) AS row_count, MIN(forecast_time) AS first_time, MAX(forecast_time) AS last_time
        FROM openmeteo_forecasts f
        WHERE run_init_utc = 'historical_forecast_api'
          AND model IN ({model_filter})
        GROUP BY model
        ORDER BY model
    """, db.conn, params=_model_params())
    historical_coverage = historical_rows.to_dict(orient="records")
    for row in historical_coverage:
        row["row_count"] = int(row["row_count"])

    qm_rows = pd.read_sql_query(f"""
        SELECT model, parameter, lead_bucket, n_samples, low_confidence,
               COALESCE(source_type, 'unknown') AS source_type,
               COALESCE(correction_layer, 'historical_prior') AS correction_layer,
               COALESCE(regime, 'ALL') AS regime,
               skill_score
        FROM qm_cdfs
        WHERE enabled = 1
          AND COALESCE(deprecated, 0) = 0
          AND model IN ({model_filter})
        ORDER BY model, parameter, lead_bucket
    """, db.conn, params=_model_params())
    qm_by_parameter = {}
    qm_by_model = {}
    qm_by_source = {}
    qm_by_layer = {}
    low_confidence = []
    for _, row in qm_rows.iterrows():
        qm_by_parameter[row["parameter"]] = qm_by_parameter.get(row["parameter"], 0) + 1
        qm_by_model[row["model"]] = qm_by_model.get(row["model"], 0) + 1
        qm_by_source[row["source_type"]] = qm_by_source.get(row["source_type"], 0) + 1
        qm_by_layer[row["correction_layer"]] = qm_by_layer.get(row["correction_layer"], 0) + 1
        if int(row["low_confidence"] or 0):
            low_confidence.append({
                "model": row["model"],
                "parameter": row["parameter"],
                "lead_bucket": row["lead_bucket"],
                "n_samples": int(row["n_samples"]),
                "source_type": row["source_type"],
                "correction_layer": row["correction_layer"],
            })

    lead_rows = pd.read_sql_query(f"""
        WITH latest AS (
            SELECT model, MAX(scraped_at) AS max_scraped
            FROM openmeteo_forecasts
            WHERE run_init_utc <> 'historical_forecast_api'
              AND lead_hours >= 0
              AND model IN ({model_filter})
            GROUP BY model
        )
        SELECT
            CASE
                WHEN f.lead_hours <= 6 THEN 'L1_0_6h'
                WHEN f.lead_hours <= 12 THEN 'L2_6_12h'
                WHEN f.lead_hours <= 24 THEN 'L3_12_24h'
                WHEN f.lead_hours <= 48 THEN 'L4_24_48h'
                ELSE 'L5_48plus'
            END AS lead_bucket,
            COUNT(*) AS row_count
        FROM openmeteo_forecasts f
        INNER JOIN latest
            ON f.model = latest.model
           AND f.scraped_at = latest.max_scraped
        WHERE f.run_init_utc <> 'historical_forecast_api'
          AND f.model IN ({model_filter})
          AND f.lead_hours >= 0
        GROUP BY lead_bucket
        ORDER BY lead_bucket
    """, db.conn, params=_model_params(2))

    lead_bucket_rows = lead_rows.to_dict(orient="records")
    for row in lead_bucket_rows:
        row["row_count"] = int(row["row_count"])

    workflow = {
        "metadata": {
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "latest_forecast_scrape": latest_time,
            "timezone": "UTC",
        },
        "summary": {
            "current_forecast_rows": db_health.get("forecast_records"),
            "openmeteo_rows_total": db_health.get("openmeteo_records"),
            "awos_hourly_rows": db_health.get("observation_records"),
            "awos_1min_rows": db_health.get("observation_1min_records"),
            "qm_cdfs_enabled": db_health.get("qm_cdfs_enabled"),
            "active_model_count": int(len(freshness)),
            "historical_model_count": int(len(historical_coverage)),
            "observation_verification_status": (observation_freshness or {}).get("verification_status", "unknown"),
        },
        "pipeline_stages": [
            {"name": "Open-Meteo fetch", "output": "current 16-day model forecasts"},
            {"name": "SQLite archive", "output": "run_init_utc, forecast_time, lead_hours, scraped_at"},
            {"name": "AWOS ingestion", "output": "hourly and 1-minute observations"},
            {"name": "Training pairs", "output": "forecast-observation joins"},
            {"name": "QM calibration", "output": "parameter/model correction CDFs"},
            {"name": "Dynamic weights", "output": "recent skill-weighted ensemble"},
            {"name": "TAF engine", "output": "base group, change groups, warnings"},
            {"name": "Dashboard export", "output": "static JSON under docs/data"},
        ],
        "model_freshness": freshness,
        "observation_freshness": observation_freshness or {},
        "model_registry": registry_payload(),
        "historical_coverage": historical_coverage,
        "lead_bucket_rows_current": lead_bucket_rows,
        "qm_calibration": {
            "enabled_by_parameter": qm_by_parameter,
            "enabled_by_model": qm_by_model,
            "enabled_by_source": qm_by_source,
            "enabled_by_layer": qm_by_layer,
            "low_confidence": low_confidence,
            "note": "Historical Forecast API calibration is used as a non-lead-aware historical prior. Operational multi-init data trains lead residuals when enough verified pairs exist.",
        },
    }

    with open(os.path.join(output_dir, "system_workflow.json"), "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(workflow), f, indent=2, default=str, allow_nan=False)
    model_audit = {
        "metadata": workflow["metadata"],
        "registry": registry_payload(),
        "latest_runs": freshness,
        "notes": [
            "Forecast values are exported on Open-Meteo's hourly output grid.",
            "Provider update frequency is tracked separately and should guide freshness, weighting confidence, and event verification.",
            "Rainfall and gust should be verified with event windows, not strict hourly-only scores.",
        ],
    }
    with open(os.path.join(output_dir, "model_audit.json"), "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(model_audit), f, indent=2, default=str, allow_nan=False)
    return workflow

def export_all(db: ForecastDB, output_dir: str, qm_artifact_status: dict | None = None):
    """
    Exports JSON payloads for the HTML dashboard:
    1. tafor_intel.json (Consensus timeline + TAF)
    2. latest_weights.json (Current model weights)
    3. latest_performance.json (Metrics)
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 0. Database Health Checker
    export_now = datetime.now(timezone.utc)
    db_path = db.conn.execute("PRAGMA database_list").fetchall()[0][2]
    db_size_bytes = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0
    model_filter = _model_placeholders()
    forecast_count = db.conn.execute(
        f"""
        WITH latest AS (
            SELECT model, MAX(scraped_at) AS max_scraped
            FROM openmeteo_forecasts
            WHERE run_init_utc <> 'historical_forecast_api'
              AND lead_hours >= 0
              AND model IN ({model_filter})
            GROUP BY model
        )
        SELECT COUNT(*)
        FROM openmeteo_forecasts f
        INNER JOIN latest
            ON f.model = latest.model
           AND f.scraped_at = latest.max_scraped
        WHERE f.run_init_utc <> 'historical_forecast_api'
          AND f.lead_hours >= 0
          AND f.model IN ({model_filter})
        """,
        _model_params(2)
    ).fetchone()[0]
    obs_count = db.conn.execute("SELECT COUNT(*) FROM awos_observations").fetchone()[0]
    obs_1min_count = db.conn.execute("SELECT COUNT(*) FROM awos_observations_1min").fetchone()[0]
    openmeteo_count = db.conn.execute("SELECT COUNT(*) FROM openmeteo_forecasts").fetchone()[0]
    historical_openmeteo_count = db.conn.execute(
        "SELECT COUNT(*) FROM openmeteo_forecasts WHERE run_init_utc = 'historical_forecast_api'"
    ).fetchone()[0]
    operational_openmeteo_count = db.conn.execute(
        f"""
        SELECT COUNT(*)
        FROM openmeteo_forecasts
        WHERE run_init_utc <> 'historical_forecast_api'
          AND lead_hours >= 0
          AND model IN ({model_filter})
        """,
        _model_params()
    ).fetchone()[0]
    qm_cdf_count = db.conn.execute(
        f"SELECT COUNT(*) FROM qm_cdfs WHERE enabled=1 AND COALESCE(deprecated,0)=0 AND model IN ({model_filter})",
        _model_params()
    ).fetchone()[0]
    latest_model_run_init = db.conn.execute(
        f"SELECT MAX(run_init_utc) FROM openmeteo_forecasts WHERE run_init_utc <> 'historical_forecast_api' AND lead_hours >= 0 AND model IN ({model_filter})",
        _model_params()
    ).fetchone()[0]
    latest_data_pull_utc = db.conn.execute(
        f"SELECT MAX(scraped_at) FROM openmeteo_forecasts WHERE run_init_utc <> 'historical_forecast_api' AND lead_hours >= 0 AND model IN ({model_filter})",
        _model_params()
    ).fetchone()[0]
    observation_freshness = _observation_freshness(db, export_now)
    verification_frozen = observation_freshness.get("verification_status") == "frozen"
    db_health = {
        "size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "current_forecast_records": forecast_count,
        "forecast_records": forecast_count,
        "operational_forecast_records": operational_openmeteo_count,
        "historical_forecast_records": historical_openmeteo_count,
        "observation_records": obs_count,
        "observation_1min_records": obs_1min_count,
        "openmeteo_records": openmeteo_count,
        "runtime_qm_cdfs_enabled": qm_cdf_count,
        "qm_cdfs_enabled": qm_cdf_count,
        "qm_artifact_status": qm_artifact_status or {},
        "latest_model_run_init_utc": latest_model_run_init,
        "latest_data_pull_utc": latest_data_pull_utc,
        "last_sync_utc": export_now.strftime("%Y-%m-%d %H:%M:%S"),
        "observation_freshness": observation_freshness,
    }
    existing_db_health = _load_json_file(os.path.join(output_dir, "db_health.json")) or {}
    existing_obs_count = int(existing_db_health.get("observation_records") or 0)
    if obs_count < 10000 and existing_obs_count > obs_count:
        for field in ["size_mb", "observation_records", "observation_1min_records", "openmeteo_records", "historical_forecast_records", "qm_cdfs_enabled"]:
            old_value = existing_db_health.get(field)
            new_value = db_health.get(field)
            if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
                db_health[field] = max(old_value, new_value)
        db_health["archive_status"] = "historical archive counters preserved from previous full export"
    with open(os.path.join(output_dir, "db_health.json"), "w") as f:
        json.dump(db_health, f, indent=2)
        
    # 1. Calculate Weights & Metrics
    log.info("Calculating dynamic weights from recent observations...")
    if verification_frozen:
        log.warning("Hourly AWOS is stale; freezing verification-driven weights, metrics, and residual promotion")
    weighter = AdvancedEnsembleWeighter()    
    global_weights = {}
    global_metrics = {
        "24 Hours": {}, "3 Days": {}, "7 Days": {}, "Month": {}
    }
    event_weight_diagnostics = {}
    
    # Use last 60 days of overlap for the baseline pull
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=60)
    
    start_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_date.strftime("%Y-%m-%d %H:%M:%S")
    
    model_data = {}
    qm_mapper = QuantileMapper()
    
    for param in ["Temperature", "Dewpoint", "Pressure", "Rainfall", "Wind Speed", "Wind Dir.", "Wind Gust"]:
        global_weights[param] = {m: 1.0/len(MODELS) for m in MODELS}
        
        # Native LONG format from DB
        df_long_60d = pd.DataFrame() if verification_frozen else db.get_verification_pairs(param, start_str, end_str)
        is_circular = (param == "Wind Dir.")
        
        # --- LOOKBACK METRICS CALCULATION ---
        if not df_long_60d.empty:
            df_long_60d["Datetime_obj"] = pd.to_datetime(df_long_60d["Datetime"])
            max_dt = df_long_60d["Datetime_obj"].max()
            
            lookbacks = {
                "24 Hours": 1,
                "3 Days": 3,
                "7 Days": 7,
                "Month": 30
            }
            
            for lb_name, days in lookbacks.items():
                lb_thresh = max_dt - pd.Timedelta(days=days)
                df_lb = df_long_60d[df_long_60d["Datetime_obj"] >= lb_thresh]
                # calculate advanced metrics
                global_metrics[lb_name][param] = calculate_advanced_metrics(weighter, df_lb, param, is_circular)
        else:
            for lb_name in global_metrics.keys():
                global_metrics[lb_name][param] = calculate_advanced_metrics(weighter, pd.DataFrame(), param, is_circular)
                
        # --- GLOBAL WEIGHTS & MAPPER (Using 60d) ---
        df_wide = _long_to_wide(df_long_60d, param)
        
        if not df_long_60d.empty:
            # Set WITA_Target for CRPS
            df_crps = df_long_60d.copy()
            df_crps["WITA_Target"] = pd.to_datetime(df_crps["Datetime"])
            try:
                w = weighter.calculate_weights_crps(df_crps, parameter=param, is_circular=is_circular)
            except Exception as e:
                log.warning(f"CRPS weights failed for {param}: {e}")
                w = _skill_fallback_weights(df_long_60d, param, is_circular)
            if _weights_effectively_equal(w):
                w = _skill_fallback_weights(df_long_60d, param, is_circular)

            if param in {"Rainfall", "Wind Gust"}:
                threshold = 1.5 if param == "Rainfall" else 15.0
                event_metrics_60d = event_window_metrics(df_crps, threshold=threshold, windows=(0, 1, 2), block_hours=3)
                event_diag = event_window_weight_scores(event_metrics_60d, parameter=param, models=MODELS, min_events=10)
                event_diag["blend_factor"] = 0.30
                event_diag["threshold"] = threshold
                event_weight_diagnostics[param] = event_diag
                if event_diag.get("applied") and event_diag.get("event_weights"):
                    w = _blend_event_window_weights(w, event_diag["event_weights"], blend_factor=0.30)
                
            global_weights[param] = w
            
            # If Rainfall, update Quantile Mapper
            if param == "Rainfall":
                try:
                    for m in MODELS:
                        if m in df_wide.columns:
                            m_df = df_wide[[m, "OBS_Rainfall"]].dropna()
                            m_df = m_df.rename(columns={m: "Rain", "OBS_Rainfall": "OBS_Rain"})
                            qm_mapper.fit_model(m_df, model=m)
                    qm_mapper.save()
                except Exception as e:
                    log.error(f"QM Fit failed: {e}")
                    
        # Apply QM to Rainfall forecasts
        if param == "Rainfall" and not df_wide.empty:
            p_df = df_wide.copy()
            p_df.set_index("Datetime", inplace=True)
            for m in p_df.columns:
                if m in MODELS:
                    p_df[m] = qm_mapper.transform_series(p_df[m], model=m)
            # Disable diurnal bias for now
            
            model_data[param] = {m: p_df[m].dropna() for m in p_df.columns if m in MODELS}
        elif not df_wide.empty:
            p_df = df_wide.copy().set_index("Datetime")
            model_data[param] = {m: p_df[m].dropna() for m in p_df.columns if m in MODELS}
        elif param in {"Rainfall", "Wind Gust"}:
            event_weight_diagnostics[param] = {
                "applied": False,
                "reason": "event-window weighting pending because no forecast-observation pairs were available",
                "blend_factor": 0.30,
                "min_events": 10,
                "event_weights": {},
                "model_scores": {},
            }
            
    # --- RESTORE LEGACY INTELLIGENCE ---
    # Since DB is new, we fallback to the trained weights and diurnal biases from New_CODE
    legacy_guidance_path = ""
    legacy_params = {}
    if os.path.exists(legacy_guidance_path):
        try:
            with open(legacy_guidance_path, 'r', encoding='utf-8') as f:
                lg = json.load(f)
                legacy_params = lg.get('parameters', {})
                
            # Override global_weights if DB is sparse
            param_legacy_map = {
                "Temperature": "temperature",
                "Dewpoint": "dewpoint",
                "Pressure": "pressure",
                "Rainfall": "rainfall",
                "Wind Speed": "wind_speed",
                "Wind Dir.": "wind_direction"
            }
            
            for param, legacy_key in param_legacy_map.items():
                if legacy_key in legacy_params and "optimal_weights" in legacy_params[legacy_key]:
                    lw = legacy_params[legacy_key]["optimal_weights"]
                    # If CRPS failed (weights are equal 1/7), override them!
                    if all(abs(global_weights[param][m] - 1.0/len(MODELS)) < 0.01 for m in MODELS):
                        new_w = {m: lw.get(m, 1.0/len(MODELS)) for m in MODELS}
                        tot = sum(new_w.values())
                        global_weights[param] = {m: v/tot for m,v in new_w.items()}
        except Exception as e:
            log.warning(f"Failed to load legacy guidance: {e}")

    # If this runner has no forecast-observation overlap yet, reuse the last
    # verified weights from the static dashboard export before generating
    # consensus. This keeps sparse GitHub runners from reverting consensus to
    # equal weighting while the full historical archive lives elsewhere.
    existing_weights_payload = _load_json_file(os.path.join(output_dir, "latest_weights.json")) or {}
    existing_weights_map = existing_weights_payload.get("weights", {})
    for param, model_weights in list(global_weights.items()):
        prior_weights = existing_weights_map.get(param, {})
        if (
            _weights_effectively_equal(model_weights)
            and prior_weights
            and not _weights_effectively_equal(prior_weights)
        ):
            prior = {m: float(prior_weights.get(m, 0.0) or 0.0) for m in MODELS}
            total = sum(prior.values())
            if total > 0:
                global_weights[param] = {m: prior[m] / total for m in MODELS}
                log.warning(f"Using preserved verified weights for {param}; current export has no skill pairs")
    existing_event_diagnostics = (existing_weights_payload.get("metadata") or {}).get("event_weight_diagnostics", {})
    if (
        not _event_diagnostics_have_signal(event_weight_diagnostics)
        and _event_diagnostics_have_signal(existing_event_diagnostics)
    ):
        event_weight_diagnostics = existing_event_diagnostics
        log.warning("Using preserved event-window diagnostics; current export has no event skill pairs")

    residuals_path = os.path.join(output_dir, "operational_residuals.json")
    residual_state = build_operational_residual_state(db.conn, start_str, end_str, list(MODELS))
    existing_residual_state = _load_json_file(residuals_path) or {}
    if _residual_state_sample_count(existing_residual_state) > _residual_state_sample_count(residual_state):
        residual_state = existing_residual_state
        log.warning("Preserving existing operational_residuals.json because current export has fewer operational pairs")
    with open(residuals_path, "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(residual_state), f, indent=2)
    db_health["operational_residuals"] = residual_state.get("metadata", {})
    with open(os.path.join(output_dir, "db_health.json"), "w") as f:
        json.dump(db_health, f, indent=2)
            
    # 2. Extract Forecast Data & Generate Consensus
    # Wait, guidance_generator expects model_data in memory. 
    # run_pipeline currently passes nothing to export_all! We need to fetch the latest forecast.
    
    query_latest = f"SELECT MAX(scraped_at) FROM openmeteo_forecasts WHERE run_init_utc <> 'historical_forecast_api' AND lead_hours >= 0 AND model IN ({model_filter})"
    latest_time_df = pd.read_sql_query(query_latest, db.conn, params=_model_params())
    latest_time = latest_time_df.iloc[0, 0]
    
    if not latest_time:
        log.warning("No forecasts found in DB.")
        workflow = export_system_workflow(
            db, output_dir, db_health, latest_time=None, observation_freshness=observation_freshness
        )
        db_health["model_freshness"] = workflow.get("model_freshness", [])
        with open(os.path.join(output_dir, "db_health.json"), "w") as f:
            json.dump(db_health, f, indent=2)
        return
        
    log.info(f"Generating TAF guidance for latest scrape: {latest_time}")
    
    # Get the latest scrape for EACH model to handle partial failures
    query_fcst = f"""
        SELECT m.*
        FROM openmeteo_forecasts m
        INNER JOIN (
            SELECT model, MAX(scraped_at) as max_scraped
            FROM openmeteo_forecasts
            WHERE run_init_utc <> 'historical_forecast_api'
              AND lead_hours >= 0
              AND model IN ({model_filter})
            GROUP BY model
        ) latest
        ON m.model = latest.model AND m.scraped_at = latest.max_scraped
        WHERE m.run_init_utc <> 'historical_forecast_api'
          AND m.lead_hours >= 0
          AND m.model IN ({model_filter})
    """
    df_fcst = pd.read_sql_query(query_fcst, db.conn, params=_model_params(2))
    workflow = export_system_workflow(
        db, output_dir, db_health, latest_time=latest_time, observation_freshness=observation_freshness
    )
    db_health["model_freshness"] = workflow.get("model_freshness", [])
    with open(os.path.join(output_dir, "db_health.json"), "w") as f:
        json.dump(db_health, f, indent=2)
    active_models = sorted(df_fcst["model"].dropna().unique().tolist())
    run_init_by_model = {}
    scraped_at_by_model = {}
    if 'run_init_utc' in df_fcst.columns:
        for m in active_models:
            m_df = df_fcst[df_fcst["model"] == m].dropna(subset=['run_init_utc'])
            run_init_by_model[m] = str(m_df['run_init_utc'].max()) if not m_df.empty else None
            s_df = df_fcst[df_fcst["model"] == m].dropna(subset=['scraped_at'])
            scraped_at_by_model[m] = str(s_df['scraped_at'].max()) if not s_df.empty else None
    record_id_by_model_time = {}
    if "id" in df_fcst.columns:
        for _, row in df_fcst.iterrows():
            record_id_by_model_time[(row["model"], str(row["forecast_time"]))] = int(row["id"])
    
    model_data = {param: {} for param in ["Temperature", "Dewpoint", "Humidity", "Pressure", "Rainfall", "Wind Speed", "Wind Dir.", "Wind Gust", "Visibility", "Precip Probability", "Sunshine", "Low Clouds", "Mid Clouds", "High Clouds", "Condition", "CAPE", "Lifted Index", "Convective Inhibition", "Weather Code"]}
    
    param_map = {
        "Temperature": "temperature",
        "Dewpoint": "dewpoint",
        "Humidity": "humidity",
        "Pressure": "pressure_msl",
        "Rainfall": "rain",
        "Wind Speed": "wind_speed",
        "Wind Dir.": "wind_dir",
        "Wind Gust": "wind_gust",
        "Visibility": "visibility",
        "Precip Probability": "precipitation_probability",
        "Sunshine": "sunshine_duration",
        "Low Clouds": "cloud_cover_low",
        "Mid Clouds": "cloud_cover_mid",
        "High Clouds": "cloud_cover_high",
        "Condition": "condition",
        "CAPE": "cape",
        "Lifted Index": "lifted_index",
        "Convective Inhibition": "convective_inhib",
        "Weather Code": "weather_code"
    }
    
    for m in active_models:
        m_df = df_fcst[df_fcst["model"] == m].sort_values("forecast_time")
        if not m_df.empty:
            m_df.index = pd.to_datetime(m_df["forecast_time"])
            for param, db_col in param_map.items():
                if db_col in m_df.columns:
                    model_data[param][m] = m_df[db_col]
                    
    # Generate Consensus (needs weights!)
    from src.utils import circular_weighted_mean
    import numpy as np
    
    consensus = pd.DataFrame(index=pd.to_datetime(df_fcst["forecast_time"].unique()).sort_values())
    consensus.index.name = "Datetime"
    pipeline_run_id = latest_time or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    related_weight_params = {
        "Humidity": ["Dewpoint", "Temperature"],
        "Visibility": ["Rainfall", "Dewpoint", "Wind Speed"],
        "Precip Probability": ["Rainfall"],
        "Sunshine": ["Rainfall"],
        "Low Clouds": ["Dewpoint", "Rainfall", "Pressure"],
        "Mid Clouds": ["Dewpoint", "Rainfall", "Pressure"],
        "High Clouds": ["Dewpoint", "Rainfall", "Pressure"],
        "CAPE": ["Rainfall"],
        "Lifted Index": ["Rainfall"],
        "Convective Inhibition": ["Rainfall"],
        "Weather Code": ["Rainfall"],
    }
    qm_provenance = {
        "total_values": 0,
        "by_layer": {"historical_prior": 0, "operational_residual": 0, "raw": 0},
        "by_parameter": {},
        "low_confidence": 0,
        "lead_aware_pending": 0,
        "operational_residual_available": 0,
        "operational_residual_mode": "enabled" if operational_residual_qm_enabled() else "observe_only",
        "artifact_status": qm_artifact_status or {},
        "historical_prior_label": "Bias-corrected: historical prior, global, not lead-aware",
        "rainfall_note": "Rain occurrence prior may inform risk; rainfall amount correction remains strict/pending.",
    }
    
    for param in model_data.keys():
        if param == "Condition":
            continue
            
        weights = global_weights.get(param)
        if not weights:
            weights = _blend_related_weights(global_weights, related_weight_params.get(param, []))
        p_df = pd.DataFrame(model_data[param])
        if p_df.empty:
            continue
        
        # Quantile Map Rainfall before consensus
        qm_param_map = {
            "Temperature": "temperature",
            "Dewpoint": "dewpoint",
            "Pressure": "pressure",
            "Rainfall": "rain",
            "Wind Speed": "wind_speed",
            "Wind Gust": "wind_gust",
            "Wind Dir.": "wind_dir",
        }
        if param in qm_param_map and qm_cdf_count > 0:
            for m in p_df.columns:
                run_init = run_init_by_model.get(m)
                if run_init:
                    lead_hours = (p_df.index - (pd.to_datetime(run_init) + pd.Timedelta(hours=8))).total_seconds() / 3600.0
                else:
                    lead_hours = [0.0] * len(p_df)
                corrected_values = []
                for ts, v, lh in zip(p_df.index, p_df[m].values, lead_hours):
                    correction = apply_qm_with_layers(v, m, qm_param_map[param], lh, conn=db.conn)
                    corrected_values.append(correction["final_value"])
                    layer = correction["correction_layer_used"]
                    qm_provenance["total_values"] += 1
                    qm_provenance["by_layer"][layer] = qm_provenance["by_layer"].get(layer, 0) + 1
                    qm_provenance["by_parameter"].setdefault(param, {"historical_prior": 0, "operational_residual": 0, "raw": 0})
                    qm_provenance["by_parameter"][param][layer] = qm_provenance["by_parameter"][param].get(layer, 0) + 1
                    if correction.get("low_confidence"):
                        qm_provenance["low_confidence"] += 1
                    if correction.get("operational_residual_available"):
                        qm_provenance["operational_residual_available"] += 1
                    if layer == "historical_prior" and float(lh or 0.0) > 6.0:
                        qm_provenance["lead_aware_pending"] += 1
                    record_id = record_id_by_model_time.get((m, ts.strftime("%Y-%m-%d %H:%M:%S")))
                    if record_id is not None:
                        db.conn.execute("""
                            INSERT OR IGNORE INTO qm_corrections_applied (
                                forecast_record_id, model, parameter, valid_time, lead_hours,
                                raw_value, historical_prior_value, operational_residual_value,
                                final_corrected_value, historical_qm_id, operational_qm_id,
                                correction_layer_used, applied_at, pipeline_run_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            record_id, m, qm_param_map[param], ts.strftime("%Y-%m-%d %H:%M:%S"), float(lh or 0.0),
                            None if pd.isna(correction["raw_value"]) else float(correction["raw_value"]),
                            None if pd.isna(correction["historical_prior_value"]) else float(correction["historical_prior_value"]),
                            None if correction["operational_residual_value"] is None or pd.isna(correction["operational_residual_value"]) else float(correction["operational_residual_value"]),
                            None if pd.isna(correction["final_value"]) else float(correction["final_value"]),
                            correction["historical_qm_id"], correction["operational_qm_id"],
                            layer, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), pipeline_run_id,
                        ))
                p_df[m] = corrected_values
        elif param == "Rainfall":
            for m in p_df.columns:
                p_df[m] = qm_mapper.transform_series(p_df[m], model=m)
                
        # Apply Diurnal Bias from legacy guidance - DISABLED due to UTC/Local skewing
        # legacy_key = param_legacy_map.get(param) if 'param_legacy_map' in locals() else None
        # if legacy_key and legacy_key in legacy_params:
        #     diurnal_bias = legacy_params[legacy_key].get("diurnal_bias", {})
        #     if diurnal_bias:
        #         for m in p_df.columns:
        #             m_bias = diurnal_bias.get(m, {})
        #             if m_bias:
        #                 def apply_bias(row):
        #                     if pd.isna(row): return row
        #                     hr = str(row.name.hour)
        #                     offset = float(m_bias.get(hr, 0.0))
        #                     if param in ["Rainfall", "Wind Speed"]:
        #                         return max(0.0, row - offset)
        #                     elif param == "Wind Dir.":
        #                         return (row - offset) % 360.0
        #                     return row - offset
        #                 p_df[m] = p_df[m].to_frame().apply(lambda x: apply_bias(x), axis=1)
                
        def row_weights(valid_index):
            if len(valid_index) == 0:
                return []
            w = [float(weights.get(m, 1.0 / len(valid_index)) or 0.0) for m in valid_index]
            if sum(w) <= 0:
                return [1.0 / len(valid_index)] * len(valid_index)
            return w

        if param == "Wind Dir.":
            consensus[param] = p_df.apply(
                lambda row: circular_weighted_mean(row.dropna().values, row_weights(row.dropna().index))
                if not row.dropna().empty else np.nan,
                axis=1
            )
        elif param == "Rainfall":
            def apply_rain_consensus(row):
                valid = row.dropna()
                if valid.empty: return np.nan
                w = [weights.get(m, 1.0 / len(valid.index)) for m in valid.index]
                w_sum = sum(w)
                if w_sum == 0: return np.nan
                r_mean = np.average(valid, weights=w)
                if r_mean >= 1.0:
                    var = sum(w_i * (r - r_mean)**2 for r, w_i in zip(valid, w)) / w_sum
                    r_std = var ** 0.5
                    if r_std >= 2.0 * r_mean:
                        restoration = min(1.0 + 0.10 * (r_std / r_mean), 1.30)
                        return r_mean * restoration
                return r_mean
            consensus[param] = p_df.apply(apply_rain_consensus, axis=1)
        elif param == "Visibility":
            consensus[param] = p_df.apply(lambda row: _aviation_visibility_consensus(row, weights), axis=1)
        elif param == "Weather Code":
            consensus[param] = p_df.apply(lambda row: _weighted_weather_code_consensus(row, weights), axis=1)
        elif param in {"Low Clouds", "Mid Clouds", "High Clouds"}:
            consensus[param] = p_df.apply(lambda row: _aviation_cloud_cover_consensus(row, weights), axis=1)
        else:
            consensus[param] = p_df.apply(
                lambda row: np.average(row.dropna(), weights=row_weights(row.dropna().index))
                if not row.dropna().empty else np.nan,
                axis=1
            )
    db.conn.commit()

    if {"Temperature", "Dewpoint"}.issubset(consensus.columns):
        both_valid = consensus["Temperature"].notna() & consensus["Dewpoint"].notna()
        consensus.loc[both_valid, "Dewpoint"] = consensus.loc[both_valid, ["Temperature", "Dewpoint"]].min(axis=1)

    if qm_provenance["total_values"] > 0:
        qm_provenance["percent_by_layer"] = {
            k: round(100.0 * v / qm_provenance["total_values"], 2)
            for k, v in qm_provenance["by_layer"].items()
        }
    else:
        qm_provenance["percent_by_layer"] = {}
    db_health["qm_provenance"] = qm_provenance
    with open(os.path.join(output_dir, "db_health.json"), "w") as f:
        json.dump(db_health, f, indent=2)

    consensus = consensus.reset_index()
    consensus = consensus.rename(columns={"Wind Speed": "Wind", "Rainfall": "Rain"})
    consensus["Thunderstorm Risk"] = consensus.apply(_compute_ts_risk, axis=1)
    ts_proxy_metadata = _ts_proxy_metadata(consensus)
    
    # Dynamically Determine Condition
    def get_condition(rain):
        if pd.isna(rain) or rain < 0.1:
            return "Normal"
        elif rain >= 10.0:
            return "Heavy Rain"
        elif rain >= 1.0:
            return "Rain"
        else:
            return "Light Rain"
    consensus["Condition"] = consensus["Rain"].apply(get_condition)
    

    
    # Generate TAF Intel
    qm_rain_data = {m: qm_mapper.transform_series(pd.DataFrame(model_data["Rainfall"])[m], model=m).to_dict() for m in model_data["Rainfall"].keys()} if "Rainfall" in model_data else {}
    
    fresh_models = {
        row["model"] for row in workflow.get("model_freshness", [])
        if row.get("status") == "fresh"
    }
    taf_reference_utc = _safe_dt(latest_time) or export_now
    default_selection = _select_default_taf_window(df_fcst, fresh_models, taf_reference_utc)

    taf_intel = {}
    for iss in ["2300", "0500", "1100", "1700"]:
        taf = generate_tafor(
            consensus,
            model_data,
            qm_rain_data,
            global_weights,
            target_issuance=iss,
            event_weight_diagnostics=event_weight_diagnostics,
        )
        if taf:
            provenance = _taf_provenance(
                df_fcst, fresh_models, workflow.get("model_freshness", []),
                taf.get("valid_start"), observation_freshness,
            )
            taf["provenance"] = provenance
            taf.setdefault("warnings", [])
            if provenance["coverage_status"] != "sufficient":
                taf["warnings"].append(
                    f"Degraded model coverage: {provenance['fresh_model_count']} fresh models cover the first "
                    f"{provenance['coverage_hours']} hours; use this issuance cautiously."
                )
            if verification_frozen:
                taf["warnings"].append(
                    "Hourly AWOS verification is stale; verification-driven weights and residual promotion are frozen."
                )
        taf_intel[iss] = taf

    selected_issuance = default_selection.get("selected_issuance")
    if selected_issuance and taf_intel.get(selected_issuance):
        selected = taf_intel[selected_issuance]
        taf_intel["default"] = {
            **selected,
            "warnings": list(selected.get("warnings") or []),
            "default_selection": default_selection,
        }
        taf_intel["default"]["warnings"].append(
            f"Default guidance selected from the next official {selected_issuance}Z issuance with fresh-model quorum."
        )
    else:
        taf_intel["default"] = {
            "status": "suppressed",
            "taf_text": None,
            "warnings": [default_selection["reason"]],
            "narration": "Default TAF is withheld until an official issuance window has fresh-model quorum coverage.",
            "default_selection": default_selection,
            "provenance": {"coverage_status": "suppressed"},
        }
    
    # Output Payloads
    out_path = os.path.join(output_dir, "tafor_intel.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(sanitize_for_json(taf_intel), f, indent=2, default=str, allow_nan=False)
        
    # Output taf_guidance.json (Consensus data)
    guidance_data = consensus.copy()
    guidance_data['Datetime'] = guidance_data['Datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
    guidance_payload = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "latest_data_pull_utc": latest_time,
            "latest_model_run_init_utc": latest_model_run_init,
            "forecast_run_label_utc": latest_time,
            "version": "v200",
            "location": "Bandara_Sangia_Ni_Bandera",
            "qm_provenance": qm_provenance,
            "ts_proxy": ts_proxy_metadata,
        },
        "data": guidance_data.to_dict(orient="records")
    }
    with open(os.path.join(output_dir, "taf_guidance.json"), 'w', encoding='utf-8') as f:
        json.dump(sanitize_for_json(guidance_payload), f, indent=2, default=str, allow_nan=False)
        
    # Output individual_models.json
    model_data_str = {}
    for prm, m_dict in model_data.items():
        model_data_str[prm] = {}
        for m_name, series in m_dict.items():
            s = series.copy()
            s.index = s.index.strftime('%Y-%m-%d %H:%M:%S')
            s_dict = s.to_dict()
            # Safely cast NaNs to None for valid JSON serialization
            s_dict = {k: (None if pd.isna(v) else v) for k, v in s_dict.items()}
            model_data_str[prm][m_name] = s_dict
            
    # Extract Run_Init_UTC — use the most recent init per model
    run_init = {}
    data_pull = {}
    if 'run_init_utc' in df_fcst.columns:
        for m in active_models:
            run_init[m] = run_init_by_model.get(m) or "Unknown"
            data_pull[m] = scraped_at_by_model.get(m) or "Unknown"
    model_data_str["Run_Init"] = run_init
    model_data_str["Data_Pull"] = data_pull
    model_data_str["Model_Metadata"] = {
        m: {
            **model_metadata_dict(m),
            **{
                "latest_run_init_utc": run_init.get(m),
                "latest_data_pull_utc": data_pull.get(m),
            },
        }
        for m in active_models
    }
            
    with open(os.path.join(output_dir, "individual_models.json"), 'w', encoding='utf-8') as f:
        json.dump(model_data_str, f, indent=2, default=str, allow_nan=False)
        
    weights_path = os.path.join(output_dir, "latest_weights.json")
    weights_payload = {
        "metadata": {
            "updated_at": latest_time,
            "event_weight_diagnostics": sanitize_for_json(event_weight_diagnostics),
        },
        "weights": global_weights,
    }
    existing_weights = _load_json_file(weights_path)
    if not _weights_have_signal(global_weights) and _weights_have_signal((existing_weights or {}).get("weights", {})):
        log.warning("Preserving existing latest_weights.json because current verification produced equal fallback weights")
        weights_payload = existing_weights
    with open(weights_path, 'w', encoding='utf-8') as f:
        json.dump(weights_payload, f, indent=2)
        
    perf_path = os.path.join(output_dir, "latest_performance.json")
    perf_payload = {"metadata": {"period": "Lookback"}, "metrics": sanitize_for_json(global_metrics)}
    existing_perf = _load_json_file(perf_path)
    if not _metrics_have_skill(global_metrics) and _metrics_have_skill((existing_perf or {}).get("metrics", {})):
        log.warning("Preserving existing latest_performance.json because current export has no forecast-observation skill pairs")
        perf_payload = existing_perf
    with open(perf_path, 'w', encoding='utf-8') as f:
        # global_metrics is now nested: { "24 Hours": { "Temperature": { "overall": {}, ... } }, ... }
        json.dump(perf_payload, f, indent=2)
        
    # Append to Weight History
    history_path = os.path.join(output_dir, "weight_history.jsonl")
    snapshot = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "station": "Bandara_Sangia_Ni_Bandera",
        "method": "CRPS",
        "weights": weights_payload.get("weights", global_weights),
        "event_weight_diagnostics": weights_payload.get("metadata", {}).get("event_weight_diagnostics", {})
    }
    with open(history_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(snapshot) + "\n")

    # 4. Copy/Generate Climatology Data
    try:
        from src.diurnal_analysis import generate_diurnal_analysis
        diurnal_path = os.path.join(output_dir, "diurnal_climatology.json")
        existing_diurnal_obs = _diurnal_total_observations(diurnal_path)
        if obs_count > 0 and obs_count < 10000 and existing_diurnal_obs > obs_count:
            log.warning(
                "Preserving existing diurnal_climatology.json because current DB has only "
                f"{obs_count} observations versus existing {existing_diurnal_obs}"
            )
        elif obs_count > 0:
            generate_diurnal_analysis(db.conn.execute("PRAGMA database_list").fetchall()[0][2], output_dir)
    except Exception as e:
        log.warning(f"Diurnal analysis export failed: {e}")

    try:
        clim_src = os.path.join(output_dir, "wawp_climatology.json")
        clim_dst = os.path.join(output_dir, 'climatology.json')
        if os.path.exists(clim_src):
            shutil.copy(clim_src, clim_dst)
    except Exception as e:
        log.error(f"Failed to copy climatology: {e}")

    # 5. Export Persistency (Last 5 Days of AWOS)
    try:
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        obs_df = pd.read_sql("SELECT * FROM awos_observations WHERE obs_time >= ? ORDER BY obs_time ASC", db.conn, params=(five_days_ago,))
        if not obs_df.empty:
            obs_df['Datetime'] = pd.to_datetime(obs_df['obs_time']).dt.strftime('%Y-%m-%d %H:00:00')
            persistency_payload = obs_df[['Datetime', 'temperature', 'dewpoint', 'pressure', 'wind_dir', 'wind_speed', 'rain_1h']].to_dict(orient='records')
            with open(os.path.join(output_dir, "persistency.json"), "w") as f:
                json.dump(persistency_payload, f, indent=2)
            log.info("Persistency data exported.")
    except Exception as e:
        log.warning(f"Persistency export failed: {e}")

    log.info("Dashboard exports complete.")
