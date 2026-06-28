import os
import logging
import sys

from src.scrape_meteologix import main as scrape_models
from src.db_manager import ForecastDB
from src.export_dashboard_data import export_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("pipeline")

def run():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(_HERE, "wawp_forecasts.db")
    ARCHIVE_DIR = os.path.join(_HERE, "Archives", "Meteologix_MultiModel")
    DOCS_DIR = os.path.join(_HERE, "docs", "data")
    
    log.info("Starting WAWP Meteologix Pipeline...")
    
    # 1. Scrape latest data
    try:
        all_models_data, stacked_rows = scrape_models()
    except Exception as e:
        log.error(f"Scraper failed: {e}")
        sys.exit(1)
        
    # 2. Ingest into local DB (WAL mode, INSERT OR IGNORE)
    db = ForecastDB(DB_PATH)
    try:
        new_count = db.ingest_rows(stacked_rows)
        log.info(f"Database ingested {new_count} new rows (duplicates ignored).")
    except Exception as e:
        log.error(f"Database ingestion failed: {e}")
        db.close()
        sys.exit(1)
        
    # 3. Generate Consensus and Export to Dashboard
    try:
        export_all(db, DOCS_DIR)
        log.info("Dashboard data exported.")
    except Exception as e:
        log.error(f"Exporter failed: {e}")
        
    db.close()
    log.info("Pipeline finished successfully.")

if __name__ == "__main__":
    run()
