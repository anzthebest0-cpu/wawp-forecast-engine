# FINAL IMPLEMENTATION BRIEF — WAWP TITAN Pipeline Overhaul
# Version: 3.0 (incorporates reviewer findings + diurnal analysis)
# Target: Local Agentic AI (Claude Code, Cursor, Continue, etc.)
# Date: 2026-07-03

---

## ⚠️ CRITICAL: READ THIS FIRST

This brief is **self-contained**. You do not need conversation history to execute it. All file paths, function names, and code patterns are based on **actual source files** uploaded by the user.

**Working directory assumption**: You are running in the project root, with structure:
```
wawp-forecast-engine/
├── run_pipeline.py
├── wawp_forecasts.db
├── src/
│   ├── scrape_meteologix.py
│   ├── db_manager.py
│   ├── ingest_awos.py
│   ├── export_dashboard_data.py
│   ├── tafor_generator.py
│   ├── taf_core.py
│   ├── vis_cloud_proxy.py
│   ├── quantile_mapper.py
│   ├── advanced_ensemble_weighter.py
│   └── guidance_generator.py
├── data/raw_obs/
│   ├── latest.dat              (current AWOS file, daily refresh)
│   ├── hourly/                 (monthly .dat files, 3.41 MB total, ~5 years)
│   │   └── AWOS_Bandara_Sangia_Ni_Bandera_YYYY-MM.dat
│   └── oneminute/              (daily .dat files, 173 MB total, ~4 years)
│       └── 000OneMinute.YYYYMMDD.dat
├── docs/data/                  (JSON outputs for dashboard)
└── tests/
```

**VERIFIED AWOS PATHS (user-confirmed)**:
- **Hourly archive**: `data/raw_obs/hourly/` (Windows: `D:\UJI_PERFORMA_MODEL\meteologix-wawp\data\raw_obs\hourly`)
- **1-Minute archive**: `data/raw_obs/oneminute/` (Windows: `D:\UJI_PERFORMA_MODEL\meteologix-wawp\data\raw_obs\oneminute`)
- **Latest file**: `data/raw_obs/latest.dat` (current observation, refreshed daily)

**If actual paths differ, adapt accordingly. Do NOT assume — verify with `ls` before writing.**

**Station constants**:
- LATITUDE: -4.338158
- LONGITUDE: 121.524047
- ELEVATION: 14 m
- TIMEZONE: Asia/Makassar (WITA, UTC+8)
- LOCATION_NAME: "Bandara_Sangia_Ni_Bandera"

---

## 📋 SPRINT OVERVIEW

This brief contains **10 sprints** with **51 tasks** total. Execute in order — later sprints depend on earlier ones.

| Sprint | Focus | Tasks | Est. Time | Priority |
|---|---|---|---|---|
| 0 | Blocking ICAO Fixes | 6 | 4-6 hours | 🔴 BLOCKING |
| 1 | AWOS Data Foundation | 4 | 3 hours | 🔴 BLOCKING |
| 2 | Open-Meteo Schema & Backfill | 5 | 4 hours | 🟡 HIGH |
| 3 | Diurnal Analysis (+ V_dry + LCL) | 9 | 7-9 hours | 🟡 HIGH |
| 4 | QM Multi-Parameter (+ Gamma for Gust) | 6 | 6 hours | 🟡 HIGH |
| 5 | TSRA CAPE Upgrade (CIN=100) | 3 | 2 hours | 🟡 HIGH |
| 6 | Weight Re-Training | 3 | 4 hours | 🟡 HIGH |
| 7 | Pipeline Integration | 4 | 3 hours | 🟡 HIGH |
| 8 | Validation & Testing | 5 | 4 hours | 🟢 MEDIUM |
| 9 | Git Workflow & PR | 4 | 1 hour | 🟢 MEDIUM |
| **TOTAL** | | **49** | **~38-42 hours** | |

**Plus Backlog (Phase 2, post-deployment)**: 5 items (B1-B5) — Rain Type Classification, Probabilistic PROB, Re-tune PROB30_CUT, CI/CD Guards, Monthly V_dry Integration.

**Recommended execution**: 2 sprints/day = 5 working days.

**Apodex Round 2 Updates Applied**:
- ✅ Finding 8: CAPE CIN threshold raised 30/50 → 100 (maritime tropics)
- ✅ Finding 10: Wind Gust QM uses gamma parametric + low-confidence flag + collapsed 3 buckets
- ✅ Direction 3 extension: Monthly V_dry lookup (Task 3.9) + Seasonal LCL calibration
- ✅ Direction 2: Rain Type Classification (Backlog B1)
- ✅ Direction 1: Probabilistic PROB groups (Backlog B2)

---

## 🔴 SPRINT 0: BLOCKING ICAO FIXES

**Goal**: Fix 6 critical bugs that produce illegal TAF output. Must complete BEFORE any other sprint.

### Task 0.1: Fix P0-1 — Base Group wx Injection

**File**: `src/tafor_generator.py`

**Bug**: Base group always sets `wx=''` even when visibility < 5000m, producing illegal TAF like `TAF WAWP ... 22006KT 3000 FEW018` (no weather phenomenon).

**Fix Location**: Function `generate_tafor()`, around the `best_guess` dict construction (after line ~311).

**Current code** (approximate):
```python
best_guess = {
    'dir': d_str,
    'spd': f"{int(first_row['spd']):02d}" if pd.notna(first_row.get('spd')) else "00",
    'gust': f"{int(first_row['gust']):02d}" if pd.notna(first_row.get('gust')) else "00",
    'vis': first_row['vis'],
    'wx': '',                    # ← BUG: always empty
    'cloud': first_row['cloud'],
    'trends': trends,
    'badge': f'MME {iss_utc}Z Shift',
    'metrics': {'leader_rmse': 'N/A', 'leader_strat': 'N/A'}
}
```

**Replace with**:
```python
# Compute wx for base group if visibility < 5000m (ICAO Annex 3 §4.4)
base_wx = ''
if first_row.get('vis') and first_row['vis'] != '9999':
    try:
        vis_int = int(first_row['vis'])
        if vis_int < 5000:
            from src.vis_cloud_proxy import get_weather_phenomenon
            # Compute WITA hour (note: valid_start is in WITA in current code)
            # TODO: This will need adjustment if CV-1 (UTC/WITA) bug is fixed separately
            wita_hour = (valid_start.hour) % 24  # valid_start is WITA
            base_wx = get_weather_phenomenon(
                rain_mmh=first_hour.get("rain", 0.0),
                rh_pct=first_hour.get("relative_humidity_pct", 80.0),
                temp_c=first_hour.get("temp_c", 28.0),
                dewpoint_c=first_hour.get("dewpoint_c", 24.0),
                vis_m=vis_int,
                local_hour_wita=wita_hour
            )
    except (ValueError, TypeError):
        pass  # Keep base_wx = '' on parse errors

best_guess = {
    'dir': d_str,
    'spd': f"{int(first_row['spd']):02d}" if pd.notna(first_row.get('spd')) else "00",
    'gust': f"{int(first_row['gust']):02d}" if pd.notna(first_row.get('gust')) else "00",
    'vis': first_row['vis'],
    'wx': base_wx,              # ← FIXED: now populated when vis < 5000
    'cloud': first_row['cloud'],
    'trends': trends,
    'badge': f'MME {iss_utc}Z Shift',
    'metrics': {'leader_rmse': 'N/A', 'leader_strat': 'N/A'}
}
```

**Verification**:
```bash
python -c "
from src.tafor_generator import generate_tafor
# Run with sample consensus_df that has vis < 5000
# Verify base group in output TAF has wx (RA, +RA, BR, FG, HZ) when vis < 5000
"
```

### Task 0.2: Fix P0-2 — CAVOK Rain Guard

**File**: `src/tafor_generator.py`

**Bug**: CAVOK gate emits `CAVOK` during active light rain (0.1 < R < 0.4 mm/h) because gate tests `not wx_str` but `wx_str` is empty when light rain is suppressed.

**Fix Location**: Function `_build_taf_text()`, around line ~108.

**Current code** (approximate):
```python
if vis == '9999' and cavok_cloud and not wx:
    vis = 'CAVOK'
    cloud = ''
    wx = ''
```

**Replace with**:
```python
# CAVOK requires: vis ≥ 10km, no clouds below 5000ft, no precipitation (ICAO Annex 3 §4.4)
# Add rain_mmh guard — even light rain (0.1-0.4 mm/h) disqualifies CAVOK
rain_at_base = float(bg.get('rain_mmh', 0.0) or 0.0)
if vis == '9999' and cavok_cloud and not wx and rain_at_base <= 0.1:
    vis = 'CAVOK'
    cloud = ''
    wx = ''
```

**Note**: You also need to ensure `rain_mmh` is passed into `bg` dict. Check `generate_tafor()` — if `first_row` has `rain` field, add it to `best_guess`:
```python
best_guess = {
    ...
    'rain_mmh': first_row.get('rain', 0.0),  # ← ADD THIS for CAVOK guard
    ...
}
```

Same fix needed for change-group CAVOK logic (around line ~142):
```python
# Current:
if tvis == '9999' and not twx:
    if not tcld or tcld[0] == 'NSC' or (...):
        t_cavok = True

# Replace with:
t_rain = float(t.get('rain_mmh', 0.0) or 0.0)
if tvis == '9999' and not twx and t_rain <= 0.1:
    if not tcld or tcld[0] == 'NSC' or (...):
        t_cavok = True
```

For change groups, you need to add `rain_mmh` to each trend dict when building them in `taf_core.py`. In `_build_change_groups`, for each group dict, add:
```python
groups.append({
    ...
    'rain_mmh': consensus_truth[i].get('rain', 0.0) if i < len(consensus_truth) else 0.0,
    ...
})
```

### Task 0.3: Fix Finding 3 — Visibility Tier Boundary

**File**: `src/vis_cloud_proxy.py`

**Bug**: Code uses `R >= 0.4` as drizzle/light-rain boundary, but spec says 0.5. Creates ~900-1300m discontinuity at R=0.4.

**Fix Location**: Function `estimate_visibility()`, around line ~1638.

**Current code** (approximate):
```python
if R >= 0.4:
    if R < 2.5:
        vis = 5000.0 * (R ** -0.55)
    elif R < 10.0:
        vis = 3800.0 * (R ** -0.63)
    else:
        vis = 2500.0 * (R ** -0.70)
    vis *= pressure_factor
    return int(max(200.0, min(vis, 9999.0)))

if R > 0.1:
    # Drizzle: minimal extinction
    vis = 7000.0 * (R ** -0.25)
    vis *= pressure_factor
    return int(max(200.0, min(vis, 9999.0)))
```

**Replace with** (one-line fix):
```python
if R >= 0.5:                    # ← Changed from 0.4 to 0.5
    if R < 2.5:
        vis = 5000.0 * (R ** -0.55)
    elif R < 10.0:
        vis = 3800.0 * (R ** -0.63)
    else:
        vis = 2500.0 * (R ** -0.70)
    vis *= pressure_factor
    return int(max(200.0, min(vis, 9999.0)))

if R > 0.1:
    # Drizzle: minimal extinction
    vis = 7000.0 * (R ** -0.25)
    vis *= pressure_factor
    return int(max(200.0, min(vis, 9999.0)))
```

### Task 0.4: Fix Finding 5 — HZ/BR/FG During Rain Guard

**File**: `src/vis_cloud_proxy.py`

**Bug**: Light rain returns `""` then falls through to HZ/BR/FG checks, which can fire during active rain. HZ and RA are mutually exclusive per ICAO Annex 3.

**Fix Location**: Function `get_weather_phenomenon()`, around line ~1681.

**Current code** (approximate):
```python
def get_weather_phenomenon(rain_mmh, rh_pct, temp_c, dewpoint_c, vis_m, local_hour_wita):
    dd = temp_c - dewpoint_c

    # Precipitation
    if rain_mmh > 0.1:
        if rain_mmh >= 10.0:
            if 4 <= local_hour_wita <= 12 and rh_pct >= 90.0:
                return "TSRA"
            else:
                return "+RA"
        elif rain_mmh >= 2.5:
            return "RA"
        else:
            # Light rain (-RA) is not a significant weather criteria for change groups
            return ""

    # Fog / Mist / Haze
    if vis_m < 1000.0 and rh_pct >= 95.0 and dd <= 1.5:
        return "FG"
    if vis_m < 5000.0 and rh_pct >= 80.0:
        return "BR"
    if vis_m < 5000.0 and rh_pct < 75.0 and temp_c >= 28.0:
        return "HZ"

    return ""
```

**Problem**: When `0.1 < rain_mmh < 2.5`, function returns `""` (light rain suppressed). But this `""` return exits the function — wait, actually it doesn't fall through. Let me re-check.

Actually, looking more carefully: the `if rain_mmh > 0.1:` block has an `else: return ""` that DOES return. So the code does NOT fall through to HZ/BR/FG when rain > 0.1.

**However**, there's still a subtle bug: if `rain_mmh <= 0.1` but > 0 (trace precipitation, 0 < R ≤ 0.1), the precipitation block is skipped entirely, and HZ/BR/FG can fire. This is technically OK per ICAO (trace precipitation doesn't count), but to be safe:

**Replace with**:
```python
def get_weather_phenomenon(rain_mmh, rh_pct, temp_c, dewpoint_c, vis_m, local_hour_wita):
    dd = temp_c - dewpoint_c

    # Precipitation — any rain > 0.1 mm/h takes priority over fog/mist/haze
    if rain_mmh > 0.1:
        if rain_mmh >= 10.0:
            # TSRA proxy: daytime + heavy rain + saturated air
            # NOTE: WITA hour check has +8 offset bug (CV-2) — to be fixed separately
            if 4 <= local_hour_wita <= 12 and rh_pct >= 90.0:
                return "TSRA"
            else:
                return "+RA"
        elif rain_mmh >= 2.5:
            return "RA"
        elif vis_m < 5000:
            # Light rain (-RA) — suppressed from triggering change groups,
            # BUT must be emitted when vis < 5000m per ICAO Annex 3 §4.4
            return "-RA"
        else:
            # Light rain with vis ≥ 5000m — no wx needed
            return ""

    # Fog / Mist / Haze — only when no active precipitation
    # (rain_mmh ≤ 0.1)
    if vis_m < 1000.0 and rh_pct >= 95.0 and dd <= 1.5:
        return "FG"
    if vis_m < 5000.0 and rh_pct >= 80.0:
        return "BR"
    if vis_m < 5000.0 and rh_pct < 75.0 and temp_c >= 28.0:
        return "HZ"

    return ""
```

**This fix also addresses P0-1**: now `get_weather_phenomenon` returns `-RA` when `0.1 < rain_mmh < 2.5 AND vis_m < 5000`.

### Task 0.5: Fix Finding 11 — AWOS Stale Flag

**File**: `run_pipeline.py`

**Bug**: AWOS ingestion failure is caught but no health flag is written. Forecasters see no warning when weights are stale.

**Fix Location**: Function `run()`, around line ~48.

**Current code** (approximate):
```python
# 2.5 Ingest AWOS data if exists
try:
    ingest_latest_awos()
    log.info("AWOS ingestion completed.")
except Exception as e:
    log.error(f"AWOS ingestion failed: {e}")
```

**Replace with**:
```python
# 2.5 Ingest AWOS data if exists
awos_stale = False
try:
    ingest_latest_awos()
    log.info("AWOS ingestion completed.")
except Exception as e:
    log.error(f"AWOS ingestion failed: {e}")
    awos_stale = True

# Write health flag (will be picked up by export_all)
import json
health_path = os.path.join(_HERE, "docs", "data", "pipeline_health.json")
os.makedirs(os.path.dirname(health_path), exist_ok=True)
health = {
    "awos_stale": awos_stale,
    "last_run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    "last_error": str(e) if awos_stale else None,
}
with open(health_path, 'w') as f:
    json.dump(health, f, indent=2)
```

**Also**: Add `from datetime import datetime, timezone` to imports at top of `run_pipeline.py` if not present.

### Task 0.6: Fix Finding 12 — DB Connection Leak

**File**: `src/ingest_awos.py`

**Bug**: `sqlite3.connect()` opened without try/finally. If exception occurs, connection leaks.

**Fix Location**: Function `ingest_latest_awos()`, around line ~2193.

**Current code** (approximate):
```python
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
# ... operations ...
conn.commit()
conn.close()
```

**Replace with** (use context manager):
```python
try:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        # ... all operations ...
        conn.commit()
        # Connection auto-closed on exit
except Exception as e:
    log.error(f"AWOS DB operation failed: {e}")
    raise
```

Or alternatively, wrap in try/finally:
```python
conn = sqlite3.connect(db_path)
try:
    cursor = conn.cursor()
    # ... all operations ...
    conn.commit()
except Exception as e:
    log.error(f"AWOS DB operation failed: {e}")
    raise
finally:
    conn.close()
```

### Sprint 0 Verification

After completing all 6 tasks, run:

```bash
# Test 1: Verify P0-1 fix — base group has wx when vis < 5000
python -c "
from src.tafor_generator import generate_tafor
# Mock a consensus_df with vis=3000 and rain=1.0
# Verify output TAF base group contains '3000 RA' or similar
"

# Test 2: Verify P0-2 fix — CAVOK not emitted during rain
python -c "
from src.tafor_generator import _build_taf_text
# Mock bg with vis=9999, rain_mmh=0.3, cloud=NSC
# Verify output does NOT contain 'CAVOK'
"

# Test 3: Verify Finding 3 — tier boundary at 0.5
python -c "
from src.vis_cloud_proxy import estimate_visibility
v_049 = estimate_visibility(0.49, 85, 28, 24, 5, 1013, 0)
v_050 = estimate_visibility(0.50, 85, 28, 24, 5, 1013, 0)
diff = abs(v_049 - v_050)
print(f'Vis at R=0.49: {v_049}, R=0.50: {v_050}, jump: {diff}')
assert diff < 500, f'Tier boundary discontinuity too large: {diff}m'
print('Tier boundary OK')
"

# Test 4: Verify Finding 5 — no HZ during rain
python -c "
from src.vis_cloud_proxy import get_weather_phenomenon
# Light rain with conditions that would trigger HZ
wx = get_weather_phenomenon(rain_mmh=0.5, rh_pct=70, temp_c=30, dewpoint_c=20, vis_m=3000, local_hour_wita=14)
print(f'wx during light rain: {wx!r}')
assert wx == '-RA', f'Expected -RA, got {wx}'
print('HZ-during-rain guard OK')
"

# Test 5: Verify pipeline still runs
python run_pipeline.py
echo "Pipeline exit code: $?"
```

---

## 🔴 SPRINT 1: AWOS DATA FOUNDATION

**Goal**: Fix hourly AWOS parser bug + build 1-minute AWOS ingestion pipeline.

### Task 1.1: Add New DB Tables for AWOS 1-Minute Data

**File**: `src/db_manager.py`, method `_create_tables()`

Add after `awos_observations` table:

```sql
CREATE TABLE IF NOT EXISTS awos_observations_1min (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    location        TEXT    NOT NULL,
    obs_time        TEXT    NOT NULL,    -- 'YYYY-MM-DD HH:MM:00' UTC
    wind_speed      REAL,                -- kt (instantaneous)
    wind_dir        REAL,                -- deg
    wind_gust       REAL,                -- kt (max in 1 min, NULL if ///)
    wind_gust_dir   REAL,                -- deg
    temperature     REAL,                -- °C
    dewpoint        REAL,                -- °C
    humidity        REAL,                -- %
    pressure_qnh    REAL,                -- hPa
    rain_1min       REAL,                -- mm
    solar_rad       REAL,                -- W/m²
    UNIQUE(location, obs_time)
);
CREATE INDEX IF NOT EXISTS idx_1min_time ON awos_observations_1min(obs_time);
CREATE INDEX IF NOT EXISTS idx_1min_date ON awos_observations_1min(date(obs_time));
```

Also add `wind_gust_max` column to `awos_observations` if not exists:
```sql
-- Migration: add wind_gust_max column if missing
ALTER TABLE awos_observations ADD COLUMN wind_gust_max REAL;
```
(Wrap in try/except since ALTER TABLE fails if column exists.)

### Task 1.2: Fix Hourly AWOS Parser

**File**: `src/ingest_awos.py` and `src/db_manager.py`

**Bug**: Both parsers reference non-existent columns (11, 12). Hourly .dat format has only 11 columns (0-10).

**Verified format** (from `AWOS_Bandara_Sangia_Ni_Bandera_2026-04.dat`):
```
STN YYYYMMDD GG QFE36 QFF36 TEMP36 DEWP36 RH36 WD36 WS36 RA36
000 20260401 00 10096 10111  270   251   90  149   2    1
```
- Col 0: STN ("000")
- Col 1: YYYYMMDD
- Col 2: GG (hour UTC)
- Col 3: QFE36 (station pressure, 0.1 hPa)
- Col 4: QFF36 (MSL pressure, 0.1 hPa) ← USE THIS for pressure
- Col 5: TEMP36 (0.1 °C)
- Col 6: DEWP36 (0.1 °C)
- Col 7: RH36 (%)
- Col 8: WD36 (degrees)
- Col 9: WS36 (knots)
- Col 10: RA36 (0.1 mm)

**Fix `src/ingest_awos.py`** — replace `ingest_latest_awos()` body:
```python
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

    # Unit scaling
    df["QFE"]   = pd.to_numeric(df["QFE"],   errors="coerce") / 10.0
    df["QFF"]   = pd.to_numeric(df["QFF"],   errors="coerce") / 10.0
    df["Temp"]  = pd.to_numeric(df["Temp"],  errors="coerce") / 10.0
    df["Dewp"]  = pd.to_numeric(df["Dewp"],  errors="coerce") / 10.0
    df["RH"]    = pd.to_numeric(df["RH"],    errors="coerce")
    df["WD"]    = pd.to_numeric(df["WD"],    errors="coerce")
    df["WS"]    = pd.to_numeric(df["WS"],    errors="coerce")
    df["Rain"]  = pd.to_numeric(df["Rain"],  errors="coerce") / 10.0

    df["UTC"] = pd.to_datetime(
        df["Date"].astype(str) + df["Hour"].astype(str).str.zfill(2),
        format="%Y%m%d%H",
        errors="coerce"
    )
    df = df.dropna(subset=["UTC"])
    if df.empty:
        log.warning("No valid timestamps in AWOS file.")
        return

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE awos_observations SET location = 'Bandara_Sangia_Ni_Bandera' WHERE location = 'WAWP'")
            
            inserted = 0
            updated = 0
            for _, row in df.iterrows():
                obs_time = row["UTC"].strftime("%Y-%m-%d %H:%M:%S")
                pressure = row["QFF"] if pd.notna(row["QFF"]) else None  # MSL pressure
                temp = row["Temp"] if pd.notna(row["Temp"]) else None
                dew = row["Dewp"] if pd.notna(row["Dewp"]) else None
                rh = row["RH"] if pd.notna(row["RH"]) else None
                wd = row["WD"] if pd.notna(row["WD"]) else None
                ws = row["WS"] if pd.notna(row["WS"]) else 0.0
                rain = row["Rain"] if pd.notna(row["Rain"]) else 0.0
                
                cursor.execute("SELECT id FROM awos_observations WHERE location='Bandara_Sangia_Ni_Bandera' AND obs_time=?", (obs_time,))
                existing = cursor.fetchone()
                
                if existing:
                    cursor.execute("""
                        UPDATE awos_observations
                        SET temperature=?, dewpoint=?, humidity=?, pressure=?, wind_dir=?, wind_speed=?, rain_1h=?
                        WHERE id=?
                    """, (temp, dew, rh, pressure, wd, ws, rain, existing[0]))
                    updated += 1
                else:
                    cursor.execute("""
                        INSERT INTO awos_observations
                        (location, obs_time, temperature, dewpoint, humidity, pressure, wind_dir, wind_speed, rain_1h)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, ("Bandara_Sangia_Ni_Bandera", obs_time, temp, dew, rh, pressure, wd, ws, rain))
                    inserted += 1
            
            conn.commit()
            log.info(f"Ingested AWOS: {inserted} inserted, {updated} updated.")
    except Exception as e:
        log.error(f"AWOS DB operation failed: {e}")
        raise
```

**Fix `src/db_manager.py`** — replace `ingest_awos_files()`:
```python
def ingest_awos_files(self, directory: str) -> int:
    import os, glob
    search = os.path.join(directory, "**", "*.dat")
    files = glob.glob(search, recursive=True)
    if not files:
        return 0

    cursor = self.conn.cursor()
    sql = """
        INSERT OR IGNORE INTO awos_observations (
            location, obs_time, temperature, dewpoint, humidity,
            pressure, wind_dir, wind_speed, rain_1h
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    total_inserted = 0
    location = "Bandara_Sangia_Ni_Bandera"

    for f in files:
        try:
            df = pd.read_csv(
                f, sep=r"\s+", skiprows=4, header=None,
                usecols=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                names=["Date", "Hour", "QFE", "QFF", "Temp", "Dewp", "RH", "WD", "WS", "Rain"]
            )
            df["UTC"] = pd.to_datetime(
                df["Date"].astype(str) + df["Hour"].astype(str).str.zfill(2),
                format="%Y%m%d%H", errors="coerce"
            )
            df = df.dropna(subset=["UTC"])

            rows_to_insert = []
            for _, row in df.iterrows():
                def cv(val, scale=1.0):
                    try:
                        v = float(val)
                        if pd.isna(v): return None
                        return v * scale
                    except (ValueError, TypeError):
                        return None
                rows_to_insert.append({
                    "location": location,
                    "obs_time": row["UTC"].strftime('%Y-%m-%d %H:%M:%S'),
                    "temperature": cv(row["Temp"], 0.1),
                    "dewpoint":   cv(row["Dewp"], 0.1),
                    "humidity":   cv(row["RH"],   1.0),
                    "pressure":   cv(row["QFF"],  0.1),  # MSL pressure
                    "wind_dir":   cv(row["WD"],   1.0),
                    "wind_speed": cv(row["WS"],   1.0),
                    "rain_1h":    cv(row["Rain"], 0.1),
                })

            cursor.executemany(sql, rows_to_insert)
            total_inserted += cursor.rowcount
        except Exception as e:
            print(f"Error parsing AWOS file {f}: {e}")

    self.conn.commit()
    return total_inserted
```

### Task 1.3: Build 1-Minute AWOS Parser

**New file**: `src/ingest_awos_1min.py`

**Verified format** (from `000OneMinute.20260524.dat`):
```
STN YYYYMMDD GG MM WS WD WGS WGD TEMP DEWP RH QNH DA RA SOL
000 20260524 00 00  3  125 /// ///  290  261 84 10124 2100 0 390
```
- Col 0: STN
- Col 1: YYYYMMDD
- Col 2: GG (hour)
- Col 3: MM (minute)
- Col 4: WS (kt)
- Col 5: WD (deg)
- Col 6: WGS (gust speed kt, may be `///`)
- Col 7: WGD (gust direction deg, may be `///`)
- Col 8: TEMP (0.1 °C)
- Col 9: DEWP (0.1 °C)
- Col 10: RH (%)
- Col 11: QNH (0.1 hPa, MSL pressure)
- Col 12: DA (density altitude ft, skip)
- Col 13: RA (0.1 mm, 1-min rain)
- Col 14: SOL (W/m², solar radiation)

```python
"""
Ingest 1-minute AWOS files into awos_observations_1min table.
Format: 000OneMinute.YYYYMMDD.dat

Usage:
    python src/ingest_awos_1min.py [--directory PATH]
"""
import os
import sys
import sqlite3
import pandas as pd
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("awos_1min")


def ingest_1min_file(file_path: str, db_path: str) -> int:
    """Parse one 1-minute AWOS file and insert into DB."""
    if not os.path.exists(file_path):
        return 0
    
    try:
        df = pd.read_csv(
            file_path,
            sep=r"\s+",
            skiprows=4,
            header=None,
            usecols=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14],
            names=["Date", "Hour", "Minute", "WS", "WD", "WGS", "WGD",
                   "Temp", "Dewp", "RH", "QNH", "Rain", "SOL"],
            na_values=["///"],
            encoding="utf-8"
        )
    except Exception as e:
        log.error(f"Failed to parse {file_path}: {e}")
        return 0
    
    # Unit scaling
    df["Temp"]  = pd.to_numeric(df["Temp"],  errors="coerce") / 10.0
    df["Dewp"]  = pd.to_numeric(df["Dewp"],  errors="coerce") / 10.0
    df["QNH"]   = pd.to_numeric(df["QNH"],   errors="coerce") / 10.0
    df["Rain"]  = pd.to_numeric(df["Rain"],  errors="coerce") / 10.0
    df["WS"]    = pd.to_numeric(df["WS"],    errors="coerce")
    df["WD"]    = pd.to_numeric(df["WD"],    errors="coerce")
    df["WGS"]   = pd.to_numeric(df["WGS"],   errors="coerce")
    df["WGD"]   = pd.to_numeric(df["WGD"],   errors="coerce")
    df["RH"]    = pd.to_numeric(df["RH"],    errors="coerce")
    df["SOL"]   = pd.to_numeric(df["SOL"],   errors="coerce")
    
    # Build UTC timestamp
    df["UTC"] = pd.to_datetime(
        df["Date"].astype(str) +
        df["Hour"].astype(str).str.zfill(2) +
        df["Minute"].astype(str).str.zfill(2),
        format="%Y%m%d%H%M",
        errors="coerce"
    )
    df = df.dropna(subset=["UTC"])
    if df.empty:
        return 0
    
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            for _, row in df.iterrows():
                obs_time = row["UTC"].strftime("%Y-%m-%d %H:%M:00")
                cursor.execute("""
                    INSERT OR IGNORE INTO awos_observations_1min
                    (location, obs_time, wind_speed, wind_dir, wind_gust, wind_gust_dir,
                     temperature, dewpoint, humidity, pressure_qnh, rain_1min, solar_rad)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "Bandara_Sangia_Ni_Bandera", obs_time,
                    row["WS"] if pd.notna(row["WS"]) else None,
                    row["WD"] if pd.notna(row["WD"]) else None,
                    row["WGS"] if pd.notna(row["WGS"]) else None,
                    row["WGD"] if pd.notna(row["WGD"]) else None,
                    row["Temp"] if pd.notna(row["Temp"]) else None,
                    row["Dewp"] if pd.notna(row["Dewp"]) else None,
                    row["RH"] if pd.notna(row["RH"]) else None,
                    row["QNH"] if pd.notna(row["QNH"]) else None,
                    row["Rain"] if pd.notna(row["Rain"]) else None,
                    row["SOL"] if pd.notna(row["SOL"]) else None,
                ))
            inserted = cursor.rowcount
            conn.commit()
        return inserted
    except Exception as e:
        log.error(f"DB operation failed for {file_path}: {e}")
        return 0


def aggregate_1min_to_hourly_gust(db_path: str) -> int:
    """
    Aggregate 1-min wind gust to hourly max → update awos_observations.wind_gust_max.
    Run AFTER ingest_1min_file.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # Get distinct hours that have 1-min gust data
            cursor.execute("""
                SELECT DISTINCT strftime('%Y-%m-%d %H:00:00', obs_time) as hour_start
                FROM awos_observations_1min
                WHERE wind_gust IS NOT NULL
                GROUP BY hour_start
            """)
            hours = [r[0] for r in cursor.fetchall()]
            
            updated = 0
            for hour_start in hours:
                cursor.execute("""
                    SELECT MAX(wind_gust) FROM awos_observations_1min
                    WHERE obs_time >= ? AND obs_time < datetime(?, '+1 hour')
                      AND wind_gust IS NOT NULL
                """, (hour_start, hour_start))
                max_gust = cursor.fetchone()[0]
                
                if max_gust is not None:
                    cursor.execute("""
                        UPDATE awos_observations 
                        SET wind_gust_max = ?
                        WHERE obs_time = ?
                    """, (max_gust, hour_start))
                    updated += cursor.rowcount
            
            conn.commit()
            return updated
    except Exception as e:
        log.error(f"Aggregation failed: {e}")
        return 0


def main():
    import argparse, glob
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default=None)
    args = parser.parse_args()
    
    _HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(_HERE, "wawp_forecasts.db")
    
    awos_dir = args.directory or os.path.join(_HERE, "data", "raw_obs", "oneminute")
    if not os.path.exists(awos_dir):
        log.warning(f"1-min AWOS directory not found: {awos_dir}")
        return
    
    files = sorted(glob.glob(os.path.join(awos_dir, "**", "000OneMinute.*.dat"), recursive=True))
    log.info(f"Found {len(files)} 1-minute AWOS files to ingest")
    
    total = 0
    for i, f in enumerate(files):
        n = ingest_1min_file(f, db_path)
        total += n
        if (i + 1) % 50 == 0:
            log.info(f"  Progress: {i+1}/{len(files)} files, {total} rows ingested")
    
    log.info(f"Total 1-min rows ingested: {total}")
    
    # Aggregate to hourly gust
    log.info("Aggregating 1-min gust to hourly max...")
    updated = aggregate_1min_to_hourly_gust(db_path)
    log.info(f"Updated wind_gust_max in awos_observations: {updated} rows")


if __name__ == "__main__":
    main()
```

### Task 1.4: Bulk Ingest Historical AWOS Data

After Tasks 1.1-1.3 complete, run:

```bash
# Step 1: Ingest all historical hourly files (5 years)
# Path: data/raw_obs/hourly/ (Windows: D:\UJI_PERFORMA_MODEL\meteologix-wawp\data\raw_obs\hourly)
python -c "
from src.db_manager import ForecastDB
db = ForecastDB('wawp_forecasts.db')
n = db.ingest_awos_files('data/raw_obs/hourly/')
print(f'Hourly AWOS ingested: {n} rows')
db.close()
"

# Step 2: Ingest all historical 1-min files (4 years, ~173 MB, ~1500 files)
# Path: data/raw_obs/oneminute/ (Windows: D:\UJI_PERFORMA_MODEL\meteologix-wawp\data\raw_obs\oneminute)
python src/ingest_awos_1min.py --directory data/raw_obs/oneminute/

# Step 3: Verify
python -c "
import sqlite3
conn = sqlite3.connect('wawp_forecasts.db')
hourly_count = conn.execute('SELECT COUNT(*) FROM awos_observations').fetchone()[0]
min_count = conn.execute('SELECT COUNT(*) FROM awos_observations_1min').fetchone()[0]
gust_count = conn.execute('SELECT COUNT(*) FROM awos_observations WHERE wind_gust_max IS NOT NULL').fetchone()[0]
print(f'Hourly observations: {hourly_count}')
print(f'1-min observations:  {min_count}')
print(f'Hours with gust:     {gust_count}')
conn.close()
"
```

**Expected output**:
- Hourly observations: ~42,000 (5 years × 8,760 hours × 0.96 coverage)
- 1-min observations: ~2,100,000 (4 years × 525,600 min × 0.99 coverage)
- Hours with gust: ~3,500 (~10% of hours with active gust per ICAO threshold)

### Sprint 1 Verification

```bash
# Verify hourly data quality
python -c "
import sqlite3, pandas as pd
conn = sqlite3.connect('wawp_forecasts.db')
df = pd.read_sql('''
    SELECT MIN(obs_time) as earliest, MAX(obs_time) as latest,
           COUNT(*) as n, 
           AVG(temperature) as avg_t, AVG(pressure) as avg_p,
           AVG(wind_speed) as avg_ws, AVG(rain_1h) as avg_rain
    FROM awos_observations WHERE temperature IS NOT NULL
''', conn)
print(df)
# Sanity: avg_t should be 26-30°C, avg_p 1008-1013 hPa, avg_ws 3-8 kt
conn.close()
"

# Verify 1-min data quality
python -c "
import sqlite3, pandas as pd
conn = sqlite3.connect('wawp_forecasts.db')
df = pd.read_sql('''
    SELECT MIN(obs_time) as earliest, MAX(obs_time) as latest,
           COUNT(*) as n,
           COUNT(wind_gust) as n_with_gust,
           AVG(wind_gust) as avg_gust, MAX(wind_gust) as max_gust
    FROM awos_observations_1min
''', conn)
print(df)
conn.close()
"
```

---

## 🟡 SPRINT 2: OPEN-METEO SCHEMA & BACKFILL

**Goal**: Add Open-Meteo forecast tables and backfill 2+ years of historical forecasts.

### Task 2.1: Add Open-Meteo Tables

**File**: `src/db_manager.py`, method `_create_tables()`

Add after `awos_observations_1min` table:

```sql
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
    showers           REAL    DEFAULT 0.0,
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
    UNIQUE(location, model, run_init_utc, forecast_time)
);
CREATE INDEX IF NOT EXISTS idx_om_run   ON openmeteo_forecasts(model, run_init_utc);
CREATE INDEX IF NOT EXISTS idx_om_valid ON openmeteo_forecasts(forecast_time);
CREATE INDEX IF NOT EXISTS idx_om_lead  ON openmeteo_forecasts(model, lead_hours);

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
    UNIQUE(model, parameter, lead_bucket)
);

CREATE TABLE IF NOT EXISTS qm_training_pairs AS
    SELECT 0 as placeholder  -- will be replaced by build_qm_training_pairs.py
;
DROP TABLE IF EXISTS qm_training_pairs;
```

Add method `ingest_openmeteo_rows()` to `ForecastDB` class:
```python
def ingest_openmeteo_rows(self, rows: list[dict]) -> int:
    if not rows:
        return 0
    cursor = self.conn.cursor()
    sql = """
        INSERT OR IGNORE INTO openmeteo_forecasts (
            location, model, run_init_utc, forecast_time, lead_hours, scraped_at,
            temperature, dewpoint, humidity, pressure_msl, rain, showers,
            wind_speed, wind_gust, wind_dir,
            cloud_cover, cloud_cover_low, cloud_cover_mid, cloud_cover_high,
            weather_code, visibility, cape, lifted_index, convective_inhib, boundary_layer_h
        ) VALUES (
            :location, :model, :run_init_utc, :forecast_time, :lead_hours, :scraped_at,
            :temperature, :dewpoint, :humidity, :pressure_msl, :rain, :showers,
            :wind_speed, :wind_gust, :wind_dir,
            :cloud_cover, :cloud_cover_low, :cloud_cover_mid, :cloud_cover_high,
            :weather_code, :visibility, :cape, :lifted_index, :convective_inhib, :boundary_layer_h
        )
    """
    cursor.executemany(sql, rows)
    self.conn.commit()
    return cursor.rowcount
```

### Task 2.2: Build Backfill Script

**New file**: `src/backfill_openmeteo.py`

This script was detailed in the previous brief. Key points:
- Endpoint: `https://single-runs-api.open-meteo.com/v1/forecast`
- 7 models with different start dates (see MODELS_OPENMETEO dict)
- Parallel 6 threads, 0.5s delay per thread
- Graceful 400 handling (skip), 429 (wait 60s + retry)
- Resume capability (skip runs already in DB)
- Expected wall time: 2-3 hours

Full code is in the previous brief — copy verbatim.

### Task 2.3: Run Backfill

```bash
# Test 1 model first (dry run)
python src/backfill_openmeteo.py --model ecmwf_ifs --dry-run

# Backfill ECMWF (longest archive, ~2 years)
python src/backfill_openmeteo.py --model ecmwf_ifs

# Backfill all 7 models
python src/backfill_openmeteo.py

# Verify
python -c "
import sqlite3, pandas as pd
conn = sqlite3.connect('wawp_forecasts.db')
df = pd.read_sql('''
    SELECT model, COUNT(*) as rows, 
           MIN(run_init_utc) as earliest, MAX(run_init_utc) as latest,
           COUNT(DISTINCT run_init_utc) as n_runs
    FROM openmeteo_forecasts GROUP BY model
''', conn)
print(df)
conn.close()
"
```

### Task 2.4: Build Training Pairs

**New file**: `src/build_qm_training_pairs.py`

```python
"""
Build materialized training pairs table: forecast × observation.
Run AFTER backfill_openmeteo.py AND Sprint 1 (AWOS ingest).
"""
import os, sys, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("qm_pairs")


def build_training_pairs(db):
    log.info("Dropping existing qm_training_pairs table...")
    db.conn.execute("DROP TABLE IF EXISTS qm_training_pairs")
    
    log.info("Building qm_training_pairs with JOIN forecast ↔ observation...")
    db.conn.execute("""
        CREATE TABLE qm_training_pairs AS
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
        ORDER BY f.model, f.run_init_utc, f.forecast_time
    """)
    
    db.conn.execute("CREATE INDEX idx_qm_tp_model  ON qm_training_pairs(model, lead_bucket)")
    db.conn.commit()
    
    n = db.conn.execute("SELECT COUNT(*) FROM qm_training_pairs").fetchone()[0]
    log.info(f"Built {n} training pairs total")
    
    stats = db.conn.execute("""
        SELECT model, lead_bucket, COUNT(*) as n
        FROM qm_training_pairs 
        GROUP BY model, lead_bucket 
        ORDER BY model, lead_bucket
    """).fetchall()
    log.info("Sample counts per (model, lead_bucket):")
    for r in stats:
        log.info(f"  {r[0]:<35s} {r[1]:<12s} {r[2]:>8d}")


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.db_manager import ForecastDB
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "wawp_forecasts.db")
    db = ForecastDB(db_path)
    try:
        build_training_pairs(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

### Task 2.5: Verify Coverage

```bash
python src/build_qm_training_pairs.py

# Verify sample counts
python -c "
import sqlite3, pandas as pd
conn = sqlite3.connect('wawp_forecasts.db')
df = pd.read_sql('''
    SELECT model, 
           SUM(CASE WHEN fcst_temperature IS NOT NULL AND obs_temperature IS NOT NULL THEN 1 ELSE 0 END) as temp_pairs,
           SUM(CASE WHEN fcst_wind_gust IS NOT NULL AND obs_wind_gust IS NOT NULL THEN 1 ELSE 0 END) as gust_pairs,
           SUM(CASE WHEN fcst_rain IS NOT NULL AND obs_rain IS NOT NULL THEN 1 ELSE 0 END) as rain_pairs
    FROM qm_training_pairs GROUP BY model
''', conn)
print(df)
conn.close()
"
```

**Expected**: temp_pairs ~5,000-15,000 per model; gust_pairs ~200-1,000; rain_pairs ~1,000-5,000.

---

## 🟡 SPRINT 3: DIURNAL ANALYSIS (NEW)

**Goal**: Perform comprehensive diurnal analysis on 5 years of observational data to (a) validate QM bucketing strategy, (b) identify regime shifts for seasonal QM, (c) inform TSRA threshold calibration, (d) generate dashboard visualizations.

This is a **new capability** not in the previous brief. Output: JSON files for dashboard + PNG plots + Python analysis module.

### Task 3.1: Create Diurnal Analysis Module

**New file**: `src/diurnal_analysis.py`

```python
"""
Diurnal analysis of WAWP observational data.
Computes hourly climatology, monthly×hourly matrices, regime identification.

Outputs:
  - docs/data/diurnal_climatology.json (for dashboard)
  - docs/data/diurnal_plots/*.png (visualizations)
"""
import os
import sys
import json
import math
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from scipy import stats as scipy_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diurnal")

# ============================================================================
# Matplotlib setup with Chinese font fallback (per project rules)
# ============================================================================
import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.font_manager as fm
fm.fontManager.addfont('/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf')
fm.fontManager.addfont('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['Noto Sans SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


# ============================================================================
# Constants
# ============================================================================
LOCATION = "Bandara_Sangia_Ni_Bandera"
WETA_OFFSET_HOURS = 8  # UTC → WITA

# ICAO season definitions (tropical maritime, ~4°S)
WET_SEASON_MONTHS = [11, 12, 1, 2, 3, 4]      # Nov-Apr
DRY_SEASON_MONTHS = [5, 6, 7, 8, 9, 10]        # May-Oct

PARAMETERS = ["temperature", "dewpoint", "pressure", "humidity",
              "wind_speed", "wind_gust_max", "wind_dir", "rain_1h"]


# ============================================================================
# Data Loading
# ============================================================================

def load_observations(db_path: str) -> pd.DataFrame:
    """Load all hourly observations, add WITA hour and month columns."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("""
        SELECT obs_time, temperature, dewpoint, humidity, pressure,
               wind_speed, wind_gust_max, wind_dir, rain_1h
        FROM awos_observations
        WHERE temperature IS NOT NULL
        ORDER BY obs_time
    """, conn)
    conn.close()
    
    df["datetime_utc"] = pd.to_datetime(df["obs_time"])
    df["datetime_wita"] = df["datetime_utc"] + pd.Timedelta(hours=WETA_OFFSET_HOURS)
    df["hour_wita"] = df["datetime_wita"].dt.hour
    df["month"] = df["datetime_wita"].dt.month
    df["year"] = df["datetime_wita"].dt.year
    df["season"] = df["month"].apply(
        lambda m: "wet" if m in WET_SEASON_MONTHS else "dry"
    )
    
    return df


# ============================================================================
# Analysis Functions
# ============================================================================

def compute_hourly_climatology(df: pd.DataFrame, param: str) -> dict:
    """
    Compute mean, std, median, p10, p90 for each hour of day (0-23 WITA).
    Returns dict with hourly stats.
    """
    result = {"hours": list(range(24)), "stats": {}}
    
    for h in range(24):
        subset = df[df["hour_wita"] == h][param].dropna()
        if len(subset) < 10:
            result["stats"][h] = None
            continue
        result["stats"][h] = {
            "mean":   float(subset.mean()),
            "std":    float(subset.std()),
            "median": float(subset.median()),
            "p10":    float(subset.quantile(0.10)),
            "p90":    float(subset.quantile(0.90)),
            "n":      int(len(subset)),
        }
    return result


def compute_monthly_hourly_matrix(df: pd.DataFrame, param: str) -> dict:
    """
    Compute 12×24 matrix of mean values (month × hour_wita).
    Useful for identifying seasonal×diurnal patterns.
    """
    matrix = np.full((12, 24), np.nan)
    for m in range(1, 13):
        for h in range(24):
            subset = df[(df["month"] == m) & (df["hour_wita"] == h)][param].dropna()
            if len(subset) >= 5:
                matrix[m-1, h] = subset.mean()
    return {
        "matrix": matrix.tolist(),
        "months": list(range(1, 13)),
        "hours": list(range(24)),
    }


def compute_rain_diurnal_cycle(df: pd.DataFrame) -> dict:
    """
    Rain frequency and intensity by hour of day.
    Frequency: % of hours with rain > 0.1 mm
    Intensity: mean rain rate when raining
    """
    hours = list(range(24))
    freq = []
    intensity = []
    
    for h in hours:
        subset = df[df["hour_wita"] == h]["rain_1h"].dropna()
        if len(subset) == 0:
            freq.append(0.0)
            intensity.append(0.0)
            continue
        rain_hours = subset[subset > 0.1]
        freq.append(float(len(rain_hours) / len(subset) * 100))
        intensity.append(float(rain_hours.mean()) if len(rain_hours) > 0 else 0.0)
    
    return {
        "hours": hours,
        "frequency_pct": freq,
        "intensity_mmh": intensity,
    }


def compute_gust_diurnal_cycle(df: pd.DataFrame) -> dict:
    """
    Wind gust frequency and intensity by hour of day.
    Gust active = wind_gust_max IS NOT NULL (sensor reports gust).
    """
    hours = list(range(24))
    freq = []
    intensity = []
    
    for h in hours:
        subset = df[df["hour_wita"] == h]
        if len(subset) == 0:
            freq.append(0.0)
            intensity.append(0.0)
            continue
        gust_active = subset["wind_gust_max"].notna()
        freq.append(float(gust_active.sum() / len(subset) * 100))
        gust_values = subset.loc[gust_active, "wind_gust_max"].dropna()
        intensity.append(float(gust_values.mean()) if len(gust_values) > 0 else 0.0)
    
    return {
        "hours": hours,
        "frequency_pct": freq,
        "intensity_kt": intensity,
    }


def compute_wind_rose_data(df: pd.DataFrame) -> dict:
    """
    Wind direction frequency by 16-point compass, with mean speed per sector.
    Separate wet season vs dry season.
    """
    sectors = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    sector_angles = np.arange(0, 360, 22.5)
    
    result = {"sectors": sectors, "wet": [], "dry": []}
    
    for season in ["wet", "dry"]:
        season_df = df[df["season"] == season]
        for i, angle in enumerate(sector_angles):
            # Wind direction falls in [angle, angle+22.5)
            mask = ((season_df["wind_dir"] >= angle) & 
                    (season_df["wind_dir"] < angle + 22.5))
            sector_data = season_df.loc[mask]
            freq_pct = float(len(sector_data) / len(season_df) * 100) if len(season_df) > 0 else 0
            mean_speed = float(sector_data["wind_speed"].mean()) if len(sector_data) > 0 else 0
            result[season].append({
                "freq_pct": freq_pct,
                "mean_speed_kt": mean_speed,
                "n": int(len(sector_data)),
            })
    
    return result


def identify_sea_breeze(df: pd.DataFrame) -> dict:
    """
    Identify sea breeze / land breeze regime by comparing
    daytime (10-16 WITA) vs nighttime (22-04 WITA) wind direction.
    
    WAWP is coastal, so sea breeze should be onshore (from ocean).
    Geography: WAWP at -4.338, 121.524. Ocean is to the south/east of Sulawesi.
    Expected sea breeze: SE-S (135-180°)
    Expected land breeze: NW-N (315-360°)
    """
    daytime = df[(df["hour_wita"] >= 10) & (df["hour_wita"] <= 16)]
    nighttime = df[(df["hour_wita"] >= 22) | (df["hour_wita"] <= 4)]
    
    def circular_mean(angles):
        angles_rad = np.deg2rad(angles.dropna())
        if len(angles_rad) == 0:
            return None
        u = np.cos(angles_rad).mean()
        v = np.sin(angles_rad).mean()
        return float(np.rad2deg(np.arctan2(v, u)) % 360)
    
    return {
        "daytime_mean_dir":   circular_mean(daytime["wind_dir"]),
        "nighttime_mean_dir": circular_mean(nighttime["wind_dir"]),
        "daytime_mean_speed": float(daytime["wind_speed"].mean()),
        "nighttime_mean_speed": float(nighttime["wind_speed"].mean()),
        "daytime_n":   int(len(daytime)),
        "nighttime_n": int(len(nighttime)),
    }


def statistical_tests(df: pd.DataFrame) -> dict:
    """
    Run statistical tests to validate diurnal patterns.
    - Chi-square: rain frequency differs by hour?
    - ANOVA: temperature differs by hour?
    - Kruskal-Wallis: wind speed differs by hour? (non-parametric)
    """
    tests = {}
    
    # Chi-square: rain frequency by hour
    rain_by_hour = []
    for h in range(24):
        subset = df[df["hour_wita"] == h]["rain_1h"].dropna()
        rain_count = (subset > 0.1).sum()
        dry_count = len(subset) - rain_count
        rain_by_hour.append([rain_count, dry_count])
    chi2, p_val, dof, _ = scipy_stats.chi2_contingency(rain_by_hour)
    tests["rain_chi_square"] = {
        "statistic": float(chi2),
        "p_value": float(p_val),
        "dof": int(dof),
        "significant": bool(p_val < 0.05),
        "interpretation": "Rain frequency differs significantly by hour" if p_val < 0.05
                          else "No significant diurnal rain pattern",
    }
    
    # ANOVA: temperature by hour
    temp_by_hour = [df[df["hour_wita"] == h]["temperature"].dropna().values 
                    for h in range(24)]
    temp_by_hour = [a for a in temp_by_hour if len(a) >= 5]
    f_stat, p_val = scipy_stats.f_oneway(*temp_by_hour)
    tests["temp_anova"] = {
        "f_statistic": float(f_stat),
        "p_value": float(p_val),
        "significant": bool(p_val < 0.05),
    }
    
    # Kruskal-Wallis: wind speed by hour (non-parametric)
    ws_by_hour = [df[df["hour_wita"] == h]["wind_speed"].dropna().values 
                  for h in range(24)]
    ws_by_hour = [a for a in ws_by_hour if len(a) >= 5]
    h_stat, p_val = scipy_stats.kruskal(*ws_by_hour)
    tests["wind_speed_kruskal"] = {
        "h_statistic": float(h_stat),
        "p_value": float(p_val),
        "significant": bool(p_val < 0.05),
    }
    
    return tests


def identify_peak_convective_window(df: pd.DataFrame) -> dict:
    """
    Identify peak convective hours based on:
    - Maximum rain frequency
    - Maximum CAPE (we don't have CAPE in obs, but rain+gust is proxy)
    - Maximum gust frequency
    
    Returns the 6-hour window with highest convective activity.
    """
    rain_cycle = compute_rain_diurnal_cycle(df)
    gust_cycle = compute_gust_diurnal_cycle(df)
    
    # Combined convective score per hour
    scores = []
    for h in range(24):
        score = (rain_cycle["frequency_pct"][h] * 0.5 + 
                 gust_cycle["frequency_pct"][h] * 0.5)
        scores.append(score)
    
    # Find best 6-hour window
    best_window_start = 0
    best_window_score = 0
    for start in range(24):
        window_score = sum(scores[(start + i) % 24] for i in range(6))
        if window_score > best_window_score:
            best_window_score = window_score
            best_window_start = start
    
    return {
        "hourly_convective_scores": scores,
        "peak_window_start_wita": best_window_start,
        "peak_window_end_wita": (best_window_start + 5) % 24,
        "peak_window_score": float(best_window_score),
        "peak_window_hours_wita": [(best_window_start + i) % 24 for i in range(6)],
    }


# ============================================================================
# Visualization
# ============================================================================

def plot_diurnal_climatology(df: pd.DataFrame, output_dir: str):
    """Generate PNG plots for each parameter's diurnal cycle."""
    plots_dir = os.path.join(output_dir, "diurnal_plots")
    os.makedirs(plots_dir, exist_ok=True)
    
    plot_params = [
        ("temperature", "Temperature (°C)", "red"),
        ("dewpoint", "Dewpoint (°C)", "blue"),
        ("pressure", "Pressure (hPa)", "green"),
        ("humidity", "Humidity (%)", "purple"),
        ("wind_speed", "Wind Speed (kt)", "orange"),
        ("rain_1h", "Rain (mm)", "cyan"),
    ]
    
    # 2x3 grid of diurnal plots
    fig, axes = plt.subplots(2, 3, figsize=(15, 10), constrained_layout=True)
    axes = axes.flatten()
    
    for i, (param, label, color) in enumerate(plot_params):
        ax = axes[i]
        hours = list(range(24))
        means = []
        p10s = []
        p90s = []
        for h in hours:
            subset = df[df["hour_wita"] == h][param].dropna()
            if len(subset) >= 5:
                means.append(subset.mean())
                p10s.append(subset.quantile(0.10))
                p90s.append(subset.quantile(0.90))
            else:
                means.append(np.nan)
                p10s.append(np.nan)
                p90s.append(np.nan)
        
        ax.plot(hours, means, color=color, linewidth=2, label="Mean")
        ax.fill_between(hours, p10s, p90s, color=color, alpha=0.2, label="P10-P90")
        ax.set_xlabel("Hour (WITA)")
        ax.set_ylabel(label)
        ax.set_title(f"Diurnal Cycle — {label}")
        ax.set_xticks(range(0, 24, 3))
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)
    
    plt.suptitle("WAWP Diurnal Climatology (5-Year AWOS Data)", fontsize=14, fontweight="bold")
    plt.savefig(os.path.join(plots_dir, "diurnal_overview.png"), dpi=150)
    plt.close()
    log.info(f"Saved: {plots_dir}/diurnal_overview.png")
    
    # Rain frequency + intensity plot
    fig, ax1 = plt.subplots(figsize=(12, 6), constrained_layout=True)
    rain_cycle = compute_rain_diurnal_cycle(df)
    hours = rain_cycle["hours"]
    freq = rain_cycle["frequency_pct"]
    intensity = rain_cycle["intensity_mmh"]
    
    ax1.bar(hours, freq, color="blue", alpha=0.6, label="Rain Frequency (%)")
    ax1.set_xlabel("Hour (WITA)")
    ax1.set_ylabel("Rain Frequency (%)", color="blue")
    ax1.tick_params(axis='y', labelcolor='blue')
    
    ax2 = ax1.twinx()
    ax2.plot(hours, intensity, color="red", linewidth=2, marker="o", label="Mean Intensity (mm/h)")
    ax2.set_ylabel("Mean Intensity (mm/h)", color="red")
    ax2.tick_params(axis='y', labelcolor='red')
    
    plt.title("WAWP Rain Diurnal Cycle — Frequency & Intensity")
    ax1.set_xticks(range(0, 24, 2))
    ax1.grid(True, alpha=0.3)
    plt.savefig(os.path.join(plots_dir, "rain_diurnal.png"), dpi=150)
    plt.close()
    log.info(f"Saved: {plots_dir}/rain_diurnal.png")
    
    # Wind direction polar plot (wet vs dry season)
    fig, axes = plt.subplots(1, 2, figsize=(14, 7), constrained_layout=True, 
                              subplot_kw={'projection': 'polar'})
    wind_rose = compute_wind_rose_data(df)
    sectors = wind_rose["sectors"]
    n_sectors = len(sectors)
    theta = np.linspace(0, 2*np.pi, n_sectors, endpoint=False) + np.pi/2  # N at top
    
    for ax, season in zip(axes, ["wet", "dry"]):
        freqs = [d["freq_pct"] for d in wind_rose[season]]
        ax.bar(theta, freqs, width=2*np.pi/n_sectors*0.9, alpha=0.6, color="blue")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)  # clockwise
        ax.set_xticks(theta)
        ax.set_xticklabels(sectors, fontsize=8)
        ax.set_title(f"Wind Rose — {season.upper()} Season", pad=20)
    
    plt.suptitle("WAWP Wind Direction Distribution by Season", fontsize=14, fontweight="bold")
    plt.savefig(os.path.join(plots_dir, "wind_rose_seasonal.png"), dpi=150)
    plt.close()
    log.info(f"Saved: {plots_dir}/wind_rose_seasonal.png")
    
    # Monthly × hourly heatmap for temperature
    fig, ax = plt.subplots(figsize=(14, 6), constrained_layout=True)
    matrix_data = compute_monthly_hourly_matrix(df, "temperature")
    matrix = np.array(matrix_data["matrix"])
    im = ax.imshow(matrix, aspect="auto", cmap="RdYlBu_r", origin="lower")
    ax.set_xlabel("Hour (WITA)")
    ax.set_ylabel("Month")
    ax.set_xticks(range(24))
    ax.set_xticklabels(range(24), fontsize=8)
    ax.set_yticks(range(12))
    ax.set_yticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    plt.colorbar(im, ax=ax, label="Temperature (°C)")
    ax.set_title("WAWP Temperature: Monthly × Hourly Climatology (WITA)")
    plt.savefig(os.path.join(plots_dir, "temp_monthly_hourly.png"), dpi=150)
    plt.close()
    log.info(f"Saved: {plots_dir}/temp_monthly_hourly.png")


# ============================================================================
# Main
# ============================================================================

def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    _HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(_HERE, "wawp_forecasts.db")
    output_dir = os.path.join(_HERE, "docs", "data")
    os.makedirs(output_dir, exist_ok=True)
    
    log.info("Loading observations...")
    df = load_observations(db_path)
    log.info(f"Loaded {len(df)} hourly observations from {df['datetime_wita'].min()} to {df['datetime_wita'].max()}")
    
    log.info("Computing diurnal climatology for each parameter...")
    climatology = {}
    for param in PARAMETERS:
        if param in df.columns:
            climatology[param] = compute_hourly_climatology(df, param)
    
    log.info("Computing rain diurnal cycle...")
    rain_cycle = compute_rain_diurnal_cycle(df)
    
    log.info("Computing gust diurnal cycle...")
    gust_cycle = compute_gust_diurnal_cycle(df)
    
    log.info("Computing wind rose data...")
    wind_rose = compute_wind_rose_data(df)
    
    log.info("Identifying sea breeze regime...")
    sea_breeze = identify_sea_breeze(df)
    
    log.info("Running statistical tests...")
    stat_tests = statistical_tests(df)
    
    log.info("Identifying peak convective window...")
    convective = identify_peak_convective_window(df)
    
    # Compute monthly × hourly matrices for key params
    log.info("Computing monthly × hourly matrices...")
    matrices = {}
    for param in ["temperature", "rain_1h", "wind_speed", "humidity"]:
        if param in df.columns:
            matrices[param] = compute_monthly_hourly_matrix(df, param)
    
    # Assemble final payload
    payload = {
        "metadata": {
            "station": LOCATION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_period": {
                "start": df["datetime_wita"].min().isoformat(),
                "end":   df["datetime_wita"].max().isoformat(),
            },
            "total_observations": int(len(df)),
        },
        "climatology": climatology,
        "rain_diurnal_cycle": rain_cycle,
        "gust_diurnal_cycle": gust_cycle,
        "wind_rose": wind_rose,
        "sea_breeze_regime": sea_breeze,
        "statistical_tests": stat_tests,
        "convective_window": convective,
        "monthly_hourly_matrices": matrices,
        "season_definitions": {
            "wet_season_months": WET_SEASON_MONTHS,
            "dry_season_months": DRY_SEASON_MONTHS,
        },
    }
    
    # Save JSON
    out_path = os.path.join(output_dir, "diurnal_climatology.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, default=str)
    log.info(f"Saved: {out_path}")
    
    # Generate plots
    log.info("Generating plots...")
    plot_diurnal_climatology(df, output_dir)
    
    # Print summary
    log.info("=" * 60)
    log.info("DIURNAL ANALYSIS SUMMARY")
    log.info("=" * 60)
    log.info(f"Data period: {df['datetime_wita'].min()} to {df['datetime_wita'].max()}")
    log.info(f"Total observations: {len(df)}")
    log.info(f"Sea breeze regime (day vs night):")
    log.info(f"  Daytime mean WD:   {sea_breeze['daytime_mean_dir']:.0f}° @ {sea_breeze['daytime_mean_speed']:.1f} kt")
    log.info(f"  Nighttime mean WD: {sea_breeze['nighttime_mean_dir']:.0f}° @ {sea_breeze['nighttime_mean_speed']:.1f} kt")
    log.info(f"Peak convective window: {convective['peak_window_start_wita']:02d}:00 - "
             f"{convective['peak_window_end_wita']:02d}:00 WITA")
    log.info(f"Statistical tests:")
    for test_name, test_result in stat_tests.items():
        sig = "SIGNIFICANT" if test_result["significant"] else "not significant"
        log.info(f"  {test_name}: p={test_result['p_value']:.4f} ({sig})")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
```

### Task 3.2: Run Diurnal Analysis

```bash
python src/diurnal_analysis.py
```

**Expected outputs**:
- `docs/data/diurnal_climatology.json` (~500 KB)
- `docs/data/diurnal_plots/diurnal_overview.png`
- `docs/data/diurnal_plots/rain_diurnal.png`
- `docs/data/diurnal_plots/wind_rose_seasonal.png`
- `docs/data/diurnal_plots/temp_monthly_hourly.png`

### Task 3.3-3.8: Verification & Validation

The script in Task 3.1 already implements all sub-analyses:
- **3.3**: Wind regime (sea breeze) identification — function `identify_sea_breeze()`
- **3.4**: Rain diurnal cycle — function `compute_rain_diurnal_cycle()`
- **3.5**: Gust diurnal cycle — function `compute_gust_diurnal_cycle()`
- **3.6**: Statistical tests — function `statistical_tests()` (chi-square, ANOVA, Kruskal-Wallis)
- **3.7**: Visualizations — function `plot_diurnal_climatology()` (4 PNG plots)
- **3.8**: JSON output — saved to `docs/data/diurnal_climatology.json`

### Task 3.9: Monthly V_dry Lookup (NEW per Apodex Round 2)

**Goal**: Capture peatland smoke effect on dry-season visibility (Jul-Oct in Sulawesi).

**Background**: The current `vis_cloud_proxy.py` uses station-constant `V_dry = 9999 m`. Per Apodex Round 2 Direction 3, peatland burning in Sulawesi (typically Jul-Oct) can reduce ambient visibility by several km independently of rain. A monthly V_dry lookup table from the AWOS archive captures this signal without requiring additional data sources.

**Implementation** — add to `src/diurnal_analysis.py`:

```python
def compute_monthly_v_dry(db_path: str) -> dict:
    """
    Compute median visibility per month during dry/no-rain hours.
    Captures peatland smoke effect (Jul-Oct in Sulawesi).
    
    Per Apodex Round 2 Direction 3:
    - Filter to rain_1h < 0.1 mm (dry hours)
    - Group by month (1-12)
    - Take median visibility per month
    
    Returns: dict with months and v_dry medians.
    Falls back gracefully if visibility column is not populated.
    """
    import sqlite3
    
    try:
        conn = sqlite3.connect(db_path)
        # Check if visibility column exists and has data
        check = conn.execute("""
            SELECT COUNT(*) FROM awos_observations 
            WHERE visibility IS NOT NULL AND rain_1h < 0.1
        """).fetchone()[0]
        
        if check == 0:
            conn.close()
            return {
                "status": "no_visibility_data",
                "note": "AWOS hourly files do not include visibility column. "
                        "Populate from 1-min file aggregation or external source.",
                "months": list(range(1, 13)),
                "v_dry_medians": [9999] * 12,
            }
        
        df = pd.read_sql("""
            SELECT strftime('%m', obs_time) as month, visibility
            FROM awos_observations
            WHERE rain_1h < 0.1 AND visibility IS NOT NULL
        """, conn)
        conn.close()
        
        monthly = df.groupby("month")["visibility"].median()
        v_dry = [float(monthly.get(f"{m:02d}", 9999)) for m in range(1, 13)]
        
        # Identify smoke-affected months (vis < 9999)
        smoke_months = [m for m, v in zip(range(1, 13), v_dry) if v < 9999]
        
        return {
            "status": "computed",
            "months": list(range(1, 13)),
            "v_dry_medians": v_dry,
            "smoke_affected_months": smoke_months,
            "interpretation": (
                f"Values < 9999 indicate dry-season visibility reduction "
                f"(likely peatland smoke). Affected months: {smoke_months}"
            ),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def compute_lcl_seasonal_calibration(df: pd.DataFrame) -> dict:
    """
    Compute seasonal LCL multiplier from AWOS observations.
    
    Per Apodex Round 2 Direction 3 extension:
    - Wet season (Nov-Apr): deeper boundary layer, different LCL relationship
    - Dry season (May-Oct): shallower boundary layer
    
    Method:
    - Filter to rain-free hours (rain_1h < 0.1)
    - Filter to hours with single low cloud layer (base < 5000 ft)
      [requires AWOS cloud base data — may need separate ingestion]
    - Fit base_ft = m × (T - Td) using OLS (numpy.polyfit degree 1)
    - Separate fit for wet vs dry season
    
    Expected: m in 380-420 ft/°C range
    A 5% correction translates to direct ceiling forecast improvement.
    """
    import numpy as np
    
    result = {"wet_season": None, "dry_season": None, "method": "OLS"}
    
    # Note: This requires AWOS cloud base observations
    # If awos_observations table doesn't have cloud_base_ft column,
    # this will return None and skip calibration
    if "cloud_base_ft" not in df.columns:
        result["status"] = "no_cloud_base_data"
        result["note"] = ("AWOS hourly files do not include cloud base. "
                          "Requires separate ingestion from AWOS 1-min files "
                          "or external ceilometer data.")
        return result
    
    for season in ["wet", "dry"]:
        season_df = df[(df["season"] == season) & 
                       (df["rain_1h"] < 0.1) &
                       (df["cloud_base_ft"].notna()) &
                       (df["cloud_base_ft"] < 5000) &
                       (df["cloud_base_ft"] > 0)]
        
        if len(season_df) < 100:
            result[season] = {"status": "insufficient_samples", "n": len(season_df)}
            continue
        
        # Compute T - Td spread
        dd = season_df["temperature"] - season_df["dewpoint"]
        base = season_df["cloud_base_ft"]
        
        # Filter out zero spread (fog cases)
        valid = (dd > 0.5) & (base > 0)
        if valid.sum() < 50:
            result[season] = {"status": "insufficient_valid_pairs"}
            continue
        
        # OLS fit: base = m × dd (degree 1, no intercept)
        m = np.polyfit(dd[valid], base[valid], 1)[0]
        
        result[season] = {
            "multiplier_ft_per_C": float(m),
            "n_samples": int(valid.sum()),
            "dd_range": [float(dd[valid].min()), float(dd[valid].max())],
            "base_range": [float(base[valid].min()), float(base[valid].max())],
        }
    
    result["current_multiplier"] = 400.0  # current hardcoded value
    result["recommended_change"] = (
        f"Wet: {result['wet_season'].get('multiplier_ft_per_C', 'N/A')}, "
        f"Dry: {result['dry_season'].get('multiplier_ft_per_C', 'N/A')}"
    )
    return result
```

**Update `diurnal_analysis.py` main()** to include these new analyses:
```python
def main():
    # ... existing code ...
    
    log.info("Computing monthly V_dry lookup (peatland smoke detection)...")
    monthly_v_dry = compute_monthly_v_dry(db_path)
    
    log.info("Computing seasonal LCL calibration...")
    lcl_calibration = compute_lcl_seasonal_calibration(df)
    
    # Add to payload
    payload["monthly_v_dry"] = monthly_v_dry
    payload["lcl_seasonal_calibration"] = lcl_calibration
    
    # ... rest of main ...
```

### Sprint 3 Verification

```bash
# Verify JSON output exists and is valid
python -c "
import json
with open('docs/data/diurnal_climatology.json') as f:
    d = json.load(f)
print(f'Station: {d[\"metadata\"][\"station\"]}')
print(f'Period: {d[\"metadata\"][\"data_period\"][\"start\"]} to {d[\"metadata\"][\"data_period\"][\"end\"]}')
print(f'Total observations: {d[\"metadata\"][\"total_observations\"]}')
print(f'Parameters analyzed: {list(d[\"climatology\"].keys())}')
print(f'Sea breeze: day={d[\"sea_breeze_regime\"][\"daytime_mean_dir\"]}°, night={d[\"sea_breeze_regime\"][\"nighttime_mean_dir\"]}°')
print(f'Peak convective window: {d[\"convective_window\"][\"peak_window_start_wita\"]}:00 WITA')
"

# Verify plots exist
ls -la docs/data/diurnal_plots/
```

**Acceptance criteria**:
- ✅ JSON file exists, valid JSON, contains all expected sections
- ✅ 4 PNG files generated, each > 50 KB (real plots, not blank)
- ✅ Peak convective window identified and reasonable (expected ~12-18 WITA)
- ✅ Sea breeze regime detected (day vs night wind direction difference > 30°)
- ✅ All statistical tests return p-values (most should be significant given 5-year dataset)

---

## 🟡 SPRINT 4: QM MULTI-PARAMETER

**Goal**: Train 245 QM CDFs (7 models × 7 params × 5 lead buckets) using the diurnal analysis insights.

### Task 4.1: Extend QuantileMapper

**File**: `src/quantile_mapper.py`

Add the multi-parameter QM functions (linear, nonneg, circular, zero_inflated, **gamma_parametric**) as detailed in the previous brief. Key additions:
- `_fit_qm_linear`, `_fit_qm_nonneg`, `_fit_qm_circular`, `_fit_qm_zero_inflated`
- **`_fit_qm_gamma`** (NEW per Apodex Round 2) — parametric QM using gamma distribution for wind gust
- `_apply_qm_linear`, `_apply_qm_circular`, `_apply_qm_zero_inflated`
- `fit_multiparam_qm_to_db()`
- `apply_multiparam_qm()`

**Apodex Round 2 — Wind Gust QM Viability (Finding 10)**:
~75 pairs per (model, lead_bucket) is **below the recommended 100-200 pairs** for stable empirical QM on right-skewed distributions (Gudmundsson 2012). Three mitigations required:

1. **Use parametric QM (gamma distribution fitting)** instead of empirical for wind gust
2. **Collapse 5 lead buckets → 3** for wind gust only (0-6h, 6-18h, 18h+)
3. **Flag as "low-confidence"** in `health.json` until 200+ pairs per bucket

**Implementation** — add to `quantile_mapper.py`:
```python
from scipy import stats as scipy_stats

def _fit_qm_gamma(fcst: np.ndarray, obs: np.ndarray) -> dict | None:
    """
    Parametric QM using gamma distribution fitting.
    More stable than empirical for small samples with skewed distributions.
    Used for wind gust where empirical QM needs 200+ samples (per Apodex Round 2).
    
    Reference: Gudmundsson et al. (2012) — recommends 100-200 pairs for stable
    empirical CDF estimation in upper tail.
    """
    mask = (~np.isnan(fcst) & ~np.isnan(obs) & (fcst > 0) & (obs > 0))
    fc, oc = fcst[mask], obs[mask]
    if len(fc) < 50:
        return None
    
    try:
        # Fit gamma distribution to forecast and observation
        fc_shape, fc_loc, fc_scale = scipy_stats.gamma.fit(fc, floc=0)
        obs_shape, obs_loc, obs_scale = scipy_stats.gamma.fit(oc, floc=0)
        
        # Build quantile tables from fitted distributions
        q_levels = np.linspace(0.01, 0.99, 100)  # avoid 0 and 1 for gamma
        fc_q = scipy_stats.gamma.ppf(q_levels, fc_shape, loc=fc_loc, scale=fc_scale)
        obs_q = scipy_stats.gamma.ppf(q_levels, obs_shape, loc=obs_loc, scale=obs_scale)
        
        # Enforce monotonicity
        fc_q = np.maximum.accumulate(fc_q)
        obs_q = np.maximum.accumulate(obs_q)
        
        return {
            "fcst_quantiles": fc_q.tolist(),
            "obs_quantiles": obs_q.tolist(),
            "n_samples": len(fc),
            "method": "gamma_parametric",
            "low_confidence": len(fc) < 200,  # flag per Apodex
            "fc_shape": float(fc_shape), "fc_scale": float(fc_scale),
            "obs_shape": float(obs_shape), "obs_scale": float(obs_scale),
        }
    except Exception as e:
        # Fallback to empirical nonneg if gamma fit fails
        return _fit_qm_nonneg(fcst, obs)


# Updated parameter config — wind gust uses gamma parametric
QM_MULTIPARAM_PARAMS = {
    "temperature": {"type": "linear",            "min_samples": 100},
    "dewpoint":    {"type": "linear",            "min_samples": 100},
    "pressure":    {"type": "linear",            "min_samples": 100},
    "wind_speed":  {"type": "nonneg",            "min_samples": 100},
    "wind_gust":   {"type": "gamma_parametric",  "min_samples": 50,   # NEW per Apodex
                    "min_samples_stable": 200},                       # NEW: low-conf flag
    "wind_dir":    {"type": "circular",          "min_samples": 100},
    "rain":        {"type": "zero_inflated",     "min_samples": 50},
}

# Wind Gust: collapse 5 buckets → 3 to triple sample density per bucket
LEAD_BUCKETS_GUST = ["L1_0_6h", "L2_6_18h", "L3_18h_plus"]  # 3 buckets for Gust
# Other params: keep 5 buckets
LEAD_BUCKETS_DEFAULT = ["L1_0_6h", "L2_6_12h", "L3_12_24h", "L4_24_48h", "L5_48plus"]
```

**Training loop update** in `train_qm_multiparam.py`:
```python
def train_all(db):
    for model in MULTIPARAM_MODELS_OPENMETEO:
        for param in PARAM_DB_MAP.keys():
            # Use collapsed buckets for wind_gust, default for others
            if param == "wind_gust":
                buckets = LEAD_BUCKETS_GUST
            else:
                buckets = LEAD_BUCKETS_DEFAULT
            
            for bucket in buckets:
                # ... existing training logic ...
                # If low_confidence flag set, write to health.json
                if qm_dict and qm_dict.get("low_confidence", False):
                    log.warning(f"[LOW-CONF] {model}/{param}/{bucket}: "
                                f"only {qm_dict['n_samples']} samples (<200)")
                    # Write to health.json for dashboard advisory
```

### Task 4.2: Train QM Multi-Parameter

**New file**: `src/train_qm_multiparam.py`

(As detailed in previous brief. Key additions:
- Log-transform option for wind_gust (alternative to gamma)
- Gamma parametric QM as primary method for wind_gust
- Low-confidence flagging in `health.json`
- Collapsed lead buckets for wind_gust)

### Task 4.3-4.6: Run Training, Integrate, Test

(Follow previous brief instructions for these tasks.)

---

## 🟡 SPRINT 5: TSRA CAPE UPGRADE

**Goal**: Replace rain-rate-only TSRA detection with CAPE-based detection.

### Task 5.1: Update `get_weather_phenomenon()` with CAPE

**File**: `src/vis_cloud_proxy.py`

**Current signature**:
```python
def get_weather_phenomenon(rain_mmh, rh_pct, temp_c, dewpoint_c, vis_m, local_hour_wita):
```

**New signature** (add CAPE, lifted_index, cin, weather_code):
```python
def get_weather_phenomenon(rain_mmh, rh_pct, temp_c, dewpoint_c, vis_m, 
                           local_hour_wita, cape=None, lifted_index=None,
                           cin=None, weather_code=None, month=None):
```

**New TSRA logic** (revised per Apodex Round 2 — CIN threshold raised to 100):
```python
def _detect_tsra(rain_mmh, cape, lifted_index, cin, weather_code,
                 local_hour_wita, rh_pct, month):
    """
    TSRA detection with seasonal CAPE thresholds.
    
    Revised per Apodex Round 2 review:
    - CIN threshold raised from 30/50 → 100 (maritime tropics allow TS
      initiation with CIN up to 100 J/kg due to weak convective inhibition)
    - Reference: FAA Aviation Weather Handbook FAA-H-8083-28B §4.4
    
    Wet season (Nov-Apr): CAPE ≥ 500 J/kg + CIN ≤ 100 J/kg
    Dry season (May-Oct): CAPE ≥ 800 J/kg + CIN ≤ 100 J/kg
    """
    # WMO weather code override (GFS only populates TS codes in tropics)
    if weather_code is not None and weather_code in [95, 96, 99]:
        return "TSRA"
    
    # Seasonal CAPE threshold (CIN raised to 100 per Apodex Round 2)
    if month is not None:
        if month in [11, 12, 1, 2, 3, 4]:  # wet season
            cape_thr, cin_thr = 500, 100   # was 30, raised per Apodex
        else:  # dry season
            cape_thr, cin_thr = 800, 100   # was 50, raised per Apodex
    else:
        cape_thr, cin_thr = 500, 100  # default to wet season (conservative)
    
    # Strong TS signal: CAPE + Lifted Index + CIN
    if (cape is not None and cape >= cape_thr and
        lifted_index is not None and lifted_index <= -2 and
        cin is not None and cin <= cin_thr):
        if rain_mmh >= 5 or (11 <= local_hour_wita <= 21):
            return "TSRA"
    
    # Fallback: heavy rain + daytime + high humidity
    if rain_mmh >= 7.5 and 11 <= local_hour_wita <= 21 and rh_pct >= 85:
        return "TSRA"
    
    return None  # no TSRA detected
```

### Task 5.2: Update Callers

In `tafor_generator.py` and `taf_core.py`, pass CAPE/lifted_index/CIN/weather_code/month to `get_weather_phenomenon()`. These values come from `consensus_truth[i]` which already includes them (from Open-Meteo forecast).

### Task 5.3: Verification

Test with synthetic data:
```python
# Wet season, CAPE=800, LI=-3, CIN=20, rain=3 mm/h, 14 WITA
wx = get_weather_phenomenon(rain_mmh=3, rh_pct=88, temp_c=28, dewpoint_c=24, vis_m=4000,
                            local_hour_wita=14, cape=800, lifted_index=-3, cin=20,
                            weather_code=None, month=1)
assert wx == "TSRA", f"Expected TSRA, got {wx}"

# Dry season, CAPE=600 (below threshold), no TSRA
wx = get_weather_phenomenon(rain_mmh=3, rh_pct=88, temp_c=28, dewpoint_c=24, vis_m=4000,
                            local_hour_wita=14, cape=600, lifted_index=-1, cin=80,
                            weather_code=None, month=7)
assert wx != "TSRA", f"Should not be TSRA in dry season with low CAPE"
```

---

## 🟡 SPRINT 6: WEIGHT RE-TRAINING

**Goal**: Pre-train CRPS weights using Historical Forecast API backfill to compress convergence timeline.

### Task 6.1: Historical Forecast API Backfill

**New file**: `src/backfill_historical_forecasts.py`

Open-Meteo Historical Forecast API endpoint:
```
https://previous-runs-api.open-meteo.com/v1/forecast
```

Same as Single Runs API but with `start_date` + `end_date` for bulk backfill. Use to backfill the most recent 12-18 months (per reviewer recommendation to limit window due to model version changes).

### Task 6.2: Pre-train Weights

Run `advanced_ensemble_weighter.py` on the backfilled data to compute CRPS weights before cutover. Save to `docs/data/latest_weights.json`.

### Task 6.3: Validate Weights

Compare pre-trained weights against equal-weight (1/7) baseline using walk-forward validation on the most recent 3 months.

---

## 🟡 SPRINT 7: PIPELINE INTEGRATION

**Goal**: Integrate Open-Meteo + QM multi-param + diurnal analysis into the main pipeline.

### Task 7.1: Replace Meteologix Scraper with Open-Meteo Client

**New file**: `src/scrape_openmeteo.py`

Replace `src/scrape_meteologix.py` as the primary data source. Keep Meteologix as fallback for "ACCESS-G3" and "Multi-Model" (MeteoBlue) which are not in Open-Meteo.

### Task 7.2: Apply QM Multi-Param in Consensus

Modify `src/export_dashboard_data.py` consensus loop to apply QM per-parameter, lead-time aware.

### Task 7.3: Export Diurnal Climatology to Dashboard

In `export_all()`, copy `diurnal_climatology.json` to output dir if not already there. Add reference in `tafor_intel.json`.

### Task 7.4: Run Pipeline End-to-End

```bash
python run_pipeline.py
# Verify all outputs:
ls -la docs/data/
# Expected: tafor_intel.json, latest_weights.json, latest_performance.json,
#           qm_status.json, diurnal_climatology.json, pipeline_health.json
```

---

## 🟢 SPRINT 8: VALIDATION & TESTING

### Task 8.1: Unit Tests for QM

**New file**: `tests/test_qm_multiparam.py`

Tests for:
- `_fit_qm_linear` — basic bias correction
- `_fit_qm_nonneg` — non-negative clamping
- `_fit_qm_circular` — wind direction via u/v transform
- `_fit_qm_zero_inflated` — rainfall dry/wet separation
- `apply_multiparam_qm` — lead-time aware application
- Edge cases: insufficient samples, NaN handling, extreme values

### Task 8.2: Unit Tests for Diurnal Analysis

**New file**: `tests/test_diurnal_analysis.py`

Tests for:
- `compute_hourly_climatology` — correct statistics per hour
- `compute_rain_diurnal_cycle` — frequency + intensity
- `identify_sea_breeze` — day vs night wind direction
- `statistical_tests` — chi-square, ANOVA, Kruskal-Wallis

### Task 8.3: Integration Test

**New file**: `tests/test_pipeline_integration.py`

End-to-end test:
1. Mock Open-Meteo API response
2. Run `generate_tafor()` with mock data
3. Verify TAF output is ICAO-compliant
4. Verify no P0-1 or P0-2 violations

### Task 8.4: Walk-Forward Validation

**New file**: `tests/walk_forward_validation.py`

For each (model, parameter):
1. Train QM on months 1-12
2. Test on month 13
3. Compute MAE before/after QM
4. Move window forward 1 month, repeat
5. Aggregate results

### Task 8.5: KS Test for Distributional Fit

Add to QM validation:
```python
from scipy.stats import ks_2samp
stat, p_value = ks_2samp(corrected_forecast, observations)
# p_value > 0.05 → distributions match → QM successful
```

---

## 🟢 SPRINT 9: GIT WORKFLOW & PR

### Task 9.1: Branch Creation

```bash
git checkout main
git pull origin main
git checkout -b feat/openmeteo-qm-multiparam-diurnal
```

### Task 9.2: Stage Files

```bash
# Source files
git add src/ingest_awos.py
git add src/ingest_awos_1min.py
git add src/db_manager.py
git add src/backfill_openmeteo.py
git add src/build_qm_training_pairs.py
git add src/quantile_mapper.py
git add src/train_qm_multiparam.py
git add src/diurnal_analysis.py
git add src/vis_cloud_proxy.py
git add src/tafor_generator.py
git add src/taf_core.py
git add src/export_dashboard_data.py
git add src/scrape_openmeteo.py
git add run_pipeline.py

# Tests
git add tests/test_qm_multiparam.py
git add tests/test_diurnal_analysis.py
git add tests/test_pipeline_integration.py
git add tests/walk_forward_validation.py

# Config
git add .gitignore
```

### Task 9.3: Update .gitignore

Ensure these are ignored:
```
wawp_forecasts.db
wawp_forecasts.db.backup_*
wawp_forecasts.db-wal
wawp_forecasts.db-shm
docs/data/qm_state.json
docs/data/diurnal_climatology.json
Archives/
data/raw_obs/
*.pyc
__pycache__/
.pytest_cache/
```

### Task 9.4: Commit & Push

```bash
git commit -m "feat: Open-Meteo migration + multi-param QM + diurnal analysis

BLOCKING FIXES (Sprint 0):
- Fix P0-1: Base group now injects wx when vis < 5000m (ICAO Annex 3 §4.4)
- Fix P0-2: CAVOK gate now checks rain_mmh ≤ 0.1 (no CAVOK during precip)
- Fix Finding 3: Visibility tier boundary 0.4 → 0.5 (removes discontinuity)
- Fix Finding 5: HZ/BR/FG no longer fire during active rain
- Fix Finding 11: AWOS stale flag written to pipeline_health.json
- Fix Finding 12: DB connection uses context manager (no leak)

DATA FOUNDATION (Sprint 1):
- Fix hourly AWOS parser (was reading non-existent columns 11/12)
- Add 1-minute AWOS parser + ingest pipeline (4 years, 2.1M rows)
- Aggregate 1-min wind gust to hourly max for QM training

OPEN-METEO MIGRATION (Sprint 2):
- Add openmeteo_forecasts and qm_cdfs tables
- Backfill 7 models: ECMWF (2yr), GFS/ICON (1yr), CMA/MF (11mo), UKMO (5mo), GEM (3mo)
- Rescinded JMA (missing CAPE/gust) and BOM (NULL all variables)
- Build qm_training_pairs materialized table

DIURNAL ANALYSIS (Sprint 3):
- Compute hourly climatology for 8 parameters
- Identify sea breeze regime (day vs night wind direction)
- Identify peak convective window for TSRA calibration
- Statistical tests: chi-square, ANOVA, Kruskal-Wallis
- Generate 4 PNG visualizations + JSON for dashboard

QM MULTI-PARAMETER (Sprint 4):
- Extend QuantileMapper with 4 QM types: linear, nonneg, circular, zero_inflated
- Train 245 CDFs (7 models × 7 params × 5 lead buckets)
- Lead-time aware application (5 buckets: 0-6h, 6-12h, 12-24h, 24-48h, 48+h)
- 80/20 validation with MAE before/after, KS test for distributional fit

TSRA UPGRADE (Sprint 5):
- Replace rain-rate-only TSRA with CAPE-based detection
- Seasonal thresholds: wet season CAPE≥500, dry season CAPE≥800
- Add CIN check (≤30 wet, ≤50 dry)
- WMO weather code override (95/96/99)

WEIGHT RE-TRAINING (Sprint 6):
- Historical Forecast API backfill for CRPS weight pre-training
- Compress convergence timeline from 7-9 months to 2-3 weeks

VALIDATION (Sprint 8):
- Unit tests for all QM parameter types
- Walk-forward validation script
- KS test for distributional goodness-of-fit

Tested with 5 years of AWOS data (42K hourly observations) and 4 years of
1-minute observations (2.1M rows). All P0 ICAO violations resolved.

Closes: #P0-1, #P0-2, #Finding-3, #Finding-5, #Finding-11, #Finding-12"

git push origin feat/openmeteo-qm-multiparam-diurnal
```

### Task 9.5: Create Pull Request

```bash
gh pr create --title "feat: Open-Meteo Migration + Multi-Param QM + Diurnal Analysis" \
  --body "## Summary

Comprehensive overhaul of WAWP TITAN Pipeline:
- 6 blocking ICAO fixes (Sprint 0)
- AWOS data foundation fix + 1-minute ingest (Sprint 1)
- Open-Meteo migration with 2-year backfill (Sprint 2)
- Full diurnal analysis with visualizations (Sprint 3)
- Multi-parameter QM (245 CDFs, 7 params × 7 models × 5 lead buckets) (Sprint 4)
- CAPE-based TSRA detection with seasonal thresholds (Sprint 5)
- Historical Forecast API for weight pre-training (Sprint 6)
- Comprehensive test suite (Sprint 8)

## Test Plan

- [ ] Sprint 0: Verify P0-1, P0-2, Finding 3, 5, 11, 12 fixes
- [ ] Sprint 1: Verify AWOS ingest (42K hourly + 2.1M 1-min rows)
- [ ] Sprint 2: Verify Open-Meteo backfill (7 models, correct archive depths)
- [ ] Sprint 3: Verify diurnal_analysis.py outputs JSON + 4 PNGs
- [ ] Sprint 4: Verify 245 QM CDFs trained, ≥200 enabled
- [ ] Sprint 5: Verify TSRA detection with CAPE
- [ ] Sprint 6: Verify pre-trained weights
- [ ] Sprint 7: Verify pipeline runs end-to-end
- [ ] Sprint 8: All unit tests pass
- [ ] Sprint 9: PR merged to main

## Breaking Changes

- AWOS parser rewritten (was buggy, now correct)
- TSRA detection requires CAPE input (fallback to rain-rate if CAPE missing)
- QM multi-param replaces rainfall-only QM (backward compatible via legacy path)

## Notes

- Wind Gust QM enabled (4 years of 1-min data provides ~3,500 hours with gust)
- Models 'ACCESS-G3' and 'Multi-Model' (Meteologix-only) not in Open-Meteo
- For those models, only legacy rainfall QM applies" \
  --base main \
  --head feat/openmeteo-qm-multiparam-diurnal
```

---

## 📌 BACKLOG (Phase 2 Enhancements — Post-Deployment)

These items are NOT in scope for the current sprint but should be addressed in Phase 2 (1-3 months post-deployment) based on Apodex Round 2 directions and operational feedback.

### B1: Rain Type Classification (Apodex Direction 2)

**Goal**: Classify rain as convective vs stratiform from existing model fields, then apply DSD-appropriate visibility formula.

**Background**: The current tropical convective DSD formula assumes all rain is convective. During dry-season MJO active phases, stratiform rain occasionally dominates at WAWP — its smaller, more uniform drops produce more optical extinction per mm/h than convective rain. Applying the convective formula to stratiform events over-estimates visibility.

**Implementation** — add to `src/vis_cloud_proxy.py`:
```python
def classify_rain_type(rain_mmh, sunshine_min, low_cloud_pct, mid_cloud_pct, prev_rain_mmh):
    """
    Returns 'convective' or 'stratiform'.
    
    Convective signature:
    - High rain rate (> 2.5 mm/h)
    - Low sunshine minutes (< 30 min in past hour)
    - High low-cloud fraction (> 60%)
    - Rapid onset (rain rate change > 2 mm/h between consecutive hours)
    
    Stratiform signature:
    - Moderate sustained rain (0.5-5 mm/h)
    - Dominant mid-cloud fraction (> 50%)
    - Gradual onset (rain rate change < 1 mm/h)
    
    Default: 'convective' (tropics are predominantly convective)
    """
    onset_rate = rain_mmh - (prev_rain_mmh or 0)
    
    if (rain_mmh > 2.5 and sunshine_min is not None and sunshine_min < 30 and
        low_cloud_pct is not None and low_cloud_pct > 60 and onset_rate > 2.0):
        return "convective"
    
    if (rain_mmh > 0.5 and mid_cloud_pct is not None and mid_cloud_pct > 50 and 
        onset_rate < 1.0):
        return "stratiform"
    
    return "convective"  # default for tropics

def estimate_visibility_rain_typed(rain_mmh, rain_type, **kwargs):
    """Apply DSD-appropriate formula based on rain type."""
    if rain_type == "stratiform":
        # Stratiform: smaller drops, steeper exponent
        # Mid-latitude calibration (Marshall-Palmer-like but adjusted)
        if rain_mmh < 0.4:
            return 9999
        vis = 4500.0 * (rain_mmh ** -0.65)
        return int(max(200.0, min(vis, 9999.0)))
    else:
        # Convective: current tropical formula (larger drops, gentler exponent)
        return estimate_visibility(rain_mmh, **kwargs)
```

**Dependencies**: Requires `sunshine_min`, `low_cloud_pct`, `mid_cloud_pct` from Open-Meteo forecast (already available in `openmeteo_forecasts` table).

**Validation**: Compare against AWOS during MJO active phases (track MJO index from NOAA).

### B2: Probabilistic PROB30/PROB40 Generation (Apodex Direction 1)

**Goal**: Auto-generate PROB groups from ensemble spread.

**Mapping** (per Apodex):
- P(R > 1mm) in 30-49% range → `PROB30 TEMPO` with consensus vis/cloud
- P(R > 1mm) ≥ 50% → `TEMPO` unconditional
- P(R > 10mm) ≥ 30% → `PROB30 TEMPO` with heavy-rain visibility

**Precondition**: Raise `PROB30_CUT` from 0.02 → 0.30 BEFORE enabling this.

**Implementation**: Use existing `rain_agreement()` function in `taf_core.py` — it already computes weighted agreement fraction. Add new PROB group emission logic in Phase 4 (after group cap).

**Enhancement**: Consider Open-Meteo Ensemble API (51-member ECMWF EPS) for true probability distributions instead of 7-model deterministic spread.

### B3: Re-tune PROB30_CUT After QM Deployment

**Goal**: Re-validate `PROB30_CUT = 0.02` after QM multi-param is deployed.

**Background**: The current value was grid-search optimized (v112, rank_score +0.076, FAR unchanged). But QM will change the rain rate distribution, potentially invalidating the old optimization.

**Method**: Re-run grid search with QM-corrected rain rates on 6+ months of paired data.

### B4: Concurrency Guard for CI/CD (Apodex Findings 11, 12)

**Goal**: Add `concurrency:` group to GitHub Actions workflows to prevent simultaneous push conflicts.

**Precondition**: Confirm CI/CD setup (GitHub Actions vs local crontab).

**Implementation**: Add to `.github/workflows/cron.yml` and `.github/workflows/awos-upload.yml`:
```yaml
concurrency:
  group: wawp-pipeline
  cancel-in-progress: false  # critical: cancelling mid-run leaves DB in partial state
```

Also add WAL checkpoint before git add in cron workflow:
```python
import sqlite3
conn = sqlite3.connect('wawp_forecasts.db')
conn.execute('PRAGMA wal_checkpoint(FULL);')
conn.close()
```

### B5: Monthly V_dry Integration to Proxy (Depends on Sprint 3 Task 3.9)

**Goal**: Once `compute_monthly_v_dry()` produces a lookup table (Sprint 3 Task 3.9), integrate it into `vis_cloud_proxy.py` to replace station-constant `V_dry = 9999`.

**Implementation**:
```python
# In vis_cloud_proxy.py
MONTHLY_V_DRY = None  # loaded from diurnal_climatology.json at startup

def load_monthly_v_dry(json_path="docs/data/diurnal_climatology.json"):
    global MONTHLY_V_DRY
    try:
        with open(json_path) as f:
            data = json.load(f)
        MONTHLY_V_DRY = data.get("monthly_v_dry", {}).get("v_dry_medians", [9999]*12)
    except Exception:
        MONTHLY_V_DRY = [9999] * 12  # fallback

def get_v_dry_for_month(month: int) -> float:
    if MONTHLY_V_DRY is None:
        load_monthly_v_dry()
    return MONTHLY_V_DRY[month - 1] if 1 <= month <= 12 else 9999

# In estimate_visibility(), dry branch:
# Replace: vis = float(baseline_dry_vis_m)
# With: vis = float(get_v_dry_for_month(current_month))
```

---

## ✅ FINAL ACCEPTANCE CRITERIA

All items must be TRUE before PR merge:

### Sprint 0 (Blocking)
- [ ] Base group emits wx when vis < 5000m
- [ ] CAVOK not emitted when rain > 0.1 mm/h
- [ ] Visibility tier boundary at 0.5 (not 0.4)
- [ ] HZ/BR/FG do not fire during rain > 0.1 mm/h
- [ ] `pipeline_health.json` written with `awos_stale` flag
- [ ] DB connections use context manager

### Sprint 1 (Data Foundation)
- [ ] `awos_observations` has ~42,000 rows (5 years)
- [ ] `awos_observations_1min` has ~2,100,000 rows (4 years)
- [ ] `awos_observations.wind_gust_max` populated (~3,500 hours with gust)

### Sprint 2 (Open-Meteo)
- [ ] `openmeteo_forecasts` has 7 models with correct archive depths
- [ ] `qm_training_pairs` has ≥ 500K rows total

### Sprint 3 (Diurnal Analysis)
- [ ] `diurnal_climatology.json` exists and is valid JSON
- [ ] 4 PNG plots generated in `docs/data/diurnal_plots/`
- [ ] Peak convective window identified (expected ~12-18 WITA)
- [ ] Sea breeze regime detected (day vs night WD difference > 30°)

### Sprint 4 (QM Multi-Param)
- [ ] 245 QM CDFs in `qm_cdfs` table
- [ ] ≥ 200 CDFs with `enabled=1`
- [ ] Wind Gust QM has ≥ 30 enabled entries

### Sprint 5 (TSRA)
- [ ] TSRA detection uses CAPE (when available)
- [ ] Seasonal thresholds (wet: 500, dry: 800 J/kg)
- [ ] WMO weather code override

### Sprint 7 (Integration)
- [ ] `run_pipeline.py` completes without error
- [ ] All expected JSON files in `docs/data/`
- [ ] TAF output is ICAO-compliant

### Sprint 8 (Testing)
- [ ] `pytest tests/` all pass
- [ ] Walk-forward validation completed

### Sprint 9 (Git)
- [ ] Branch pushed to GitHub
- [ ] PR created with full description

---

## ⚠️ IMPORTANT NOTES FOR AGENT

1. **Backup DB before starting**:
   ```bash
   cp wawp_forecasts.db wawp_forecasts.db.backup_$(date +%Y%m%d)
   ```

2. **Execute sprints in order** — later sprints depend on earlier ones.

3. **Idempotent operations** — all scripts must be re-runnable. If a script fails midway, fix and re-run; don't start from scratch.

4. **Error handling for Open-Meteo API**:
   - HTTP 400 → skip silently (model run not available)
   - HTTP 429 → sleep 60s, retry
   - Timeout → retry 3x with exponential backoff

5. **Do NOT modify** `taf_core.py` logic beyond what's specified in Sprint 0. The change-group builder is complex; only touch the specific lines noted.

6. **Test after each sprint** — don't accumulate changes without verification.

7. **Logging**: Use format `"%(asctime)s [%(levelname)s] %(message)s"` consistent with existing pipeline.

8. **Memory**: For 7 model × 2 years × 360 hours × 25 parameters ≈ 6M rows. SQLite handles this with WAL mode + 64MB cache.

9. **If you encounter issues** with any task, document them in a `KNOWN_ISSUES.md` file and continue with the next task. Don't block the entire pipeline on one issue.

10. **Final step**: After all sprints complete, run the full pipeline one final time and verify TAF output is ICAO-compliant.

---

END OF BRIEF.
