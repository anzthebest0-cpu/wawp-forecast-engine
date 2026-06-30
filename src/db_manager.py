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
        """
        # param map:
        # Temperature -> temperature, Dewpoint -> dewpoint, 
        # Wind Speed -> wind_speed, Wind Dir. -> wind_dir, Rain -> rain_1h / rain
        
        # We need to map standard param names to DB columns
        param_map_f = {
            "Temperature": "temperature",
            "Dewpoint": "dewpoint",
            "Humidity": "humidity",
            "Pressure": "pressure",
            "Wind Speed": "wind_speed",
            "Wind Gust": "wind_gust",
            "Wind Dir.": "wind_dir",
            "Rainfall": "rain"
        }
        
        param_map_o = {
            "Temperature": "temperature",
            "Dewpoint": "dewpoint",
            "Humidity": "humidity",
            "Pressure": "pressure",
            "Wind Speed": "wind_speed",
            "Wind Gust": "wind_gust_max",
            "Wind Dir.": "wind_dir",
            "Rainfall": "rain_1h"
        }
        
        f_col = param_map_f.get(param)
        o_col = param_map_o.get(param)
        
        if not f_col or not o_col:
            return pd.DataFrame()
            
        # Return long format directly to preserve model-specific run_init_utc for Lead-Time calculation
        query = f"""
            SELECT 
                f.forecast_time as Datetime,
                f.model as Model,
                f.run_init_utc as Run_Init_UTC,
                f.{f_col} as forecast,
                o.{o_col} as obs
            FROM meteologix_forecasts f
            INNER JOIN awos_observations o 
                ON f.location = o.location 
                AND f.forecast_time = o.obs_time
            WHERE f.forecast_time >= ? AND f.forecast_time <= ?
        """
        return pd.read_sql_query(query, self.conn, params=(start_date, end_date))

    def ingest_awos_files(self, directory: str) -> int:
        """
        Scans a directory for AWOS .dat files, parses them, and inserts into awos_observations.
        Returns the number of new rows inserted.
        """
        import os
        import glob
        
        search = os.path.join(directory, "**", "*.dat")
        files = glob.glob(search, recursive=True)
        if not files:
            return 0
            
        cursor = self.conn.cursor()
        sql = """
            INSERT OR IGNORE INTO awos_observations (
                location, obs_time, temperature, dewpoint, humidity, 
                wind_dir, wind_speed, wind_gust_max, rain_1h
            ) VALUES (
                :location, :obs_time, :temperature, :dewpoint, :humidity,
                :wind_dir, :wind_speed, :wind_gust_max, :rain_1h
            )
        """
        
        total_inserted = 0
        location = "Bandara_Sangia_Ni_Bandera"
        
        for f in files:
            try:
                # Cols: 1=Date, 2=Hour, 5=Temp, 6=Dewp, 7=RH, 8=WD, 9=WS, 11=Gust, 12=Rain
                df = pd.read_csv(
                    f, sep=r"\s+", skiprows=4, header=None,
                    usecols=[1, 2, 5, 6, 7, 8, 9, 11, 12], 
                    names=["Date", "Hour", "Temp", "Dewp", "RH", "WD", "WS", "Gust", "Rain"]
                )
                
                # Convert date strings to datetime (UTC)
                df["UTC"] = pd.to_datetime(
                    df["Date"].astype(str) + df["Hour"].astype(str).str.zfill(2),
                    format="%Y%m%d%H",
                    errors="coerce"
                )
                df = df.dropna(subset=["UTC"])
                
                # Convert to numeric, scale where necessary
                # Temp, Dewp, Rain are in 0.1 units (e.g. 243 = 24.3C)
                rows_to_insert = []
                for _, row in df.iterrows():
                    def clean_val(val, scale=1.0):
                        try:
                            # Handling '////' or '/////' which become NaN when coerced
                            v = float(val)
                            if pd.isna(v): return None
                            return v * scale
                        except (ValueError, TypeError):
                            return None
                            
                    obs_time = row["UTC"].strftime('%Y-%m-%d %H:%M:%S')
                    
                    rows_to_insert.append({
                        "location": location,
                        "obs_time": obs_time,
                        "temperature": clean_val(row["Temp"], 0.1),
                        "dewpoint": clean_val(row["Dewp"], 0.1),
                        "humidity": clean_val(row["RH"], 1.0),
                        "wind_dir": clean_val(row["WD"], 1.0),
                        "wind_speed": clean_val(row["WS"], 1.0),
                        "wind_gust_max": clean_val(row["Gust"], 1.0),
                        "rain_1h": clean_val(row["Rain"], 0.1)
                    })
                    
                cursor.executemany(sql, rows_to_insert)
                total_inserted += cursor.rowcount
            except Exception as e:
                print(f"Error parsing AWOS file {f}: {e}")
                
        self.conn.commit()
        return total_inserted

    def close(self):
        self.conn.close()
