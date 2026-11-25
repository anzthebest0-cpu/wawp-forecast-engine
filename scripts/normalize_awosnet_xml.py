"""AWOSnet snapshot adapter based on verified XML field layout, not certified sensor semantics.

Offline input or explicitly authorized one-shot HTTP capture. No public upload,
database write, warnings, or automatic polling. Use a trusted station network.
"""
import argparse
import json
import math
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_BYTES = 128 * 1024
ENDPOINT = "http://wawp.awosnet.com/awiAwosNet.php"
ALIASES = {
    "station": ("airportIdentifier", "airportID"),
    "date": ("date",), "time": ("time",),
    "wind_speed_kt": ("twoMinuteWindSpeed", "twoMinutewindSpeed"),
    "wind_direction_deg": ("twoMinuteWindDirection", "twoMinutewindDirection"),
    "gust_kt": ("windGust",),
    "temperature_c": ("airTemperature",),
    "dewpoint_c": ("dewPoint",),
    "temperature_10min_c": ("tenMinuteairTemperature",),
    "dewpoint_10min_c": ("tenMinuteDewPoint", "tenMinutedewPoint"),
    "altimeter_setting_hpa": ("altimeterSetting",),
    "relative_humidity_pct": ("relativeHumidity",),
    "rain_current_hour_mm": ("rainfallCurrentHour",),
    "solar_wm2": ("solarRadiation",),
    "density_altitude_ft": ("densityAltitude",),
    "metar": ("METAR",),
}
MISSING = {"", "-", "--", "/", "//", "///", "////", "NA", "N/A", "M", "\\\\"}
KNOTS = {"kt", "kts", "knots", "knot"}
DEGREES = {"deg", "degree", "degrees", "°", "deg true", "degrees true"}
CELSIUS = {"deg c", "°c", "c", "celsius"}
SPECS = {
    "wind_speed_kt": (KNOTS, 0, 200),
    "wind_direction_deg": (DEGREES, 0, 360),
    "gust_kt": (KNOTS, 0, 250),
    "temperature_c": (CELSIUS, -50, 65),
    "dewpoint_c": (CELSIUS, -80, 65),
    "temperature_10min_c": (CELSIUS, -50, 65),
    "dewpoint_10min_c": (CELSIUS, -80, 65),
    "altimeter_setting_hpa": ({"hpa"}, 850, 1100),
    "relative_humidity_pct": ({"percent", "%"}, 0, 100),
    "rain_current_hour_mm": ({"mm"}, 0, 500),
    "solar_wm2": ({"w/m^2", "w/m²"}, 0, 1600),
    "density_altitude_ft": ({"feet", "ft"}, -5000, 30000),
}

def utc_text(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")

def normalize(body, now, *, confirmed=False):
    if not confirmed:
        raise ValueError("Verify displayed date/time UTC, source units, 2-min wind and true-north reference before conversion")
    if now.tzinfo is None:
        raise ValueError("Reference clock must be timezone-aware")
    if len(body) > MAX_BYTES or b"\x00" in body or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("Invalid or unsafe XML payload")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError("Malformed XML") from exc
    if root.tag.rsplit("}", 1)[-1] != "awosnet":
        raise ValueError("Expected awosnet root")
    tags = {tag for aliases in ALIASES.values() for tag in aliases}
    found = {}
    for el in root:
        name = el.tag.rsplit("}", 1)[-1]
        if name not in tags:
            continue
        if name in found:
            raise ValueError("Repeated XML element: " + name)
        found[name] = ((el.get("value") or "").strip(), (el.get("units") or "").strip().lower(), (el.get("time") or "").strip())
    def group(key):
        entries = [found[tag] for tag in ALIASES[key] if tag in found]
        if not entries:
            return ("", "", "")
        if any(v != entries[0] for v in entries[1:]):
            raise ValueError("Conflicting XML aliases: " + key)
        return entries[0]
    if group("station")[0].upper() != "WAWP":
        raise ValueError("WAWP station identifier required")
    date, _, date_epoch = group("date")
    time, _, time_epoch = group("time")
    if date_epoch and time_epoch and date_epoch != time_epoch:
        raise ValueError("Date/time metadata mismatch")
    stamp = None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            stamp = datetime.strptime(date + " " + time, fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            pass
    if stamp is None or stamp > now:
        raise ValueError("Invalid or future displayed UTC timestamp")
    # The XML epoch appears offset from the display clock. Preserve an explicit
    # diagnostic, never use it as a UTC observation time or silently subtract 8h.
    epoch_offset = None
    source_epoch = time_epoch or date_epoch
    if source_epoch:
        try:
            epoch_offset = int(source_epoch) - int(stamp.timestamp())
        except ValueError as exc:
            raise ValueError("Invalid numeric time metadata") from exc
    out = {"schema":"titan-local-awos-v1", "station":"WAWP", "source":"AWOSnet",
           "observed_at_utc":utc_text(stamp), "wind_average_minutes":2,
           "wind_reference":"true", "units_confirmed":True,
           "timestamp_basis":"confirmed_display_clock_utc",
           "source_epoch_offset_seconds":epoch_offset,
           "qnh_hpa":None}
    for key, (allowed, lo, hi) in SPECS.items():
        value, units, raw_epoch = group(key)
        out[key] = None
        if value.upper() in MISSING:
            continue
        if units not in allowed:
            raise ValueError("Missing or unexpected source unit: " + key)
        if source_epoch and raw_epoch and raw_epoch != source_epoch:
            # Different feed metadata: withhold rather than borrowing the shared clock.
            continue
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value):
            raise ValueError("Malformed numeric value: " + key)
        number = float(value)
        if not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError("Out-of-range value: " + key)
        out[key] = number
    if all(out[k] is None for k in SPECS):
        raise ValueError("No usable measurements")
    for t, td in [("temperature_c","dewpoint_c"),("temperature_10min_c","dewpoint_10min_c")]:
        if out[t] is not None and out[td] is not None and out[td] > out[t]+0.5:
            raise ValueError("Dewpoint exceeds corresponding temperature")
    # Resolve coded report day/time separately, against the confirmed display clock.
    report = group("metar")[0]
    m = re.match(r"^(METAR|SPECI)(?: COR)? WAWP (\d{2})(\d{2})(\d{2})Z(?: |$)", report)
    if m and len(report) <= 400 and not re.search(r"\bNIL\b|[\x00-\x1f\x7f]", report):
        for days in (0, 1):
            day = stamp - timedelta(days=days)
            if day.day != int(m[2]):
                continue
            try:
                at = day.replace(hour=int(m[3]), minute=int(m[4]), second=0)
            except ValueError:
                continue
            if at <= stamp and stamp-at <= timedelta(hours=24):
                out["metar_text"] = report
                out["metar_date_utc"] = at.strftime("%Y-%m-%d")
                break
    return out

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def fetch_once():
    url = ENDPOINT + "?t=" + str(int(datetime.now(timezone.utc).timestamp()*1000))
    req = urllib.request.Request(url, headers={"Cache-Control":"no-cache","User-Agent":"TITAN-AWOSnet-local/1.0","Accept":"application/xml,text/xml"})
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=5) as response:
        if response.status != 200:
            raise ValueError("Unexpected response status")
        return response.read(MAX_BYTES+1)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("xml", nargs="?", type=Path)
    p.add_argument("--fetch", action="store_true", help="One authorized request to the fixed AWOSnet HTTP endpoint")
    p.add_argument("--allow-http", action="store_true", help="Explicit authorization to use unencrypted HTTP on a trusted network")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--confirmed-source-semantics", action="store_true")
    args = p.parse_args()
    if bool(args.xml) == args.fetch:
        p.error("Choose exactly one: local XML file or --fetch")
    if args.fetch and not args.allow_http:
        p.error("--fetch requires --allow-http")
    if not args.confirmed_source_semantics:
        p.error("Confirm displayed UTC clock, units and true-north wind before conversion")
    repo = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if out == repo or repo in out.parents:
        p.error("Output must be outside repository, on approved local storage")
    if args.fetch:
        body = fetch_once()
    else:
        with args.xml.open("rb") as f:
            body = f.read(MAX_BYTES+1)
    now = datetime.now(timezone.utc)
    sample = normalize(body, now, confirmed=True)
    if args.fetch and now-datetime.strptime(sample["observed_at_utc"],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)>timedelta(minutes=5):
        p.error("Fetched snapshot is stale (>5 min); no output written")
    with out.open("x", encoding="utf-8") as f:
        json.dump(sample, f, indent=2)
    print("Local snapshot created. No publication performed.")

if __name__ == "__main__":
    main()
