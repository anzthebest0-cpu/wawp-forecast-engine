import sqlite3
import pandas as pd
from datetime import datetime

class ForecastDB:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        # Enable WAL mode for high concurrency and crash safety
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()
        
        # Forecasts Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meteologix_forecasts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                location        TEXT    NOT NULL,
                model           TEXT    NOT NULL,
                run_init_utc    TEXT    NOT NULL,   -- ISO8601: '2026-06-15 00:00:00'
                forecast_time   TEXT    NOT NULL,   -- ISO8601: '2026-06-15 09:00:00' (WITA)
                scraped_at      TEXT    NOT NULL,   -- ISO8601: when the scraper ran
                temperature     REAL,
                dewpoint        REAL,
                humidity        REAL,
                pressure        REAL,
                rain            REAL    DEFAULT 0.0,
                prob_precip_01  REAL    DEFAULT 0.0,
                prob_precip_10  REAL    DEFAULT 0.0,
                prob_precip_100 REAL    DEFAULT 0.0,
                wind_speed      REAL    DEFAULT 0.0,
                wind_gust       REAL    DEFAULT 0.0,
                wind_dir        REAL,
                sunshine        REAL    DEFAULT 0.0,
                low_clouds      REAL    DEFAULT 0.0,
                mid_clouds      REAL    DEFAULT 0.0,
                high_clouds     REAL    DEFAULT 0.0,
                condition       TEXT,
                UNIQUE(location, model, run_init_utc, forecast_time)
            );
        """)
        
        # Indexes for fast querying
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_forecast_time ON meteologix_forecasts(forecast_time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_model_run ON meteologix_forecasts(model, run_init_utc);")

        # Observations Table (For future use)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS awos_observations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                location        TEXT    NOT NULL,
                obs_time        TEXT    NOT NULL,   -- ISO8601
                temperature     REAL,
                dewpoint        REAL,
                humidity        REAL,
                pressure        REAL,
                rain_1h         REAL    DEFAULT 0.0,
                wind_speed      REAL    DEFAULT 0.0,
                wind_gust_max   REAL    DEFAULT 0.0,
                wind_dir        REAL,
                visibility      REAL,
                UNIQUE(location, obs_time)
            );
        """)
        
        self.conn.commit()

    def ingest_rows(self, rows: list[dict]) -> int:
        """
        Inserts new forecast rows into the database.
        Uses INSERT OR IGNORE to automatically deduplicate overlapping runs (e.g., GEM 00Z during a 06Z scrape).
        Returns the count of newly inserted rows.
        """
        if not rows:
            return 0
            
        cursor = self.conn.cursor()
        
        sql = """
            INSERT OR IGNORE INTO meteologix_forecasts (
                location, model, run_init_utc, forecast_time, scraped_at,
                temperature, dewpoint, humidity, pressure, rain,
                prob_precip_01, prob_precip_10, prob_precip_100,
                wind_speed, wind_gust, wind_dir, sunshine,
                low_clouds, mid_clouds, high_clouds, condition
            ) VALUES (
                :location, :model, :run_init_utc, :forecast_time, :scraped_at,
                :temperature, :dewpoint, :humidity, :pressure, :rain,
                :prob_precip_01, :prob_precip_10, :prob_precip_100,
                :wind_speed, :wind_gust, :wind_dir, :sunshine,
                :low_clouds, :mid_clouds, :high_clouds, :condition
            )
        """
        
        # Map original dict keys to safe SQL parameter names
        clean_rows = []
        for row in rows:
            clean_row = {
                "location": row.get("Location"),
                "model": row.get("Model"),
                "run_init_utc": row.get("Run_Init_UTC"),
                "forecast_time": row.get("Datetime"),
                "scraped_at": row.get("Scraped_At"),
                "temperature": row.get("Temperature"),
                "dewpoint": row.get("Dewpoint"),
                "humidity": row.get("Humidity"),
                "pressure": row.get("Pressure"),
                "rain": row.get("Rain"),
                "prob_precip_01": row.get("Prob_Precip_0.1"),
                "prob_precip_10": row.get("Prob_Precip_1.0"),
                "prob_precip_100": row.get("Prob_Precip_10.0"),
                "wind_speed": row.get("Wind"),
                "wind_gust": row.get("Gust"),
                "wind_dir": row.get("Wind Dir."),
                "sunshine": row.get("Sunshine"),
                "low_clouds": row.get("Low_Clouds"),
                "mid_clouds": row.get("Mid_Clouds"),
                "high_clouds": row.get("High_Clouds"),
                "condition": row.get("Condition"),
            }
            # Convert pandas nan to None
            for k, v in clean_row.items():
                if pd.isna(v):
                    clean_row[k] = None
                    
            clean_rows.append(clean_row)
            
        cursor.executemany(sql, clean_rows)
        self.conn.commit()
        
        return cursor.rowcount

    def get_latest_forecasts(self, location: str) -> pd.DataFrame:
        """
        For each model, get the most recent run_init_utc's forecast rows.
        This provides the freshest consensus input.
        """
        query = """
            WITH LatestRuns AS (
                SELECT model, MAX(run_init_utc) as max_init
                FROM meteologix_forecasts
                WHERE location = ?
                GROUP BY model
            )
            SELECT f.*
            FROM meteologix_forecasts f
            INNER JOIN LatestRuns lr ON f.model = lr.model AND f.run_init_utc = lr.max_init
            WHERE f.location = ?
            ORDER BY f.model, f.forecast_time
        """
        return pd.read_sql_query(query, self.conn, params=(location, location))

    def get_verification_pairs(self, param: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Join forecasts with observations for weighter training.
        This will be fully implemented when observation ingestion is integrated.
        """
        # Placeholder for phase 8/future observation joining logic
        pass

    def close(self):
        self.conn.close()
