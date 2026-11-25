"""Offline wind-only AWOSnet XML adapter, based on Android AwosClient.java.
Requires independent confirmation of UTC, knots and true-north wind reference.
No network polling, upload, warning generation or forecast/database writes.
"""
import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 128 * 1024
FIELDS = {"airportidentifier", "date", "time", "twominutewindspeed", "twominutewinddirection", "windgust"}

def normalize(body, now, *, confirmed=False):
    if not confirmed:
        raise ValueError("Verify source UTC, knots, 2-min wind and true-north reference before conversion")
    if len(body) > MAX_BYTES or b"\x00" in body or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("Invalid or unsafe XML payload")
    root = ET.fromstring(body)
    found = {}
    for el in root.iter():
        key = el.tag.rsplit("}", 1)[-1].lower()
        if key not in FIELDS:
            continue
        if key in found:
            raise ValueError("Duplicate XML field: " + key)
        found[key] = ((el.get("value") or "").strip(), (el.get("units") or "").strip().lower())
    if found.get("airportidentifier", ("", ""))[0].upper() != "WAWP":
        raise ValueError("WAWP station identifier required")
    raw = found.get("date", ("", ""))[0] + " " + found.get("time", ("", ""))[0]
    stamp = None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            stamp = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            pass
    if stamp is None or stamp > now:
        raise ValueError("Invalid or future source UTC timestamp")
    def field(key, allowed, lo, hi):
        value, units = found.get(key, ("", ""))
        if value in ("", "-", "--", "/", "//", "///", "////", "NA", "N/A", "M"):
            return None
        if units and units not in allowed:
            raise ValueError("Unexpected source unit: " + key)
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value):
            raise ValueError("Malformed numeric value: " + key)
        number = float(value)
        if not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError("Out-of-range value: " + key)
        return number
    knots = {"kt", "kts", "knots", "knot"}
    speed = field("twominutewindspeed", knots, 0, 200)
    direction = field("twominutewinddirection", {"deg", "degree", "degrees", "°", "deg true", "degrees true"}, 0, 360)
    gust = field("windgust", knots, 0, 250)
    if speed is None and gust is None:
        raise ValueError("No valid wind speed or gust")
    return {"schema":"titan-local-awos-v1", "station":"WAWP", "source":"AWOSnet",
            "observed_at_utc":stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), "wind_average_minutes":2,
            "wind_reference":"true", "units_confirmed":True, "wind_speed_kt":speed,
            "wind_direction_deg":direction, "gust_kt":gust,
            "temperature_c":None,"dewpoint_c":None,"qnh_hpa":None}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("xml", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--confirmed-source-semantics", action="store_true")
    args = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if out == repo or repo in out.parents:
        p.error("Output must be outside repository, on approved local storage")
    with args.xml.open("rb") as f:
        body = f.read(MAX_BYTES+1)
    sample = normalize(body, datetime.now(timezone.utc), confirmed=args.confirmed_source_semantics)
    with out.open("x", encoding="utf-8") as f:
        json.dump(sample, f, indent=2)
    print("Local snapshot created. No publication performed.")

if __name__ == "__main__":
    main()
