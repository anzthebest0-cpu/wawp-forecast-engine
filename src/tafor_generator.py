import os
import json
from datetime import datetime, timedelta
import pandas as pd
from src.taf_core import _build_change_groups
from src.vis_cloud_proxy import build_hourly_vis_cloud

def _build_taf_text(bg: dict, v_start, iss_day: int, iss_utc: str) -> str:
    issued_hh  = iss_utc[:2]
    issued_str = f"{iss_day:02d}{issued_hh}00Z"
    v_end      = v_start + timedelta(hours=24)
    valid_str  = (f"{v_start.day:02d}{v_start.hour:02d}/"
                  f"{v_end.day:02d}{v_end.hour:02d}")
    d = str(bg.get('dir', '000')).upper()
    s = str(bg.get('spd', '00')).zfill(2)
    wind = '00000KT' if s == '00' else f"{'VRB' if d=='VRB' else d.zfill(3)}{s}KT"
    vis   = bg.get('vis',   '9999') or '9999'
    wx    = (bg.get('wx',   '') or '').upper()
    cloud = (bg.get('cloud','SCT018') or 'SCT018').upper()
    
    parts = [f'TAF WAWP {issued_str}', valid_str, wind, vis]
    if wx: parts.append(wx)
    parts.append(cloud)
    
    body = ' '.join(filter(None, parts))
    
    for t in (bg.get('trends') or []):
        gt   = (t.get('type','')    or '').upper()
        ts   = (t.get('time_str','')or '').upper()
        tdir = (t.get('dir','')     or '').upper()
        tspd = str(t.get('spd','')  or '')
        tvis = (t.get('vis','')     or '')
        twx  = (t.get('wx','')      or '').upper()
        tcld = [c.upper() for c in (t.get('clouds',[]) or []) if c]
        
        wp   = (f"{'VRB' if tdir=='VRB' else tdir.zfill(3)}{tspd.zfill(2)}KT"
                if tdir and tspd else '')
        
        line = f'{gt} {ts}'.strip()
        extras = [x for x in [wp, tvis, twx] + tcld if x]
        if extras: line += ' ' + ' '.join(extras)
        body += '\n     ' + line
        
    header = f'FTID40 WAWP {issued_str}'
    raw    = header + '\n' + body + '='
    return '\n'.join(' '.join(l.split()).rstrip() for l in raw.splitlines())

def generate_tafor(consensus_df: pd.DataFrame, model_data: dict, qm_rain_data: dict, model_weights: dict, target_issuance: str = None) -> dict:
    """
    Core TAF generator. Takes the consensus DataFrame and raw/QM model data 
    and returns a structured intel dictionary with the final TAF text.
    If target_issuance is provided (e.g. "0500"), shifts the 24h valid window.
    """
    if consensus_df.empty:
        return {}
        
    full_truth = []
    pressure_history = []
    
    for _, row in consensus_df.iterrows():
        # Build base dictionary for proxy
        hour_data = {
            "temp_c": row["Temperature"] if pd.notna(row.get("Temperature")) else 28.0,
            "dewpoint_c": row["Dewpoint"] if pd.notna(row.get("Dewpoint")) else 24.0,
            "pressure_hpa": row["Pressure"] if pd.notna(row.get("Pressure")) else 1013.25,
            "relative_humidity_pct": row["Humidity"] if pd.notna(row.get("Humidity")) else 80.0,
            "rain": row["Rain"] if pd.notna(row.get("Rain")) else 0.0,
            "spd": row["Wind"] if pd.notna(row.get("Wind")) else 0.0,
            "Condition": row["Condition"] if pd.notna(row.get("Condition")) else "Clear"
        }
        
        pressure_history.append(hour_data["pressure_hpa"])
        vis_code, cloud_group = build_hourly_vis_cloud(hour_data, pressure_history)
        
        full_truth.append({
            "Rain": hour_data["rain"],
            "Wind": hour_data["spd"],
            "Wind Dir.": row["Wind Dir."],
            "Condition": hour_data["Condition"],
            "spd": hour_data["spd"],
            "dir_num": row["Wind Dir."],
            "rain": hour_data["rain"],
            "temp_c": hour_data["temp_c"],
            "dewpoint_c": hour_data["dewpoint_c"],
            "pressure_hpa": hour_data["pressure_hpa"],
            "vis": vis_code,
            "cloud": cloud_group,
            "Datetime": row["Datetime"]
        })
        
    # Map issuance_utc -> valid hour
    val_map = {"2300": 0, "0500": 6, "1100": 12, "1700": 18}
    
    start_idx = 0
    if target_issuance and target_issuance in val_map:
        target_hr = val_map[target_issuance]
        # Find first hour matching target_hr
        for i, row in enumerate(full_truth):
            dt = pd.to_datetime(row["Datetime"])
            if dt.hour == target_hr:
                start_idx = i
                break
                
    # Slice arrays to exactly 24 hours
    consensus_truth = full_truth[start_idx:start_idx+24]
    if not consensus_truth:
        return {}
        
    valid_start = pd.to_datetime(consensus_truth[0]["Datetime"])
    
    if target_issuance:
        iss_utc = target_issuance
        iss_utc_obj = valid_start - timedelta(hours=1)
        iss_day = iss_utc_obj.day
    else:
        iss_utc_obj = valid_start - timedelta(hours=1)
        iss_utc = f"{iss_utc_obj.hour:02d}00"
        iss_day = iss_utc_obj.day
    
    # Map model_data into meteo_data_local: {model: {param: series}}
    from src.advanced_ensemble_weighter import MODELS
    
    meteo_data_local = {m: {} for m in MODELS}
    for param, models_dict in model_data.items():
        for m, series in models_dict.items():
            # Slice series to match the window
            meteo_data_local[m][param] = series.iloc[start_idx:start_idx+24].reset_index(drop=True) if len(series) > start_idx else series
            
    # Also slice qm_rain_data
    qm_rain_local = {}
    for m, d_dict in qm_rain_data.items():
        # qm_rain_data is a dict of index -> value. Re-index starting from 0
        sliced_values = [d_dict[i] for i in sorted(d_dict.keys())][start_idx:start_idx+24]
        qm_rain_local[m] = {i: v for i, v in enumerate(sliced_values)}
            
    # Generate change groups
    trends, warnings = _build_change_groups(
        consensus_truth=consensus_truth,
        valid_start=valid_start,
        start_hour=0, # Relative to the sliced array
        meteo_data_local=meteo_data_local,
        corrected_rain_data=qm_rain_local,
        rain_timing_offset=0, 
        model_weights=model_weights
    )
    
    # Define Base Group
    first_row = consensus_truth[0]
    best_guess = {
        'dir': f"{int(first_row['Wind Dir.']):03d}" if pd.notna(first_row['Wind Dir.']) else "000",
        'spd': f"{int(first_row['Wind']):02d}" if pd.notna(first_row['Wind']) else "00",
        'vis': first_row['vis'],
        'wx': '',
        'cloud': first_row['cloud'],
        'trends': trends,
        'badge': f'MME {iss_utc}Z Shift',
        'metrics': {'leader_rmse': 'N/A', 'leader_strat': 'N/A'}
    }
    
    # Build actual TAF text
    taf_text = _build_taf_text(best_guess, valid_start, iss_day, iss_utc)
    
    return {
        "valid_start": valid_start.strftime("%Y-%m-%d %H:%M:%S"),
        "issued_utc": iss_utc,
        "issued_day": iss_day,
        "base_group": best_guess,
        "taf_text": taf_text,
        "warnings": warnings
    }
