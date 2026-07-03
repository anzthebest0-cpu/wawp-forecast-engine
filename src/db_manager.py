import sqlite3
import pandas as pd
from datetime import datetime
from src.awos_hourly_parser import read_hourly_awos

LOCATION_NAME = "Bandara_Sangia_Ni_Bandera"

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
                visibility      REAL,
                cape            REAL,
                lifted_index    REAL,
                convective_inhib REAL,
                weather_code    INTEGER,
                boundary_layer_h REAL,
                UNIQUE(location, model, run_init_utc, forecast_time)
            );
        """)
        for ddl in [
            "ALTER TABLE meteologix_forecasts ADD COLUMN visibility REAL",
            "ALTER TABLE meteologix_forecasts ADD COLUMN cape REAL",
            "ALTER TABLE meteologix_forecasts ADD COLUMN lifted_index REAL",
            "ALTER TABLE meteologix_forecasts ADD COLUMN convective_inhib REAL",
            "ALTER TABLE meteologix_forecasts ADD COLUMN weather_code INTEGER",
            "ALTER TABLE meteologix_forecasts ADD COLUMN boundary_layer_h REAL",
        ]:
            try:
                cursor.execute(ddl)
            except sqlite3.OperationalError:
                pass
        
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
        try:
            cursor.execute("ALTER TABLE awos_observations ADD COLUMN wind_gust_max REAL;")
        except sqlite3.OperationalError:
            pass

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_awos_time ON awos_observations(obs_time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_awos_location_time ON awos_observations(location, obs_time);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS awos_observations_1min (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                location        TEXT    NOT NULL,
                obs_time        TEXT    NOT NULL,
                wind_speed      REAL,
                wind_dir        REAL,
                wind_gust       REAL,
                wind_gust_dir   REAL,
                temperature     REAL,
                dewpoint        REAL,
                humidity        REAL,
                pressure_qnh    REAL,
                rain_1min       REAL,
                solar_rad       REAL,
                UNIQUE(location, obs_time)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_1min_time ON awos_observations_1min(obs_time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_1min_date ON awos_observations_1min(date(obs_time));")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS openmeteo_forecasts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                location          TEXT    NOT NULL,
                model             TEXT    NOT NULL,
                run_init_utc      TEXT    NOT NULL,
                forecast_time     TEXT    NOT NULL,
                lead_hours        REAL,
                scraped_at        TEXT    NOT NULL,
                temperature       REAL,
                dewpoint          REAL,
                humidity          REAL,
                pressure_msl      REAL,
                rain              REAL    DEFAULT 0.0,
                precipitation     REAL    DEFAULT 0.0,
                showers           REAL    DEFAULT 0.0,
                snowfall          REAL    DEFAULT 0.0,
                wind_speed        REAL,
                wind_gust         REAL,
                wind_dir          REAL,
                cloud_cover       REAL,
                cloud_cover_low   REAL,
                cloud_cover_mid   REAL,
                cloud_cover_high  REAL,
                weather_code      INTEGER,
                visibility        REAL,
                cape              REAL,
                lifted_index      REAL,
                convective_inhib  REAL,
                boundary_layer_h  REAL,
                sunshine_duration REAL,
                shortwave_radiation REAL,
                direct_radiation  REAL,
                diffuse_radiation REAL,
                precipitation_probability REAL,
                soil_temperature_0_to_7cm REAL,
                soil_moisture_0_to_7cm REAL,
                UNIQUE(location, model, run_init_utc, forecast_time)
            );
        """)
        for ddl in [
            "ALTER TABLE openmeteo_forecasts ADD COLUMN precipitation REAL DEFAULT 0.0",
            "ALTER TABLE openmeteo_forecasts ADD COLUMN snowfall REAL DEFAULT 0.0",
            "ALTER TABLE openmeteo_forecasts ADD COLUMN sunshine_duration REAL",
            "ALTER TABLE openmeteo_forecasts ADD COLUMN shortwave_radiation REAL",
            "ALTER TABLE openmeteo_forecasts ADD COLUMN direct_radiation REAL",
            "ALTER TABLE openmeteo_forecasts ADD COLUMN diffuse_radiation REAL",
            "ALTER TABLE openmeteo_forecasts ADD COLUMN precipitation_probability REAL",
            "ALTER TABLE openmeteo_forecasts ADD COLUMN soil_temperature_0_to_7cm REAL",
            "ALTER TABLE openmeteo_forecasts ADD COLUMN soil_moisture_0_to_7cm REAL",
        ]:
            try:
                cursor.execute(ddl)
            except sqlite3.OperationalError:
                pass
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_om_run ON openmeteo_forecasts(model, run_init_utc);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_om_valid ON openmeteo_forecasts(forecast_time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_om_lead ON openmeteo_forecasts(model, lead_hours);")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS qm_cdfs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                model           TEXT NOT NULL,
                parameter       TEXT NOT NULL,
                lead_bucket     TEXT NOT NULL,
                fcst_quantiles  TEXT NOT NULL,
                obs_quantiles   TEXT NOT NULL,
                n_samples       INTEGER NOT NULL,
                crps_before     REAL,
                crps_after      REAL,
                bias_before     REAL,
                bias_after      REAL,
                trained_at      TEXT NOT NULL,
                enabled         INTEGER DEFAULT 1,
                method          TEXT,
                low_confidence  INTEGER DEFAULT 0,
                metadata        TEXT,
                UNIQUE(model, parameter, lead_bucket)
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
                low_clouds, mid_clouds, high_clouds, condition,
                visibility, cape, lifted_index, convective_inhib, weather_code, boundary_layer_h
            ) VALUES (
                :location, :model, :run_init_utc, :forecast_time, :scraped_at,
                :temperature, :dewpoint, :humidity, :pressure, :rain,
                :prob_precip_01, :prob_precip_10, :prob_precip_100,
                :wind_speed, :wind_gust, :wind_dir, :sunshine,
                :low_clouds, :mid_clouds, :high_clouds, :condition,
                :visibility, :cape, :lifted_index, :convective_inhib, :weather_code, :boundary_layer_h
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
                "visibility": row.get("Visibility"),
                "cape": row.get("CAPE"),
                "lifted_index": row.get("Lifted Index"),
                "convective_inhib": row.get("Convective Inhibition"),
                "weather_code": row.get("Weather Code"),
                "boundary_layer_h": row.get("Boundary Layer Height"),
            }
            # Convert pandas nan to None
            for k, v in clean_row.items():
                if pd.isna(v):
                    clean_row[k] = None
                    
            clean_rows.append(clean_row)
            
        cursor.executemany(sql, clean_rows)
        self.conn.commit()
        
        return cursor.rowcount

    def ingest_openmeteo_rows(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        cursor = self.conn.cursor()
        sql = """
            INSERT INTO openmeteo_forecasts (
                location, model, run_init_utc, forecast_time, lead_hours, scraped_at,
                temperature, dewpoint, humidity, pressure_msl, rain, precipitation, showers, snowfall,
                wind_speed, wind_gust, wind_dir,
                cloud_cover, cloud_cover_low, cloud_cover_mid, cloud_cover_high,
                weather_code, visibility, cape, lifted_index, convective_inhib, boundary_layer_h,
                sunshine_duration, shortwave_radiation, direct_radiation, diffuse_radiation,
                precipitation_probability, soil_temperature_0_to_7cm, soil_moisture_0_to_7cm
            ) VALUES (
                :location, :model, :run_init_utc, :forecast_time, :lead_hours, :scraped_at,
                :temperature, :dewpoint, :humidity, :pressure_msl, :rain, :precipitation, :showers, :snowfall,
                :wind_speed, :wind_gust, :wind_dir,
                :cloud_cover, :cloud_cover_low, :cloud_cover_mid, :cloud_cover_high,
                :weather_code, :visibility, :cape, :lifted_index, :convective_inhib, :boundary_layer_h,
                :sunshine_duration, :shortwave_radiation, :direct_radiation, :diffuse_radiation,
                :precipitation_probability, :soil_temperature_0_to_7cm, :soil_moisture_0_to_7cm
            )
            ON CONFLICT(location, model, run_init_utc, forecast_time) DO UPDATE SET
                lead_hours=excluded.lead_hours,
                scraped_at=excluded.scraped_at,
                temperature=excluded.temperature,
                dewpoint=excluded.dewpoint,
                humidity=excluded.humidity,
                pressure_msl=excluded.pressure_msl,
                rain=excluded.rain,
                precipitation=excluded.precipitation,
                showers=excluded.showers,
                snowfall=excluded.snowfall,
                wind_speed=excluded.wind_speed,
                wind_gust=excluded.wind_gust,
                wind_dir=excluded.wind_dir,
                cloud_cover=excluded.cloud_cover,
                cloud_cover_low=excluded.cloud_cover_low,
                cloud_cover_mid=excluded.cloud_cover_mid,
                cloud_cover_high=excluded.cloud_cover_high,
                weather_code=excluded.weather_code,
                visibility=excluded.visibility,
                cape=excluded.cape,
                lifted_index=excluded.lifted_index,
                convective_inhib=excluded.convective_inhib,
                boundary_layer_h=excluded.boundary_layer_h,
                sunshine_duration=excluded.sunshine_duration,
                shortwave_radiation=excluded.shortwave_radiation,
                direct_radiation=excluded.direct_radiation,
                diffuse_radiation=excluded.diffuse_radiation,
                precipitation_probability=excluded.precipitation_probability,
                soil_temperature_0_to_7cm=excluded.soil_temperature_0_to_7cm,
                soil_moisture_0_to_7cm=excluded.soil_moisture_0_to_7cm
        """
        clean_rows = []
        for row in rows:
            clean = {k: row.get(k) for k in [
                "location", "model", "run_init_utc", "forecast_time", "lead_hours", "scraped_at",
                "temperature", "dewpoint", "humidity", "pressure_msl", "rain", "precipitation", "showers", "snowfall",
                "wind_speed", "wind_gust", "wind_dir", "cloud_cover", "cloud_cover_low",
                "cloud_cover_mid", "cloud_cover_high", "weather_code", "visibility",
                "cape", "lifted_index", "convective_inhib", "boundary_layer_h",
                "sunshine_duration", "shortwave_radiation", "direct_radiation", "diffuse_radiation",
                "precipitation_probability", "soil_temperature_0_to_7cm", "soil_moisture_0_to_7cm"
            ]}
            clean["location"] = clean.get("location") or LOCATION_NAME
            for key, value in list(clean.items()):
                if pd.isna(value):
                    clean[key] = None
            clean_rows.append(clean)

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
            
        # Return long format directly to preserve model-specific run_init_utc for lead-time calculation.
        # Verification has migrated to Open-Meteo; the old meteologix_forecasts
        # table is retained only as a compatibility archive for earlier exports.
        f_col_sql = "pressure_msl" if f_col == "pressure" else f_col
        query = f"""
            SELECT 
                f.forecast_time as Datetime,
                f.model as Model,
                f.run_init_utc as Run_Init_UTC,
                f.{f_col_sql} as forecast,
                o.{o_col} as obs
            FROM openmeteo_forecasts f
            INNER JOIN awos_observations o 
                ON f.location = o.location 
                AND f.forecast_time = o.obs_time
            WHERE f.forecast_time >= ? AND f.forecast_time <= ?
              AND f.run_init_utc = 'historical_forecast_api'
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
            INSERT INTO awos_observations (
                location, obs_time, temperature, dewpoint, humidity,
                pressure, wind_dir, wind_speed, rain_1h
            ) VALUES (
                :location, :obs_time, :temperature, :dewpoint, :humidity,
                :pressure, :wind_dir, :wind_speed, :rain_1h
            )
            ON CONFLICT(location, obs_time) DO UPDATE SET
                temperature=excluded.temperature,
                dewpoint=excluded.dewpoint,
                humidity=excluded.humidity,
                pressure=excluded.pressure,
                wind_dir=excluded.wind_dir,
                wind_speed=excluded.wind_speed,
                rain_1h=excluded.rain_1h
        """
        
        total_inserted = 0
        location = LOCATION_NAME
        
        for f in files:
            try:
                df = read_hourly_awos(f)
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
                        "temperature": clean_val(row["Temp"]),
                        "dewpoint": clean_val(row["Dewp"]),
                        "humidity": clean_val(row["RH"], 1.0),
                        "pressure": clean_val(row["QFF"]),
                        "wind_dir": clean_val(row["WD"], 1.0),
                        "wind_speed": clean_val(row["WS"], 1.0),
                        "rain_1h": clean_val(row["Rain"])
                    })
                    
                cursor.executemany(sql, rows_to_insert)
                total_inserted += cursor.rowcount
            except Exception as e:
                print(f"Error parsing AWOS file {f}: {e}")
                
        self.conn.commit()
        return total_inserted

    def close(self):
        self.conn.close()
