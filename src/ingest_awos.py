import os
import sqlite3
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("awos_ingest")
LOCATION_NAME = "Bandara_Sangia_Ni_Bandera"

def ingest_latest_awos():
    _HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    awos_file = os.path.join(_HERE, "data", "raw_obs", "latest.dat")
    db_path = os.path.join(_HERE, "wawp_forecasts.db")
    
    if not os.path.exists(awos_file):
        log.warning(f"No AWOS file found at {awos_file}")
        return

    try:
        df = pd.read_csv(
            awos_file,
            sep=r"\s+",
            skiprows=4,
            header=None,
            usecols=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            names=["Date", "Hour", "QFE", "QFF", "Temp", "Dewp", "RH", "WD", "WS", "Rain"],
            encoding="utf-8"
        )
    except Exception as e:
        log.error(f"Failed to parse {awos_file}: {e}")
        return

    df["QFE"] = pd.to_numeric(df["QFE"], errors="coerce") / 10.0
    df["QFF"] = pd.to_numeric(df["QFF"], errors="coerce") / 10.0
    df["Temp"] = pd.to_numeric(df["Temp"], errors="coerce") / 10.0
    df["Dewp"] = pd.to_numeric(df["Dewp"], errors="coerce") / 10.0
    df["RH"] = pd.to_numeric(df["RH"], errors="coerce")
    df["Rain"] = pd.to_numeric(df["Rain"], errors="coerce") / 10.0
    df["WS"] = pd.to_numeric(df["WS"], errors="coerce")
    df["WD"] = pd.to_numeric(df["WD"], errors="coerce")

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

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE awos_observations SET location = ? WHERE location = 'WAWP'", (LOCATION_NAME,))

            inserted = 0
            updated = 0
            for _, row in df.iterrows():
                obs_time = row["UTC"].strftime("%Y-%m-%d %H:%M:%S")
                pressure = row["QFF"] if pd.notna(row["QFF"]) else None
                temp = row["Temp"] if pd.notna(row["Temp"]) else None
                dew = row["Dewp"] if pd.notna(row["Dewp"]) else None
                rh = row["RH"] if pd.notna(row["RH"]) else None
                wd = row["WD"] if pd.notna(row["WD"]) else None
                ws = row["WS"] if pd.notna(row["WS"]) else 0.0
                rain = row["Rain"] if pd.notna(row["Rain"]) else 0.0

                cursor.execute(
                    "SELECT id FROM awos_observations WHERE location = ? AND obs_time = ?",
                    (LOCATION_NAME, obs_time),
                )
                existing = cursor.fetchone()

                if existing:
                    cursor.execute("""
                        UPDATE awos_observations
                        SET temperature = ?, dewpoint = ?, humidity = ?, pressure = ?,
                            wind_dir = ?, wind_speed = ?, rain_1h = ?
                        WHERE id = ?
                    """, (temp, dew, rh, pressure, wd, ws, rain, existing[0]))
                    updated += 1
                else:
                    cursor.execute("""
                        INSERT INTO awos_observations
                        (location, obs_time, temperature, dewpoint, humidity, pressure, wind_dir, wind_speed, rain_1h)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (LOCATION_NAME, obs_time, temp, dew, rh, pressure, wd, ws, rain))
                    inserted += 1

            conn.commit()
            log.info(f"Ingested AWOS data: {inserted} inserted, {updated} updated.")
    except Exception as e:
        log.error(f"AWOS DB operation failed: {e}")
        raise

if __name__ == "__main__":
    ingest_latest_awos()
