import os
import json
import logging
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
            
    # 2. Extract Forecast Data & Generate Consensus
    # Wait, guidance_generator expects model_data in memory. 
    # run_pipeline currently passes nothing to export_all! We need to fetch the latest forecast.
    
    query_latest = "SELECT MAX(forecast_time) FROM meteologix_forecasts"
    latest_time_df = pd.read_sql_query(query_latest, db.conn)
    latest_time = latest_time_df.iloc[0, 0]
    
    if not latest_time:
        log.warning("No forecasts found in DB.")
        return
        
    log.info(f"Generating TAF guidance for forecast cycle: {latest_time}")
    
    query_fcst = "SELECT * FROM meteologix_forecasts WHERE forecast_time >= ?"
    df_fcst = pd.read_sql_query(query_fcst, db.conn, params=(latest_time,))
    
    model_data = {param: {} for param in ["Temperature", "Dewpoint", "Pressure", "Rainfall", "Wind Speed", "Wind Dir.", "Wind Gust"]}
    
    param_map = {
        "Temperature": "temperature",
        "Dewpoint": "dewpoint",
        "Pressure": "pressure",
        "Rainfall": "rain",
        "Wind Speed": "wind_speed",
        "Wind Dir.": "wind_dir",
        "Wind Gust": "wind_gust"
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
        weights = global_weights.get(param, {m: 1.0/len(MODELS) for m in MODELS})
        p_df = pd.DataFrame(model_data[param])
        
        # Quantile Map Rainfall before consensus
        if param == "Rainfall":
            for m in p_df.columns:
                p_df[m] = qm_mapper.transform_series(p_df[m], model=m)
                
        if param == "Wind Dir.":
            consensus[param] = p_df.apply(lambda row: circular_weighted_mean(row.dropna().values, [weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
        else:
            consensus[param] = p_df.apply(lambda row: np.average(row.dropna(), weights=[weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
            
    consensus = consensus.reset_index()
    consensus = consensus.rename(columns={"Wind Speed": "Wind", "Rainfall": "Rain"})
    consensus["Condition"] = "Normal" # Mock condition
    
    # Generate TAF Intel
    qm_rain_data = {m: qm_mapper.transform_series(pd.DataFrame(model_data["Rainfall"])[m], model=m).to_dict() for m in model_data["Rainfall"].keys()} if "Rainfall" in model_data else {}
    
    taf_intel = generate_tafor(consensus, model_data, qm_rain_data, global_weights)
    
    # Output Payloads
    out_path = os.path.join(output_dir, "tafor_intel.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(taf_intel, f, indent=2, default=str)
        
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
        
    log.info("Dashboard exports complete.")
