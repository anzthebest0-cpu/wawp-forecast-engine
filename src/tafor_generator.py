import os
import json
from datetime import datetime, timedelta
import pandas as pd
from src.taf_core import _build_change_groups

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

def generate_tafor(consensus_df: pd.DataFrame, model_data: dict, qm_rain_data: dict, model_weights: dict) -> dict:
    """
    Core TAF generator. Takes the consensus DataFrame and raw/QM model data 
    and returns a structured intel dictionary with the final TAF text.
    """
    if consensus_df.empty:
        return {}
        
    consensus_truth = []
    for _, row in consensus_df.iterrows():
        consensus_truth.append({
            "Rain": row["Rain"],
            "Wind": row["Wind"],
            "Wind Dir.": row["Wind Dir."],
            "Condition": row["Condition"],
            "spd": row["Wind"],
            "dir_num": row["Wind Dir."],
            "rain": row["Rain"]
        })
        
    valid_start = pd.to_datetime(consensus_df.iloc[0]["Datetime"])
    
    # We assume generation time is closely tied to valid_start for simplicity
    iss_utc_obj = valid_start - timedelta(hours=1)
    iss_utc = f"{iss_utc_obj.hour:02d}00"
    iss_day = iss_utc_obj.day
    
    # Map model_data into meteo_data_local: {model: {param: series}}
    # In guidance_generator, model_data is {param: {model: series}}
    # taf_core expects {model: {param: series}}
    from src.advanced_ensemble_weighter import MODELS
    
    meteo_data_local = {m: {} for m in MODELS}
    for param, models_dict in model_data.items():
        for m, series in models_dict.items():
            meteo_data_local[m][param] = series
            
    # Generate change groups
    trends, warnings = _build_change_groups(
        consensus_truth=consensus_truth,
        valid_start=valid_start,
        start_hour=0,
        meteo_data_local=meteo_data_local,
        corrected_rain_data=qm_rain_data,
        rain_timing_offset=0, # Assuming no timing shift for now
        model_weights=model_weights
    )
    
    # Define Base Group
    first_row = consensus_truth[0]
    best_guess = {
        'dir': f"{int(first_row['Wind Dir.']):03d}" if pd.notna(first_row['Wind Dir.']) else "000",
        'spd': f"{int(first_row['Wind']):02d}" if pd.notna(first_row['Wind']) else "00",
        'vis': '9999',
        'wx': '',
        'cloud': 'SCT018',
        'trends': trends,
        'badge': 'MME Consensus',
        'metrics': {'leader_rmse': 'N/A', 'leader_strat': 'N/A'}
    }
    
    # Build actual TAF text
    taf_text = _build_taf_text(best_guess, valid_start, iss_day, iss_utc)
    
    # Return structured Intel object
    return {
        "valid_start": valid_start.strftime("%Y-%m-%d %H:%M:%S"),
        "issued_utc": iss_utc,
        "issued_day": iss_day,
        "base_group": best_guess,
        "taf_text": taf_text,
        "warnings": warnings
    }
