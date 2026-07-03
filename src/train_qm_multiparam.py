"""
Train multi-parameter quantile mappers from qm_training_pairs.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.quantile_mapper import fit_multiparam_qm_to_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("qm_train")


def train_all(db_path: str, output_dir: str) -> dict:
    with sqlite3.connect(db_path) as conn:
        trained = fit_multiparam_qm_to_db(conn, log_fn=log.warning)
        low_conf = []
        rows = conn.execute("""
            SELECT model, parameter, lead_bucket, n_samples,
                   COALESCE(source_type, 'unknown') AS source_type,
                   COALESCE(correction_layer, 'historical_prior') AS correction_layer
            FROM qm_cdfs
            WHERE low_confidence = 1 AND enabled = 1
              AND COALESCE(deprecated, 0) = 0
            ORDER BY model, parameter, lead_bucket
        """).fetchall()
        for model, parameter, lead_bucket, n_samples, source_type, correction_layer in rows:
            low_conf.append({
                "model": model,
                "parameter": parameter,
                "lead_bucket": lead_bucket,
                "n_samples": n_samples,
                "source_type": source_type,
                "correction_layer": correction_layer,
            })

    os.makedirs(output_dir, exist_ok=True)
    status = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "trained": trained,
        "low_confidence": low_conf,
    }
    with open(os.path.join(output_dir, "qm_status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    with open(os.path.join(output_dir, "health.json"), "w", encoding="utf-8") as f:
        json.dump({"qm_low_confidence": low_conf}, f, indent=2)
    return status


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    status = train_all(
        db_path=os.path.join(root, "wawp_forecasts.db"),
        output_dir=os.path.join(root, "docs", "data"),
    )
    enabled = 0
    for model_data in status["trained"].values():
        for param_data in model_data.values():
            enabled += sum(1 for bucket in param_data.values() if bucket.get("enabled"))
    log.info(f"QM training complete. Enabled CDFs: {enabled}")


if __name__ == "__main__":
    main()
