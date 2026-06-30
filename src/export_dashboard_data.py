import os
import json
import logging
import shutil
from datetime import datetime, timezone, timedelta
import pandas as pd

from src.db_manager import ForecastDB
from src.advanced_ensemble_weighter import AdvancedEnsembleWeighter, MODELS, PARAMETERS
from src.guidance_generator import generate_consensus
from src.quantile_mapper import QuantileMapper
from src.tafor_generator import generate_tafor

log = logging.getLogger("exporter")

def _wide_to_long(df_wide: pd.DataFrame, param: str) -> pd.DataFrame:
    """Convert wide DB output to long format for weighter."""
    obs_col = f"OBS_{param.replace(' ', '_')}"
    if df_wide.empty or obs_col not in df_wide.columns:
        return pd.DataFrame()
        
    long_rows = []
    for m in MODELS:
        if m in df_wide.columns:
            subset = df_wide[["Datetime", m, obs_col]].copy()
            subset = subset.rename(columns={m: "forecast", obs_col: "obs"})
            subset["Model"] = m
            subset = subset.dropna(subset=["forecast", "obs"])
            long_rows.append(subset)
            
    if not long_rows:
        return pd.DataFrame()
    return pd.concat(long_rows, ignore_index=True)

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
    
    # Use last 60 days of overlap
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=60)
    start_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
    end_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
    
    global_weights = {}
    global_metrics = {}
    qm_mapper = QuantileMapper()
    
    for param in ["Temperature", "Dewpoint", "Pressure", "Rainfall", "Wind Speed", "Wind Dir.", "Wind Gust"]:
        df_wide = db.get_verification_pairs(param, start_str, end_str)
        df_long = _wide_to_long(df_wide, param)
        
        is_circular = (param == "Wind Dir.")
        
        if not df_long.empty:
            metrics = weighter.calculate_all_metrics(
                df_long, parameter=param, is_circular=is_circular
            )
            # Just take equal weights if data too sparse, else CRPS weights
            try:
                w = weighter.calculate_weights_crps(df_long, parameter=param, is_circular=is_circular)
            except Exception as e:
                log.warning(f"CRPS weights failed for {param}: {e}")
                w = {m: 1.0/len(MODELS) for m in MODELS}
                
            global_weights[param] = w
            
            # Serialize metrics
            param_metrics = {}
            for m, sk in metrics.items():
                if sk.rmse < 900:
                    param_metrics[m] = {
                        "RMSE": round(sk.rmse, 3),
                        "Bias": round(sk.bias, 3),
                        "MAE": round(sk.mae, 3),
                        "CRPS": round(sk.crps, 3) if sk.crps is not None else None
                    }
                else:
                    param_metrics[m] = {"RMSE": None, "Bias": None, "MAE": None, "CRPS": None}
            global_metrics[param] = param_metrics
            
            # If Rainfall, update Quantile Mapper
            if param == "Rainfall":
                try:
                    for m in MODELS:
                        if m in df_wide.columns:
                            m_df = df_wide[[m, f"OBS_Rainfall"]].dropna()
                            m_df = m_df.rename(columns={m: "Rain", f"OBS_Rainfall": "OBS_Rain"})
                            qm_mapper.fit_model(m_df, model=m)
                    qm_mapper.save()
                except Exception as e:
                    log.warning(f"QM fit failed: {e}")
        else:
            global_weights[param] = {m: 1.0/len(MODELS) for m in MODELS}
            global_metrics[param] = {}
            
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
            
    # Extract Run_Init_UTC
    run_init = {}
    if 'run_init_utc' in df_fcst.columns:
        for m in MODELS:
            m_df = df_fcst[df_fcst["model"] == m].dropna(subset=['run_init_utc'])
            if not m_df.empty:
                # Convert UTC string to Date string, or keep it as is if it's already formatted
                val = m_df['run_init_utc'].iloc[0]
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
        json.dump({"metadata": {"period": "Last 60 Days"}, "metrics": global_metrics}, f, indent=2)
        
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

    log.info("Dashboard exports complete.")
