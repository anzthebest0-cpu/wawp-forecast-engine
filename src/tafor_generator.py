import os
import json
import math
from datetime import datetime, timedelta
import pandas as pd
from src.taf_core import RainConfig, _build_change_groups
from src.vis_cloud_proxy import build_hourly_vis_cloud, get_weather_phenomenon


def _event_skill_context(event_weight_diagnostics: dict | None) -> dict:
    diagnostics = event_weight_diagnostics or {}

    def summarize(param: str) -> dict:
        diag = diagnostics.get(param) or {}
        event_weights = diag.get("event_weights") or {}
        model_scores = diag.get("model_scores") or {}
        weighted_score = 0.0
        weighted_far = 0.0
        far_weight = 0.0
        top_models = []

        for model, weight in event_weights.items():
            try:
                w = float(weight or 0.0)
            except (TypeError, ValueError):
                w = 0.0
            score_payload = model_scores.get(model) or {}
            try:
                score = float(score_payload.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            weighted_score += w * score
            if score_payload.get("pm2h_far") is not None:
                try:
                    weighted_far += w * float(score_payload.get("pm2h_far"))
                    far_weight += w
                except (TypeError, ValueError):
                    pass
            if score_payload.get("eligible") and w > 0:
                top_models.append((model, w, score))

        top_models.sort(key=lambda item: item[1], reverse=True)
        strength = max(0.0, min(1.0, weighted_score / 0.35)) if weighted_score > 0 else 0.0
        return {
            "applied": bool(diag.get("applied")),
            "reason": diag.get("reason") or "event-window diagnostics unavailable",
            "weighted_score": round(weighted_score, 4),
            "weighted_far": round(weighted_far / far_weight, 4) if far_weight > 0 else None,
            "strength": round(strength, 4),
            "top_models": [m for m, _, _ in top_models[:3]],
            "min_events": diag.get("min_events"),
            "threshold": diag.get("threshold"),
        }

    return {
        "Rainfall": summarize("Rainfall"),
        "Wind Gust": summarize("Wind Gust"),
    }


def _event_aware_config(event_context: dict):
    rain = event_context.get("Rainfall", {})
    gust = event_context.get("Wind Gust", {})
    rain_strength = float(rain.get("strength") or 0.0) if rain.get("applied") else 0.0
    gust_strength = float(gust.get("strength") or 0.0) if gust.get("applied") else 0.0

    class EventAwareRainConfig(RainConfig):
        pass

    if rain_strength > 0:
        threshold_relax = min(0.15, 0.05 + (0.10 * rain_strength))
        confidence_relax = min(0.20, 0.08 + (0.12 * rain_strength))
        EventAwareRainConfig.CONSENSUS_THR = max(0.75, RainConfig.CONSENSUS_THR * (1.0 - threshold_relax))
        EventAwareRainConfig.VOTE_THR = max(0.75, RainConfig.VOTE_THR * (1.0 - threshold_relax))
        EventAwareRainConfig.TEMPO_CUT = max(0.12, RainConfig.TEMPO_CUT * (1.0 - confidence_relax))
        EventAwareRainConfig.PROB40_CUT = max(0.07, RainConfig.PROB40_CUT * (1.0 - confidence_relax))
        EventAwareRainConfig.PROB30_CUT = max(0.02, RainConfig.PROB30_CUT)

    EventAwareRainConfig.GUST_EVENT_ENABLED = gust_strength > 0
    EventAwareRainConfig.GUST_TRIGGER_DELTA = max(6.0, 8.0 - (2.0 * gust_strength))
    EventAwareRainConfig.GUST_MIN_EXCESS = 10.0
    return EventAwareRainConfig

def _build_taf_text(bg: dict, v_start, iss_day: int, iss_utc: str) -> str:
    issued_hh  = iss_utc[:2]
    issued_str = f"{iss_day:02d}{issued_hh}00Z"
    v_end      = v_start + timedelta(hours=24)
    valid_str  = (f"{v_start.day:02d}{v_start.hour:02d}/"
                  f"{v_end.day:02d}{v_end.hour:02d}")
    d = str(bg.get('dir', '000')).upper()
    s = str(bg.get('spd', '00')).zfill(2)
    g = str(bg.get('gust', '00')).zfill(2)
    
    if s == '00':
        wind = '00000KT'
    else:
        wind_dir = 'VRB' if d == 'VRB' else d.zfill(3)
        if int(g) >= int(s) + 10:
            wind = f"{wind_dir}{s}G{g}KT"
        else:
            wind = f"{wind_dir}{s}KT"
    vis   = bg.get('vis',   '9999') or '9999'
    wx    = (bg.get('wx',   '') or '').upper()
    cloud = (bg.get('cloud','SCT018') or 'SCT018').upper()
    
    # Implement CAVOK logic for Base Group
    cavok_cloud = False
    if cloud == 'NSC': cavok_cloud = True
    elif len(cloud) >= 3 and cloud[-3:].isdigit() and int(cloud[-3:]) >= 50: cavok_cloud = True

    rain_at_base = float(bg.get('rain_mmh', bg.get('rain', 0.0)) or 0.0)
    if vis == '9999' and cavok_cloud and not wx and rain_at_base <= 0.1:
        vis = 'CAVOK'
        cloud = ''
        wx = ''
    
    parts = [f'TAF WAWP {issued_str}', valid_str, wind, vis]
    if wx: parts.append(wx)
    if cloud: parts.append(cloud)
    
    body = ' '.join(filter(None, parts))
    
    for t in (bg.get('trends') or []):
        gt   = (t.get('type','')    or '').upper()
        ts   = (t.get('time_str','')or '').upper()
        tdir = (t.get('dir','')     or '').upper()
        tspd = str(t.get('spd','')  or '')
        tgst = str(t.get('gust','') or '')
        tvis = (t.get('vis','')     or '')
        twx  = (t.get('wx','')      or '').upper()
        tcld = [c.upper() for c in (t.get('clouds',[t.get('cloud', '')]) or []) if c]
        if not tcld and t.get('cloud'):
            tcld = [t.get('cloud').upper()]
            
        if not tdir or not tspd:
            wp = ''
        else:
            t_wind_dir = 'VRB' if tdir == 'VRB' else tdir.zfill(3)
            if tgst and int(tgst) >= int(tspd) + 10:
                wp = f"{t_wind_dir}{tspd.zfill(2)}G{tgst.zfill(2)}KT"
            else:
                wp = f"{t_wind_dir}{tspd.zfill(2)}KT"
                
        # Implement CAVOK logic for Change Groups
        t_cavok = False
        t_rain = float(t.get('rain_mmh', 0.0) or 0.0)
        if tvis == '9999' and not twx and t_rain <= 0.1:
            if not tcld or tcld[0] == 'NSC' or (len(tcld[0]) >= 3 and tcld[0][-3:].isdigit() and int(tcld[0][-3:]) >= 50):
                t_cavok = True
                
        if t_cavok:
            tvis = 'CAVOK'
            tcld = []
            twx = ''
        
        line = f'{gt} {ts}'.strip()
        extras = [x for x in [wp, tvis, twx] + tcld if x]
        if extras: line += ' ' + ' '.join(extras)
        body += '\n     ' + line
        
    header = f'FTID40 WAWP {iss_day:02d}{issued_hh}00'
    raw    = header + '\n' + body + '='
    return '\n'.join(' '.join(l.split()).rstrip() for l in raw.splitlines())


def _format_wind_speed(value) -> str:
    """Round knots conventionally; truncation incorrectly made 0.5-0.9 kt calm."""
    try:
        return f"{max(0, int(math.floor(float(value) + 0.5))):02d}"
    except (TypeError, ValueError):
        return "00"


def _generate_narration(bg: dict, trends: list) -> str:
    vis = bg.get('vis', '9999')
    if str(vis).upper() == 'CAVOK':
        vis_desc = 'baik'
    else:
        try:
            vis_int = int(vis)
        except (TypeError, ValueError):
            vis_int = 9999
        vis_desc = 'baik' if vis_int > 8000 else ('sedang' if vis_int >= 5000 else 'terbatas')
    wind = int(bg.get('spd', 0))
    wind_desc = f'ringan hingga sedang ({wind} KT)' if wind < 10 else f'cukup kencang ({wind} KT)'
    cloud_raw = bg.get('cloud', 'SCT018')
    cloud_desc = 'berawan sebagian' if 'SCT' in cloud_raw or 'FEW' in cloud_raw else 'berawan tebal'
    
    parts = [f'Secara umum cuaca {cloud_desc} dengan jarak pandang {vis_desc}. Angin dominan bertiup dengan kecepatan {wind_desc}.']
    
    rain_trends = []
    if trends:
        for t in trends:
            if 'RA' in t.get('wx', '') or 'TS' in t.get('wx', ''):
                t_str = t.get('time_str', '')
                if '/' in t_str:
                    fragments = t_str.split('/')
                    if len(fragments) == 2 and len(fragments[0]) == 4 and len(fragments[1]) == 4:
                        h1 = fragments[0][2:] + '00'
                        h2 = fragments[1][2:] + '00'
                        rain_trends.append(f'{h1}Z - {h2}Z')
                
    if rain_trends:
        parts.append(f'Terdapat potensi presipitasi/hujan pada periode: {", ".join(rain_trends)}.')
        
    return ' '.join(parts)

def generate_tafor(
    consensus_df: pd.DataFrame,
    model_data: dict,
    qm_rain_data: dict,
    model_weights: dict,
    target_issuance: str = None,
    event_weight_diagnostics: dict | None = None,
) -> dict:
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
            "gust": row["Wind Gust"] if "Wind Gust" in row and pd.notna(row.get("Wind Gust")) else 0.0,
            "prob_precip_10": row["Precip Probability"] if "Precip Probability" in row and pd.notna(row.get("Precip Probability")) else (
                row["Prob Precip 1.0mm"] if "Prob Precip 1.0mm" in row and pd.notna(row.get("Prob Precip 1.0mm")) else 0.0
            ),
            "model_visibility_m": row["Visibility"] if "Visibility" in row and pd.notna(row.get("Visibility")) else None,
            "Condition": row["Condition"] if pd.notna(row.get("Condition")) else "Clear",
            "low_clouds": row["Low Clouds"] if "Low Clouds" in row and pd.notna(row.get("Low Clouds")) else 0.0,
            "mid_clouds": row["Mid Clouds"] if "Mid Clouds" in row and pd.notna(row.get("Mid Clouds")) else 0.0,
            "high_clouds": row["High Clouds"] if "High Clouds" in row and pd.notna(row.get("High Clouds")) else 0.0
        }
        dt = pd.to_datetime(row["Datetime"])
        hour_data["month"] = dt.month
        
        pressure_history.append(hour_data["pressure_hpa"])
        vis_code, cloud_group = build_hourly_vis_cloud(hour_data, pressure_history)
        
        full_truth.append({
            "Rain": hour_data["rain"],
            "Wind": hour_data["spd"],
            "Wind Dir.": row["Wind Dir."],
            "Condition": hour_data["Condition"],
            "spd": hour_data["spd"],
            "gust": hour_data["gust"],
            "prob_precip_10": hour_data["prob_precip_10"],
            "dir_num": float(row["Wind Dir."]) if pd.notna(row.get("Wind Dir.")) else 0.0,
            "dir": f"{int(round(float(row['Wind Dir.']) / 10.0) * 10):03d}" if pd.notna(row.get("Wind Dir.")) and round(float(row["Wind Dir."]) / 10.0) * 10 < 360 else ("360" if pd.notna(row.get("Wind Dir.")) else "000"),
            "rain": hour_data["rain"],
            "temp_c": hour_data["temp_c"],
            "dewpoint_c": hour_data["dewpoint_c"],
            "pressure_hpa": hour_data["pressure_hpa"],
            "relative_humidity_pct": hour_data["relative_humidity_pct"],
            "cape": row["CAPE"] if "CAPE" in row and pd.notna(row.get("CAPE")) else None,
            "lifted_index": row["Lifted Index"] if "Lifted Index" in row and pd.notna(row.get("Lifted Index")) else None,
            "convective_inhib": row["Convective Inhibition"] if "Convective Inhibition" in row and pd.notna(row.get("Convective Inhibition")) else None,
            "weather_code": row["Weather Code"] if "Weather Code" in row and pd.notna(row.get("Weather Code")) else None,
            "month": hour_data["month"],
            "vis": vis_code,
            "cloud": cloud_group,
            "Datetime": row["Datetime"]
        })
        
    # Map issuance_utc -> valid hour in LST (WITA = UTC+8)
    # TAF starts 1 hour after issuance.
    # 2300Z -> starts 0000Z = 0800 LST
    # 0500Z -> starts 0600Z = 1400 LST
    # 1100Z -> starts 1200Z = 2000 LST
    # 1700Z -> starts 1800Z = 0200 LST
    val_map = {"2300": 8, "0500": 14, "1100": 20, "1700": 2}
    
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
    
    valid_start_utc = valid_start - timedelta(hours=8)
    
    if target_issuance:
        iss_utc = target_issuance
        iss_day = (valid_start_utc - timedelta(hours=1)).day
    else:
        iss_utc_obj = valid_start_utc - timedelta(hours=1)
        iss_utc = f"{iss_utc_obj.hour:02d}00"
        iss_day = iss_utc_obj.day
    
    # Map model_data into meteo_data_local: {model: {param: series}}
    from src.advanced_ensemble_weighter import MODELS
    
    active_models = set(MODELS)
    for models_dict in model_data.values():
        active_models.update(models_dict.keys())
    active_models.update(qm_rain_data.keys())
    meteo_data_local = {m: {} for m in active_models}
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
            
    event_context = _event_skill_context(event_weight_diagnostics)
    taf_config = _event_aware_config(event_context)

    # Generate change groups
    change_group_weights = model_weights.get("Rainfall", model_weights) if isinstance(model_weights, dict) else model_weights
    trends, warnings = _build_change_groups(
        consensus_truth=consensus_truth,
        valid_start=valid_start_utc,
        start_hour=valid_start_utc.hour,
        meteo_data_local=meteo_data_local,
        corrected_rain_data=qm_rain_local,
        rain_timing_offset=0, 
        model_weights=change_group_weights,
        config=taf_config,
    )
    taf_notes = []
    if event_context["Rainfall"].get("applied"):
        models = ", ".join(event_context["Rainfall"].get("top_models") or [])
        taf_notes.append(
            "Rain/TS confidence adjusted with event-window skill"
            + (f" from {models}" if models else "")
            + f" (strength {event_context['Rainfall'].get('strength', 0):.2f})."
        )
    if event_context["Wind Gust"].get("applied"):
        models = ", ".join(event_context["Wind Gust"].get("top_models") or [])
        taf_notes.append(
            "Gust groups allowed by event-window skill"
            + (f" from {models}" if models else "")
            + f" (strength {event_context['Wind Gust'].get('strength', 0):.2f})."
        )
    warnings = list(warnings or []) + taf_notes
    
    # Define Base Group
    first_row = consensus_truth[0]
    
    # Round Wind Direction to nearest 10
    d_val = first_row.get('Wind Dir.')
    if pd.notna(d_val):
        d_rounded = int(round(float(d_val) / 10.0) * 10)
        d_str = f"{d_rounded:03d}" if d_rounded < 360 else "360"
    else:
        d_str = "000"

    base_wx = ''
    try:
        vis_int = 9999 if str(first_row.get('vis', '9999')).upper() == 'CAVOK' else int(first_row.get('vis', '9999'))
    except (ValueError, TypeError):
        vis_int = 9999
    base_wx = get_weather_phenomenon(
        rain_mmh=first_row.get("rain", 0.0),
        rh_pct=first_row.get("relative_humidity_pct", 80.0),
        temp_c=first_row.get("temp_c", 28.0),
        dewpoint_c=first_row.get("dewpoint_c", 24.0),
        vis_m=vis_int,
        local_hour_wita=valid_start.hour,
        cape=first_row.get("cape"),
        lifted_index=first_row.get("lifted_index"),
        cin=first_row.get("convective_inhib"),
        weather_code=first_row.get("weather_code"),
        month=first_row.get("month"),
    )
        
    best_guess = {
        'dir': d_str,
        'spd': _format_wind_speed(first_row.get('spd')),
        'gust': _format_wind_speed(first_row.get('gust')),
        'vis': first_row['vis'],
        'wx': base_wx,
        'cloud': first_row['cloud'],
        'rain': first_row.get('rain', 0.0),
        'rain_mmh': first_row.get('rain', 0.0),
        'trends': trends,
        'badge': f'MME {iss_utc}Z Shift',
        'metrics': {'leader_rmse': 'N/A', 'leader_strat': 'N/A'}
    }
    
    # Build actual TAF text
    # valid_start_utc was already calculated above
    taf_text = _build_taf_text(best_guess, valid_start_utc, iss_day, iss_utc)
    narration_text = _generate_narration(best_guess, trends)
    
    return {
        "valid_start": valid_start.strftime("%Y-%m-%d %H:%M:%S"),
        "issued_utc": iss_utc,
        "issued_day": iss_day,
        "base_group": best_guess,
        "taf_text": taf_text,
        "narration": narration_text,
        "warnings": warnings,
        "event_skill_context": event_context,
    }
