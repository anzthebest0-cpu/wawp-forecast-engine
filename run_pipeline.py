import os
import logging
import sys
import json
from datetime import datetime, timezone

from src.scrape_openmeteo import main as scrape_openmeteo_models
from src.db_manager import ForecastDB
from src.export_dashboard_data import export_all
from src.ingest_awos import ingest_latest_awos
from src.build_qm_training_pairs import build_training_pairs
from src.train_qm_multiparam import train_all as train_multiparam_qm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("pipeline")

def run():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(_HERE, "wawp_forecasts.db")
    DOCS_DIR = os.path.join(_HERE, "docs", "data")
    
    log.info("Starting WAWP Open-Meteo Pipeline...")
    
    # 1. Scrape latest data
    openmeteo_stale = False
    openmeteo_error = None
    all_models_data, stacked_rows, openmeteo_rows = {}, [], []
    try:
        all_models_data, stacked_rows, openmeteo_rows = scrape_openmeteo_models()
    except Exception as e:
        openmeteo_stale = True
        openmeteo_error = str(e)
        log.error(f"Open-Meteo scraper failed; continuing with existing forecast archive: {e}")
        
    # 2. Ingest into local DB (WAL mode, INSERT OR IGNORE)
    db = ForecastDB(DB_PATH)
    try:
        try:
            new_count = db.ingest_rows(stacked_rows) if stacked_rows else 0
            log.info(f"Database ingested {new_count} new rows (duplicates ignored).")
            if openmeteo_rows:
                om_count = db.ingest_openmeteo_rows(openmeteo_rows)
                log.info(f"Open-Meteo database ingested {om_count} new rows.")
            elif openmeteo_stale:
                log.warning("No new Open-Meteo rows ingested; exporter will use the latest archived model data.")
        except Exception as e:
            log.error(f"Database ingestion failed: {e}")
            sys.exit(1)
            
        # 2.5 Ingest AWOS data if exists
        awos_stale = False
        awos_error = None
        try:
            ingest_latest_awos()
            log.info("AWOS ingestion completed.")
        except Exception as e:
            log.error(f"AWOS ingestion failed: {e}")
            awos_stale = True
            awos_error = str(e)

        health_path = os.path.join(_HERE, "docs", "data", "pipeline_health.json")
        os.makedirs(os.path.dirname(health_path), exist_ok=True)
        with open(health_path, "w", encoding="utf-8") as f:
            json.dump({
                "openmeteo_stale": openmeteo_stale,
                "openmeteo_models_fetched": sorted(all_models_data.keys()),
                "awos_stale": awos_stale,
                "last_run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "last_error": openmeteo_error or awos_error,
                "openmeteo_error": openmeteo_error,
                "awos_error": awos_error,
            }, f, indent=2)

        try:
            obs_count = db.conn.execute("SELECT COUNT(*) FROM awos_observations").fetchone()[0]
            historical_count = db.conn.execute(
                "SELECT COUNT(*) FROM openmeteo_forecasts WHERE run_init_utc = 'historical_forecast_api'"
            ).fetchone()[0]
            if historical_count > 0 and obs_count > 0:
                pair_count = build_training_pairs(db)
                if pair_count > 0:
                    train_multiparam_qm(DB_PATH, DOCS_DIR)
            else:
                log.info("Skipping QM training refresh: no historical forecast-observation archive overlap yet.")
        except Exception as e:
            log.error(f"QM training refresh failed: {e}")

        # 3. Generate Consensus and Export to Dashboard
        try:
            export_all(db, DOCS_DIR)
            log.info("Dashboard data exported.")
        except Exception as e:
            log.error(f"Exporter failed: {e}")
            
    finally:
        db.close()
        
    log.info("Pipeline finished successfully.")

if __name__ == "__main__":
    run()
