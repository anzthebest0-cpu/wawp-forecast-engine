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
from src.quantile_mapper import QuantileMapper
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
    forecast_count = db.conn.execute("SELECT COUNT(*) FROM meteologix_forecasts").fetchone()[0]
    obs_count = db.conn.execute("SELECT COUNT(*) FROM awos_observations").fetchone()[0]
    db_health = {
        "size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "forecast_records": forecast_count,
        "observation_records": obs_count,
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
                w = {m: 1.0/len(MODELS) for m in MODELS}
                
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
    legacy_guidance_path = r"D:\UJI_PERFORMA_MODEL\New_CODE\taf_guidance.json"
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
    
    query_latest = "SELECT MAX(scraped_at) FROM meteologix_forecasts"
    latest_time_df = pd.read_sql_query(query_latest, db.conn)
    latest_time = latest_time_df.iloc[0, 0]
    
    if not latest_time:
        log.warning("No forecasts found in DB.")
        return
        
    log.info(f"Generating TAF guidance for latest scrape: {latest_time}")
    
    # Get the latest scrape for EACH model to handle partial failures
    query_fcst = """
        SELECT m.*
        FROM meteologix_forecasts m
        INNER JOIN (
            SELECT model, MAX(scraped_at) as max_scraped
            FROM meteologix_forecasts
            GROUP BY model
        ) latest
        ON m.model = latest.model AND m.scraped_at = latest.max_scraped
    """
    df_fcst = pd.read_sql_query(query_fcst, db.conn)
    
    model_data = {param: {} for param in ["Temperature", "Dewpoint", "Pressure", "Rainfall", "Wind Speed", "Wind Dir.", "Wind Gust", "Prob Precip 0.1mm", "Prob Precip 1.0mm", "Prob Precip 10.0mm", "Sunshine", "Low Clouds", "Mid Clouds", "High Clouds", "Condition"]}
    
    param_map = {
        "Temperature": "temperature",
        "Dewpoint": "dewpoint",
        "Pressure": "pressure",
        "Rainfall": "rain",
        "Wind Speed": "wind_speed",
        "Wind Dir.": "wind_dir",
        "Wind Gust": "wind_gust",
        "Prob Precip 0.1mm": "prob_precip_01",
        "Prob Precip 1.0mm": "prob_precip_10",
        "Prob Precip 10.0mm": "prob_precip_100",
        "Sunshine": "sunshine",
        "Low Clouds": "low_clouds",
        "Mid Clouds": "mid_clouds",
        "High Clouds": "high_clouds",
        "Condition": "condition"
    }
    
    for m in MODELS:
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
    
    for param in model_data.keys():
        if param == "Condition":
            continue
            
        weights = global_weights.get(param, {m: 1.0/len(MODELS) for m in MODELS})
        p_df = pd.DataFrame(model_data[param])
        
        # Quantile Map Rainfall before consensus
        if param == "Rainfall":
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
                
        if param == "Wind Dir.":
            consensus[param] = p_df.apply(lambda row: circular_weighted_mean(row.dropna().values, [weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
        else:
            consensus[param] = p_df.apply(lambda row: np.average(row.dropna(), weights=[weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
            
    consensus = consensus.reset_index()
    consensus = consensus.rename(columns={"Wind Speed": "Wind", "Rainfall": "Rain"})
    
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
        json.dump(taf_intel, f, indent=2, default=str)
        
    # Output taf_guidance.json (Consensus data)
    guidance_data = consensus.copy()
    guidance_data['Datetime'] = guidance_data['Datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
    guidance_payload = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "v200",
            "location": "Bandara_Sangia_Ni_Bandera"
        },
        "data": guidance_data.to_dict(orient="records")
    }
    with open(os.path.join(output_dir, "taf_guidance.json"), 'w', encoding='utf-8') as f:
        json.dump(guidance_payload, f, indent=2, default=str, allow_nan=False)
        
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
        for m in MODELS:
            m_df = df_fcst[df_fcst["model"] == m].dropna(subset=['run_init_utc'])
            if not m_df.empty:
                # Take the latest (MAX) init time, not just the first row
                val = m_df['run_init_utc'].max()
                run_init[m] = str(val) if val else "Unknown"
            else:
                run_init[m] = "Unknown"
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

    # 4. Copy Climatology Data
    try:
        clim_src = r'D:\UJI_PERFORMA_MODEL\New_CODE\wawp_climatology.json'
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
            persistency_payload = obs_df[['Datetime', 'temperature', 'dewpoint', 'wind_dir', 'wind_speed', 'rain_1h']].to_dict(orient='records')
            with open(os.path.join(output_dir, "persistency.json"), "w") as f:
                json.dump(persistency_payload, f, indent=2)
            log.info("Persistency data exported.")
    except Exception as e:
        log.warning(f"Persistency export failed: {e}")

    log.info("Dashboard exports complete.")
