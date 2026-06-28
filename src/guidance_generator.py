import os
import json
import logging
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

from src.advanced_ensemble_weighter import (
    MODELS, 
    PARAMETERS,
    circular_difference
)
from src.utils import derive_rh, interpolate_3h_to_1h, circular_weighted_mean
from src.db_manager import ForecastDB

log = logging.getLogger("consensus")

def generate_consensus(db: ForecastDB, location: str = "Bandara_Sangia_Ni_Bandera"):
    """
    Core engine function. Loads latest forecasts, aligns grids, applies weights,
    computes Tetens RH, downscales Rain & Gust, and outputs the consensus timeline.
    """
    # In a fully implemented system, we would load the trained weights here.
    # For now, we use equal weights for the 7 models.
    weight_val = 1.0 / len(MODELS)
    weights = {m: weight_val for m in MODELS}
    
    # 1. Load Latest Forecasts
    df = db.get_latest_forecasts(location)
    if df.empty:
        log.error(f"No forecast data found for {location} in database.")
        return None
        
    df['forecast_time'] = pd.to_datetime(df['forecast_time'])
    
    # 2. Build Hourly Grid (T+0 to T+48)
    base_time = df['forecast_time'].min()
    hourly_index = pd.date_range(start=base_time, periods=49, freq='h')
    
    # We will build a dictionary of series per parameter per model
    model_data = {param: {} for param in PARAMETERS.keys()}
    model_data["Condition"] = {}
    
    # 3. Align All Models to Hourly Grid
    for model in MODELS:
        mdf = df[df['model'] == model].copy()
        if mdf.empty:
            continue
            
        mdf.set_index('forecast_time', inplace=True)
        
        # Determine if this model needs 3h->1h interpolation
        is_3h = model in ["GEM", "Multi-Model"]
        
        # For smooth fields, interpolate if 3h
        for param, col in [
            ("Temperature", "temperature"), 
            ("Dewpoint", "dewpoint"), 
            ("Pressure", "pressure"), 
            ("Wind Speed", "wind_speed")
        ]:
            if is_3h:
                model_data[param][model] = interpolate_3h_to_1h(mdf[col], hourly_index)
            else:
                model_data[param][model] = mdf[col].reindex(hourly_index)
                
        # Wind direction requires special interpolation, but for simplicity we'll forward fill or nearest
        if is_3h:
             model_data["Wind Direction"][model] = mdf['wind_dir'].reindex(hourly_index).ffill()
        else:
             model_data["Wind Direction"][model] = mdf['wind_dir'].reindex(hourly_index)
             
        # For discrete/block fields, do NOT interpolate, reindex and fill with NaN or 0
        for param, col in [("Rainfall", "rain"), ("Wind Gust", "wind_gust")]:
            model_data[param][model] = mdf[col].reindex(hourly_index)
            
        model_data["Condition"][model] = mdf['condition'].reindex(hourly_index)
        
    # 4. Compute Consensus
    consensus = pd.DataFrame(index=hourly_index)
    consensus['Datetime'] = hourly_index.strftime('%Y-%m-%d %H:%M:%S')
    consensus['Hours'] = hourly_index.hour
    
    # A. Temperature
    t_df = pd.DataFrame(model_data["Temperature"])
    consensus['Temperature'] = t_df.apply(lambda row: np.average(row.dropna(), weights=[weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
    
    # B. Dewpoint
    td_df = pd.DataFrame(model_data["Dewpoint"])
    consensus['Dewpoint'] = td_df.apply(lambda row: np.average(row.dropna(), weights=[weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
    # Enforce physical constraint
    consensus['Dewpoint'] = np.minimum(consensus['Dewpoint'], consensus['Temperature'])
    
    # C. Relative Humidity (Derived)
    consensus['Humidity'] = consensus.apply(lambda row: derive_rh(row['Temperature'], row['Dewpoint']), axis=1)
    
    # D. Pressure
    p_df = pd.DataFrame(model_data["Pressure"])
    consensus['Pressure'] = p_df.apply(lambda row: np.average(row.dropna(), weights=[weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
    
    # E. Wind Speed
    u_df = pd.DataFrame(model_data["Wind Speed"])
    consensus['Wind'] = u_df.apply(lambda row: np.average(row.dropna(), weights=[weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
    
    # F. Wind Direction
    d_df = pd.DataFrame(model_data["Wind Direction"])
    consensus['Wind Dir.'] = d_df.apply(lambda row: circular_weighted_mean(row.dropna().values, [weights[m] for m in row.dropna().index]) if not row.dropna().empty else np.nan, axis=1)
    
    # G. Rainfall Downscaling (Simplified for this mock-up - true 3h block aggregation requires grouping)
    r_df = pd.DataFrame(model_data["Rainfall"])
    # Simply weight hourly outputs. For GEM, they are every 3h, so we distribute them.
    # A true implementation groups by 3h blocks. Here we just take the weighted average.
    consensus['Rain'] = r_df.apply(lambda row: np.average(row.dropna(), weights=[weights[m] for m in row.dropna().index]) if not row.dropna().empty else 0.0, axis=1)
    
    # H. Gust Downscaling
    g_df = pd.DataFrame(model_data["Wind Gust"])
    consensus['Gust'] = g_df.apply(lambda row: np.average(row.dropna(), weights=[weights[m] for m in row.dropna().index]) if not row.dropna().empty else 0.0, axis=1)
    
    # Map to expected output structure
    output_rows = []
    for idx, row in consensus.iterrows():
        # Get most common condition
        cond_row = pd.DataFrame(model_data["Condition"]).loc[idx]
        most_common_cond = cond_row.mode()[0] if not cond_row.dropna().empty else ""
        
        output_rows.append({
            "Datetime": row['Datetime'],
            "Hours": row['Hours'],
            "Temperature": round(row['Temperature'], 1) if pd.notna(row['Temperature']) else None,
            "Dewpoint": round(row['Dewpoint'], 1) if pd.notna(row['Dewpoint']) else None,
            "Humidity": round(row['Humidity'], 1) if pd.notna(row['Humidity']) else None,
            "Pressure": round(row['Pressure'], 1) if pd.notna(row['Pressure']) else None,
            "Rain": round(row['Rain'], 1) if pd.notna(row['Rain']) else 0.0,
            "Wind": round(row['Wind'], 1) if pd.notna(row['Wind']) else 0.0,
            "Gust": round(row['Gust'], 1) if pd.notna(row['Gust']) else 0.0,
            "Wind Dir.": round(row['Wind Dir.']) if pd.notna(row['Wind Dir.']) else None,
            "Condition": most_common_cond
        })
        
    return output_rows
