import os
import json
import logging
from datetime import datetime, timezone
from src.db_manager import ForecastDB
from src.guidance_generator import generate_consensus
from src.advanced_ensemble_weighter import MODELS

log = logging.getLogger("exporter")

def export_all(db: ForecastDB, output_dir: str):
    """
    Exports JSON payloads for the HTML dashboard:
    1. taf_guidance.json (Consensus timeline)
    2. latest_weights.json (Current model weights)
    3. latest_performance.json (Metrics)
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Consensus Guidance
    log.info("Generating consensus...")
    consensus_data = generate_consensus(db)
    
    if consensus_data:
        payload = {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                "version": "v200",
                "location": "Bandara_Sangia_Ni_Bandera"
            },
            "data": consensus_data
        }
        
        out_path = os.path.join(output_dir, "taf_guidance.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        log.info(f"Exported {out_path}")
    else:
        log.warning("No consensus data generated, skipping taf_guidance.json export.")
        
    # 2. Weights (Mock for now, normally read from weighter)
    weights_payload = {
        "metadata": {
            "updated_at": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        },
        "weights": {
            param: {m: round(1.0/len(MODELS), 3) for m in MODELS}
            for param in ["Temperature", "Dewpoint", "Pressure", "Rainfall", "Wind Speed", "Wind Direction", "Wind Gust"]
        }
    }
    weights_path = os.path.join(output_dir, "latest_weights.json")
    with open(weights_path, 'w', encoding='utf-8') as f:
        json.dump(weights_payload, f, indent=2)
        
    # 3. Performance Metrics (Mock for now)
    perf_payload = {
        "metadata": {
            "period": "Last 7 Days"
        },
        "metrics": {
            m: {"RMSE_Temp": 1.2, "RMSE_Wind": 3.0, "POD_Rain": 0.85, "FAR_Rain": 0.20}
            for m in MODELS
        }
    }
    perf_path = os.path.join(output_dir, "latest_performance.json")
    with open(perf_path, 'w', encoding='utf-8') as f:
        json.dump(perf_payload, f, indent=2)
        
    log.info("Dashboard exports complete.")
