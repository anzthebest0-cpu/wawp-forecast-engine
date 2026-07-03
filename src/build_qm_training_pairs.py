"""
Build materialized forecast-observation training pairs for multi-parameter QM.
"""
from __future__ import annotations

import logging
import os
import sys

from src.advanced_ensemble_weighter import MODELS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("qm_pairs")


def build_training_pairs(db) -> int:
    log.info("Building qm_training_pairs candidate")
    model_filter = ",".join("?" for _ in MODELS)
    db.conn.execute("DROP TABLE IF EXISTS qm_training_pairs_candidate")
    db.conn.execute(f"""
        CREATE TABLE qm_training_pairs_candidate AS
        SELECT
            f.model,
            f.run_init_utc,
            f.forecast_time AS valid_time,
            f.lead_hours,
            CASE
                WHEN f.lead_hours <= 6 THEN 'L1_0_6h'
                WHEN f.lead_hours <= 12 THEN 'L2_6_12h'
                WHEN f.lead_hours <= 24 THEN 'L3_12_24h'
                WHEN f.lead_hours <= 48 THEN 'L4_24_48h'
                ELSE 'L5_48plus'
            END AS lead_bucket,
            CASE
                WHEN f.lead_hours <= 6 THEN 'L1_0_6h'
                WHEN f.lead_hours <= 18 THEN 'L2_6_18h'
                ELSE 'L3_18h_plus'
            END AS lead_bucket_gust,
            f.temperature  AS fcst_temperature,
            f.dewpoint     AS fcst_dewpoint,
            f.pressure_msl AS fcst_pressure,
            f.wind_speed   AS fcst_wind_speed,
            f.wind_gust    AS fcst_wind_gust,
            f.wind_dir     AS fcst_wind_dir,
            f.rain         AS fcst_rain,
            o.temperature  AS obs_temperature,
            o.dewpoint     AS obs_dewpoint,
            o.pressure     AS obs_pressure,
            o.wind_speed   AS obs_wind_speed,
            o.wind_gust_max AS obs_wind_gust,
            o.wind_dir     AS obs_wind_dir,
            o.rain_1h      AS obs_rain
        FROM openmeteo_forecasts f
        INNER JOIN awos_observations o
            ON f.forecast_time = o.obs_time
            AND f.location = o.location
        WHERE o.temperature IS NOT NULL
          AND f.model IN ({model_filter})
        ORDER BY f.model, f.run_init_utc, f.forecast_time
    """, tuple(MODELS))
    n = db.conn.execute("SELECT COUNT(*) FROM qm_training_pairs_candidate").fetchone()[0]
    if n <= 0:
        exists = db.conn.execute("""
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type='table' AND name='qm_training_pairs'
        """).fetchone()[0]
        db.conn.execute("DROP TABLE IF EXISTS qm_training_pairs_candidate")
        if exists:
            db.conn.commit()
            log.warning("No new QM training pairs found; preserving existing qm_training_pairs table")
            return int(db.conn.execute("SELECT COUNT(*) FROM qm_training_pairs").fetchone()[0])
        db.conn.commit()
        log.warning("No QM training pairs found; leaving qm_training_pairs unchanged")
        return 0
    else:
        log.info("Replacing qm_training_pairs table")
        db.conn.execute("DROP TABLE IF EXISTS qm_training_pairs")
        db.conn.execute("ALTER TABLE qm_training_pairs_candidate RENAME TO qm_training_pairs")
    db.conn.execute("CREATE INDEX idx_qm_tp_model ON qm_training_pairs(model, lead_bucket)")
    db.conn.execute("CREATE INDEX idx_qm_tp_valid ON qm_training_pairs(valid_time)")
    db.conn.commit()
    log.info(f"Built {n} training pairs")
    return int(n)


def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.db_manager import ForecastDB

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db = ForecastDB(os.path.join(root, "wawp_forecasts.db"))
    try:
        build_training_pairs(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
