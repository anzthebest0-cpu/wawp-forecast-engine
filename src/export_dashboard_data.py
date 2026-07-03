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
from src.quantile_mapper import QuantileMapper, apply_qm_with_layers
from src.tafor_generator import generate_tafor

log = logging.getLogger("exporter")

def sanitize_for_json(obj):
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj

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


def _compute_ts_risk(row: pd.Series) -> float:
    """Dashboard TS proxy; TAF phenomenon selection still uses taf_core/vis_cloud_proxy."""
    def num(name, default=0.0):
        value = row.get(name, default)
        try:
            if pd.isna(value):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    prob = max(0.0, min(100.0, num("Precip Probability", 0.0)))
    rain = max(0.0, num("Rain", 0.0))
    cape = num("CAPE", 0.0)
    li = row.get("Lifted Index")
    cin = row.get("Convective Inhibition")
    weather_code = row.get("Weather Code")
    hour = row.get("Datetime").hour if hasattr(row.get("Datetime"), "hour") else 0

    score = prob * 0.35
    if rain >= 0.1:
        score += 8.0
    if rain >= 1.0:
        score += 10.0
    if rain >= 5.0:
        score += 15.0
    if cape >= 500:
        score += 12.0
    if cape >= 1000:
        score += 8.0
    try:
        li_val = float(li)
        if li_val <= -2:
            score += 10.0
        if li_val <= -4:
            score += 5.0
    except (TypeError, ValueError):
        pass
    try:
        cin_val = float(cin)
        if cin_val <= 100:
            score += 8.0
        if cin_val <= 50:
            score += 4.0
        if cin_val > 200:
            score -= 10.0
    except (TypeError, ValueError):
        pass
    if 12 <= int(hour) <= 19:
        score += 10.0
    elif 10 <= int(hour) <= 21:
        score += 5.0
    try:
        if int(weather_code) in {95, 96, 99}:
            score = max(score, 85.0)
    except (TypeError, ValueError):
        pass
    return float(max(0.0, min(100.0, score)))

def calculate_advanced_metrics(weighter, df_long: pd.DataFrame, param: str, is_circular: bool):
    """Calculate standard metrics, lead-time metrics, and diurnal bias."""
    metrics_payload = {
        "overall": {},
        "lead_time": {},
        "diurnal_bias": {},
        "significance": {}
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

    return metrics_payload


def _safe_dt(value):
    if value is None or pd.isna(value):
        return None
    dt = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(dt):
        return None
    return dt.to_pydatetime()


def _model_placeholders() -> str:
    return ",".join("?" for _ in MODELS)


def _model_params(multiplier: int = 1) -> tuple[str, ...]:
    return tuple(MODELS) * multiplier


def export_system_workflow(db: ForecastDB, output_dir: str, db_health: dict, latest_time: str | None = None) -> dict:
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

    freshness = []
    for _, row in current_rows.iterrows():
        scraped_dt = _safe_dt(row["latest_scraped_at"])
        age_hours = None
        if scraped_dt:
            age_hours = max(0.0, (now - scraped_dt).total_seconds() / 3600.0)
        status = "missing"
        if age_hours is not None:
            if age_hours <= 6:
                status = "fresh"
            elif age_hours <= 12:
                status = "aging"
            else:
                status = "stale"
        freshness.append({
            "model": row["model"],
            "row_count": int(row["row_count"]),
            "latest_scraped_at": row["latest_scraped_at"],
            "latest_run_init_utc": row["latest_run_init_utc"],
            "first_forecast_time": row["first_forecast_time"],
            "last_forecast_time": row["last_forecast_time"],
            "min_lead_hours": None if pd.isna(row["min_lead_hours"]) else float(row["min_lead_hours"]),
            "max_lead_hours": None if pd.isna(row["max_lead_hours"]) else float(row["max_lead_hours"]),
            "age_hours": None if age_hours is None else round(age_hours, 2),
            "status": status,
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
    return workflow

def export_all(db: ForecastDB, output_dir: str):
    """
    Exports JSON payloads for the HTML dashboard:
    1. tafor_intel.json (Consensus timeline + TAF)
    2. latest_weights.json (Current model weights)
    3. latest_performance.json (Metrics)
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 0. Database Health Checker
    db_size_bytes = os.path.getsize(db.conn.execute("PRAGMA database_list").fetchall()[0][2]) if os.path.exists('wawp_forecasts.db') else 0
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
    qm_cdf_count = db.conn.execute(
        f"SELECT COUNT(*) FROM qm_cdfs WHERE enabled=1 AND COALESCE(deprecated,0)=0 AND model IN ({model_filter})",
        _model_params()
    ).fetchone()[0]
    latest_model_run_init = db.conn.execute(
        f"SELECT MAX(run_init_utc) FROM openmeteo_forecasts WHERE run_init_utc <> 'historical_forecast_api' AND lead_hours >= 0 AND model IN ({model_filter})",
        _model_params()
    ).fetchone()[0]
    db_health = {
        "size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "forecast_records": forecast_count,
        "observation_records": obs_count,
        "observation_1min_records": obs_1min_count,
        "openmeteo_records": openmeteo_count,
        "qm_cdfs_enabled": qm_cdf_count,
        "latest_model_run_init_utc": latest_model_run_init,
        "last_sync_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(os.path.join(output_dir, "db_health.json"), "w") as f:
        json.dump(db_health, f, indent=2)
        
    # 1. Calculate Weights & Metrics
    log.info("Calculating dynamic weights from recent observations...")
    weighter = AdvancedEnsembleWeighter()    
    global_weights = {}
    global_metrics = {
        "24 Hours": {}, "3 Days": {}, "7 Days": {}, "Month": {}
    }
    
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
        df_long_60d = db.get_verification_pairs(param, start_str, end_str)
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
            
    # 2. Extract Forecast Data & Generate Consensus
    # Wait, guidance_generator expects model_data in memory. 
    # run_pipeline currently passes nothing to export_all! We need to fetch the latest forecast.
    
    query_latest = f"SELECT MAX(scraped_at) FROM openmeteo_forecasts WHERE run_init_utc <> 'historical_forecast_api' AND lead_hours >= 0 AND model IN ({model_filter})"
    latest_time_df = pd.read_sql_query(query_latest, db.conn, params=_model_params())
    latest_time = latest_time_df.iloc[0, 0]
    
    if not latest_time:
        log.warning("No forecasts found in DB.")
        workflow = export_system_workflow(db, output_dir, db_health, latest_time=None)
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
    workflow = export_system_workflow(db, output_dir, db_health, latest_time=latest_time)
    db_health["model_freshness"] = workflow.get("model_freshness", [])
    with open(os.path.join(output_dir, "db_health.json"), "w") as f:
        json.dump(db_health, f, indent=2)
    active_models = sorted(df_fcst["model"].dropna().unique().tolist())
    run_init_by_model = {}
    if 'run_init_utc' in df_fcst.columns:
        for m in active_models:
            m_df = df_fcst[df_fcst["model"] == m].dropna(subset=['run_init_utc'])
            run_init_by_model[m] = str(m_df['run_init_utc'].max()) if not m_df.empty else None
    record_id_by_model_time = {}
    if "id" in df_fcst.columns:
        for _, row in df_fcst.iterrows():
            record_id_by_model_time[(row["model"], str(row["forecast_time"]))] = int(row["id"])
    
    model_data = {param: {} for param in ["Temperature", "Dewpoint", "Pressure", "Rainfall", "Wind Speed", "Wind Dir.", "Wind Gust", "Precip Probability", "Sunshine", "Low Clouds", "Mid Clouds", "High Clouds", "Condition", "CAPE", "Lifted Index", "Convective Inhibition", "Weather Code"]}
    
    param_map = {
        "Temperature": "temperature",
        "Dewpoint": "dewpoint",
        "Pressure": "pressure_msl",
        "Rainfall": "rain",
        "Wind Speed": "wind_speed",
        "Wind Dir.": "wind_dir",
        "Wind Gust": "wind_gust",
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
    qm_provenance = {
        "total_values": 0,
        "by_layer": {"historical_prior": 0, "operational_residual": 0, "raw": 0},
        "by_parameter": {},
        "low_confidence": 0,
        "lead_aware_pending": 0,
    }
    
    for param in model_data.keys():
        if param == "Condition":
            continue
            
        weights = global_weights.get(param, {m: 1.0/len(MODELS) for m in MODELS})
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
    
    taf_intel = {}
    for iss in ["2300", "0500", "1100", "1700"]:
        taf_intel[iss] = generate_tafor(consensus, model_data, qm_rain_data, global_weights, target_issuance=iss)
    
    taf_intel["default"] = generate_tafor(consensus, model_data, qm_rain_data, global_weights)
    
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
            "forecast_run_label_utc": latest_model_run_init,
            "version": "v200",
            "location": "Bandara_Sangia_Ni_Bandera",
            "qm_provenance": qm_provenance
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
    if 'run_init_utc' in df_fcst.columns:
        for m in active_models:
            run_init[m] = run_init_by_model.get(m) or "Unknown"
    model_data_str["Run_Init"] = run_init
            
    with open(os.path.join(output_dir, "individual_models.json"), 'w', encoding='utf-8') as f:
        json.dump(model_data_str, f, indent=2, default=str, allow_nan=False)
        
    weights_path = os.path.join(output_dir, "latest_weights.json")
    with open(weights_path, 'w', encoding='utf-8') as f:
        json.dump({"metadata": {"updated_at": latest_time}, "weights": global_weights}, f, indent=2)
        
    perf_path = os.path.join(output_dir, "latest_performance.json")
    with open(perf_path, 'w', encoding='utf-8') as f:
        # global_metrics is now nested: { "24 Hours": { "Temperature": { "overall": {}, ... } }, ... }
        json.dump({"metadata": {"period": "Lookback"}, "metrics": sanitize_for_json(global_metrics)}, f, indent=2)
        
    # Append to Weight History
    history_path = os.path.join(output_dir, "weight_history.jsonl")
    snapshot = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "station": "Bandara_Sangia_Ni_Bandera",
        "method": "CRPS",
        "weights": global_weights
    }
    with open(history_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(snapshot) + "\n")

    # 4. Copy/Generate Climatology Data
    try:
        from src.diurnal_analysis import generate_diurnal_analysis
        if obs_count > 0:
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
        latest_obs_time = db.conn.execute("SELECT MAX(obs_time) FROM awos_observations").fetchone()[0]
        anchor = pd.to_datetime(latest_obs_time) if latest_obs_time else datetime.now(timezone.utc)
        seven_days_ago = (anchor - pd.Timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        obs_df = pd.read_sql(
            "SELECT * FROM awos_observations WHERE obs_time >= ? ORDER BY obs_time ASC",
            db.conn,
            params=(seven_days_ago,),
        )
        if not obs_df.empty:
            obs_df['Datetime'] = pd.to_datetime(obs_df['obs_time']).dt.strftime('%Y-%m-%d %H:00:00')
            persistency_payload = obs_df[['Datetime', 'temperature', 'dewpoint', 'wind_dir', 'wind_speed', 'rain_1h']].to_dict(orient='records')
            with open(os.path.join(output_dir, "persistency.json"), "w") as f:
                json.dump(persistency_payload, f, indent=2)
            log.info("Persistency data exported.")
    except Exception as e:
        log.warning(f"Persistency export failed: {e}")

    log.info("Dashboard exports complete.")
