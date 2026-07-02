import os
import sqlite3
import pandas as pd
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("awos_ingest")

def ingest_latest_awos():
    _HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    awos_file = os.path.join(_HERE, "data", "raw_obs", "latest.dat")
    db_path = os.path.join(_HERE, "wawp_forecasts.db")
    
    if not os.path.exists(awos_file):
        log.warning(f"No AWOS file found at {awos_file}")
        return

    # Parse .dat file based on legacy format
    try:
        df = pd.read_csv(
            awos_file,
            sep=r"\s+",
            skiprows=4,
            header=None,
            usecols=[1, 2, 4, 5, 6, 7, 8, 9, 12],
            names=["Date", "Hour", "Pressure", "Temp", "Dew", "RH", "WD", "WS", "Rain"],
            encoding="utf-8"
        )
    except Exception as e:
        log.error(f"Failed to parse {awos_file}: {e}")
        return

    # Unit scaling
    # Unit scaling
    df["Pressure"] = pd.to_numeric(df["Pressure"], errors="coerce") / 10.0
    df["Temp"] = pd.to_numeric(df["Temp"], errors="coerce") / 10.0
    df["Dew"]  = pd.to_numeric(df["Dew"], errors="coerce") / 10.0
    df["RH"]   = pd.to_numeric(df["RH"], errors="coerce")
    df["Rain"] = pd.to_numeric(df["Rain"], errors="coerce") / 10.0
    df["WS"]   = pd.to_numeric(df["WS"],   errors="coerce")   # knots
    df["WD"]   = pd.to_numeric(df["WD"],   errors="coerce")   # degrees

    # Build ISO8601 UTC timestamp
    df["UTC"] = pd.to_datetime(
        df["Date"].astype(str) + df["Hour"].astype(str).str.zfill(2),
        format="%Y%m%d%H",
        errors="coerce"
    )
    
    df = df.dropna(subset=["UTC"])
    if df.empty:
        log.warning("No valid timestamps found in AWOS file.")
        return

    # Connect to DB
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
    
        # Fix any previously mislabeled locations
        cursor.execute("UPDATE awos_observations SET location = 'Bandara_Sangia_Ni_Bandera' WHERE location = 'WAWP'")
    
        inserted = 0
        updated = 0
        
        for _, row in df.iterrows():
            obs_time = row["UTC"].strftime("%Y-%m-%d %H:%M:%S")
            pressure = row["Pressure"] if pd.notna(row["Pressure"]) else None
            temp = row["Temp"] if pd.notna(row["Temp"]) else None
            dew = row["Dew"] if pd.notna(row["Dew"]) else None
            rh = row["RH"] if pd.notna(row["RH"]) else None
            wd = row["WD"] if pd.notna(row["WD"]) else None
            ws = row["WS"] if pd.notna(row["WS"]) else 0.0
            rain = row["Rain"] if pd.notna(row["Rain"]) else 0.0
            
            # Check if exists
            cursor.execute("SELECT id FROM awos_observations WHERE location = 'Bandara_Sangia_Ni_Bandera' AND obs_time = ?", (obs_time,))
            existing = cursor.fetchone()
            
            if existing:
                # Update
                cursor.execute("""
                    UPDATE awos_observations
                    SET temperature = ?, dewpoint = ?, humidity = ?, pressure = ?, wind_dir = ?, wind_speed = ?, rain_1h = ?
                    WHERE id = ?
                """, (temp, dew, rh, pressure, wd, ws, rain, existing[0]))
                updated += 1
            else:
                # Insert
                cursor.execute("""
                    INSERT INTO awos_observations (location, obs_time, temperature, dewpoint, humidity, pressure, wind_dir, wind_speed, rain_1h)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, ("Bandara_Sangia_Ni_Bandera", obs_time, temp, dew, rh, pressure, wd, ws, rain))
                inserted += 1
    
        conn.commit()
        log.info(f"Ingested AWOS data: {inserted} inserted, {updated} updated.")

if __name__ == "__main__":
    ingest_latest_awos()
