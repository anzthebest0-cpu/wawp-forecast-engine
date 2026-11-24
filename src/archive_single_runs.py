"""Recover immutable provider Single Runs responses, separately from live collections.

Every successful run is a byte-identical ZIP member with a SHA-256 and an
explicit requested UTC initialization. Retrieval time is not historic issue
time. Never insert these into the live forecast collection table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.model_acquisition import CORE, FIELDS, MAX_RESPONSE_BYTES
from src.model_registry import PREVIEW_MODEL_REGISTRY, IFS_9KM_MODEL
from src.scrape_openmeteo import LATITUDE, LONGITUDE, LOCATION_NAME

URL = "https://single-runs-api.open-meteo.com/v1/forecast"
CONTRACT = "wawp-single-runs-archive-v1"
UTC = timezone.utc
IFS_MODELS = ("ECMWF_HRES", IFS_9KM_MODEL)


def run_plan(start: date, end: date, gap_start: date, cycle_hours=None):
    """Before gap_start retrieve both IFS variants; afterward all nine feeds."""
    day = start
    while day <= end:
        for model, meta in PREVIEW_MODEL_REGISTRY.items():
            if day < gap_start and model not in IFS_MODELS:
                continue
            for hour in range(0, 24, int(meta.provider_update_frequency_hours)):
                if cycle_hours is not None and hour not in cycle_hours:
                    continue
                yield model, datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
        day += timedelta(days=1)


def request_url(model, run):
    meta = PREVIEW_MODEL_REGISTRY[model]
    # Endpoint returns a grid starting at run, including +0; request through
    # the stated horizon. Short-cycle products may end early; don't fill nulls.
    return URL + "?" + urllib.parse.urlencode({
        "latitude": LATITUDE, "longitude": LONGITUDE,
        "models": meta.openmeteo_id, "run": run.strftime("%Y-%m-%dT%H:%M"),
        "hourly": ",".join(FIELDS), "forecast_hours": meta.forecast_horizon_hours+1,
        "timezone": "UTC", "wind_speed_unit": "kn", "precipitation_unit": "mm",
    })


def inspect_response(body, model, run):
    """Reject malformed identity, time axis and primary-field unit failures."""
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get("error") or payload.get("utc_offset_seconds") != 0:
        raise ValueError("provider error or non-UTC response")
    for name, expected in (("latitude", LATITUDE), ("longitude", LONGITUDE)):
        value = payload.get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value-expected)>1:
            raise ValueError("unexpected provider grid coordinate")
    h, units = payload["hourly"], payload["hourly_units"]
    timestamps = h["time"]
    times = [datetime.fromisoformat(t) for t in timestamps]
    if not times or any(t.tzinfo is not None or t.minute or t.second or t.microsecond for t in times):
        raise ValueError("unexpected timestamp format")
    times = [t.replace(tzinfo=UTC) for t in times]
    if times[0] != run or any((b-a) != timedelta(hours=1) for a,b in zip(times,times[1:])):
        raise ValueError("axis does not resolve to requested initialization")
    if not 24 <= len(times) <= PREVIEW_MODEL_REGISTRY[model].forecast_horizon_hours+1:
        raise ValueError("unexpected forecast horizon")
    counts, last_populated, unit_issues = {}, {}, []
    for field, (_, expected_unit) in FIELDS.items():
        values = h.get(field)
        if values is None:
            counts[field] = 0
            continue
        if not isinstance(values,list) or len(values)!=len(times):
            raise ValueError(f"misaligned field {field}")
        if any(v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)) for v in values):
            raise ValueError(f"invalid numeric value in {field}")
        nonnull = [i for i,v in enumerate(values) if v is not None]
        counts[field] = len(nonnull)
        if nonnull:
            last_populated[field] = nonnull[-1]
            if units.get(field) != expected_unit:
                unit_issues.append(field)
    if any(field in unit_issues for field in CORE):
        raise ValueError("primary parameter unit mismatch")
    if counts.get("temperature_2m",0)<24 or counts.get("wind_speed_10m",0)<24:
        raise ValueError("insufficient primary data")
    return {"first_valid_utc": times[0].isoformat(), "last_valid_utc": times[-1].isoformat(),
            "hours_returned": len(times), "nonnull_counts": counts,
            "last_populated_lead_hours": last_populated, "unit_issues": unit_issues,
            "grid_latitude": payload["latitude"], "grid_longitude": payload["longitude"],
            "grid_elevation_m": payload.get("elevation"), "units": units}


class Pacer:
    """Global request-start rate; never bypass a provider 429."""
    def __init__(self, interval=0.9):
        self.interval, self.next, self.lock, self.stop = interval, 0.0, threading.Lock(), threading.Event()

    def wait(self):
        with self.lock:
            if self.stop.is_set():
                return False
            delay = max(0, self.next-time.monotonic())
            if delay:
                time.sleep(delay)
            if self.stop.is_set():
                return False
            self.next = time.monotonic()+self.interval
            return True


def fetch_one(model, run, pacer):
    url = request_url(model,run)
    info = {"model": model, "provider_model_id": PREVIEW_MODEL_REGISTRY[model].openmeteo_id,
            "initialization_utc":run.isoformat(), "request_url":url}
    for attempt in range(2):
        if not pacer.wait():
            return {**info,"status":"not_attempted_rate_limit"},None
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"wawp-single-run-archive/1.0"})
            with urllib.request.urlopen(req,timeout=35) as response:
                body=response.read(MAX_RESPONSE_BYTES+1)
            if len(body)>MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            detail = inspect_response(body,model,run)
            stamp = datetime.now(UTC).isoformat()
            return {**info,**detail,"status":"accepted","retrieved_at_utc":stamp,
                    "payload_bytes":len(body),"sha256":hashlib.sha256(body).hexdigest()},body
        except urllib.error.HTTPError as exc:
            reason=exc.read(500).decode(errors="replace")
            if exc.code==429:
                pacer.stop.set()
                return {**info,"status":"rate_limited","http":429,"reason":reason},None
            if exc.code in (400,404,422):
                return {**info,"status":"provider_unavailable_or_rejected","http":exc.code,"reason":reason},None
            error=f"HTTP {exc.code}: {reason}"
        except (ValueError, KeyError, TypeError) as exc:
            return {**info,"status":"validation_rejected","reason":str(exc)},None
        except (OSError,TimeoutError) as exc:
            error=str(exc)
        if attempt==0:
            time.sleep(2)
    return {**info,"status":"transport_error","reason":error},None


def recover_batch(start,end,gap_start,out,*,workers=1,interval=1.5,resume=(),cycle_hours=None):
    out=Path(out)
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    planned=list(run_plan(start,end,gap_start,cycle_hours))
    target={(model,run.isoformat()) for model,run in planned}
    prior={}
    for previous in resume:
        manifest=verify_bundle(previous)
        for record in manifest['records']:
            key=(record['model'],record['initialization_utc'])
            if key not in target or record['status']!='accepted':
                continue
            if key in prior and prior[key][0]['sha256']!=record['sha256']:
                raise ValueError(f'conflicting archived responses for {key}; retain and audit originals')
            prior[key]=(record,previous)
    pending=[(model,run) for model,run in planned if (model,run.isoformat()) not in prior]
    pacer=Pacer(interval)
    records=[]
    with zipfile.ZipFile(out,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z, ThreadPoolExecutor(max_workers=workers) as executor:
        for info,previous in prior.values():
            with zipfile.ZipFile(previous) as source:
                z.writestr(info['member'],source.read(info['member']))
            records.append(info)
        futures={executor.submit(fetch_one,model,run,pacer):(model,run) for model,run in pending}
        done=accepted=len(prior)
        for future in as_completed(futures):
            info,body=future.result()
            done+=1
            if body is not None:
                member=f"{info['model']}/{info['initialization_utc'][:10]}/{info['initialization_utc'][11:13]}Z.json"
                z.writestr(member,body)
                info["member"]=member
                accepted+=1
            records.append(info)
            if done%25==0 or done==len(planned):
                print(f"{start}..{end}: {done}/{len(planned)} checked, {accepted} accepted",flush=True)
        manifest={"contract":CONTRACT,"location":LOCATION_NAME,"start_date":str(start),"end_date":str(end),
                  "gap_start":str(gap_start),"policy":"provider archived run initialization, not observed historic dissemination time; no live DB writes",
                  "records":sorted(records,key=lambda x:(x["initialization_utc"],x["model"]))}
        if cycle_hours is not None:manifest['cycle_hours']=list(cycle_hours)
        z.writestr("manifest.json",json.dumps(manifest,separators=(",",":"),ensure_ascii=False))
    verify_bundle(out)
    print(f"VERIFIED {out.name}: {accepted}/{len(planned)} accepted, {out.stat().st_size} bytes",flush=True)
    return manifest


def verify_bundle(path):
    with zipfile.ZipFile(path) as z:
        data=json.loads(z.read("manifest.json"))
        records=data["records"]
        keys={(r["model"],r["initialization_utc"]) for r in records}
        hours=data.get('cycle_hours')
        if hours is not None and hours!=[0,12]:raise ValueError('unsupported common-cycle cohort')
        expected={(model,run.isoformat()) for model,run in run_plan(date.fromisoformat(data["start_date"]),date.fromisoformat(data["end_date"]),date.fromisoformat(data["gap_start"]),hours)}
        assert data["contract"]==CONTRACT and keys==expected and len(keys)==len(records)
        members={"manifest.json"}
        for r in records:
            if r["status"]!="accepted":
                continue
            body=z.read(r["member"])
            assert len(body)==r["payload_bytes"] and hashlib.sha256(body).hexdigest()==r["sha256"]
            detail=inspect_response(body,r["model"],datetime.fromisoformat(r["initialization_utc"]))
            assert all(r[k]==v for k,v in detail.items())
            members.add(r["member"])
        assert set(z.namelist())==members and len(z.namelist())==len(members)
        return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start",type=date.fromisoformat,required=True)
    p.add_argument("--end",type=date.fromisoformat,required=True)
    p.add_argument("--gap-start",type=date.fromisoformat,default=date(2026,10,1))
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--workers",type=int,default=1)
    p.add_argument("--interval",type=float,default=1.5)
    p.add_argument("--resume",type=Path,action="append",default=[])
    p.add_argument("--common-cycles",action="store_true",help="Restrict the plan to 00/12 UTC for comparable nine-model research")
    args=p.parse_args()
    if not 1<=args.workers<=8 or args.interval<0.75 or args.end<args.start or (args.end-args.start).days>13:
        p.error("Use 1–8 workers, at least 0.75 s request spacing, no more than 14 days per batch")
    report=recover_batch(args.start,args.end,args.gap_start,args.output,workers=args.workers,interval=args.interval,resume=args.resume,cycle_hours=(0,12) if args.common_cycles else None)
    if any(r['status'] in {'rate_limited','not_attempted_rate_limit'} for r in report['records']):
        raise SystemExit(75)


if __name__=="__main__":
    main()
