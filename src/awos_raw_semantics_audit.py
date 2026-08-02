"""Audit raw WAWP AWOS rain fields without modifying the operational database."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.awos_hourly_parser import read_hourly_awos
from src.ingest_awos_1min import parse_1min_file
from src.meteorological_contract import AWOS_MINUTE_RAIN_SEMANTICS, SEMANTICS_VERSION


def audit_raw_awos(
    hourly_file: Path,
    minute_files: list[Path],
    database: Path | None = None,
) -> dict:
    hourly = (
        read_hourly_awos(str(hourly_file))[['UTC', 'Rain']]
        .drop_duplicates('UTC', keep='last')
        .rename(columns={'Rain': 'hourly_rain_mm'})
        .set_index('UTC')
        .sort_index()
    )
    minute_frames = [parse_1min_file(str(path))[['UTC', 'Rain']] for path in minute_files]
    minute = (
        pd.concat(minute_frames, ignore_index=True)
        .drop_duplicates('UTC', keep='last')
        .rename(columns={'Rain': 'minute_rain_mm'})
        .set_index('UTC')
        .sort_index()
        if minute_frames
        else pd.DataFrame(columns=['minute_rain_mm'], index=pd.DatetimeIndex([]))
    )

    boundary = hourly.join(minute, how='inner').dropna()
    boundary_error = (boundary['hourly_rain_mm'] - boundary['minute_rain_mm']).abs()
    grouped = minute['minute_rain_mm'].groupby(minute.index.floor('h')).agg(
        minute_count='count', minute_sum='sum', minute_max='max', minute_first='first', minute_last='last'
    )
    forward = grouped.join(hourly, how='inner').dropna(subset=['hourly_rain_mm'])

    minute_days = minute.groupby(minute.index.floor('D')).size() if len(minute) else pd.Series(dtype=int)
    interval_gust = pd.DataFrame(columns=['derived_gust'])
    minute_gust_value_count = 0
    if len(minute):
        gust_source = pd.concat(
            [parse_1min_file(str(path))[['UTC', 'WGS']] for path in minute_files],
            ignore_index=True,
        ).drop_duplicates('UTC', keep='last')
        gust_source['interval_end_utc'] = gust_source['UTC'].dt.ceil('h')
        minute_gust_value_count = int(gust_source['WGS'].notna().sum())
        interval_gust = (
            gust_source.groupby('interval_end_utc')['WGS']
            .max()
            .rename('derived_gust')
            .to_frame()
        )

    gust_reconciliation = {
        'database_provided': bool(database),
        'comparable_hours': 0,
        'match_hours': 0,
        'mismatch_hours': 0,
        'maximum_absolute_difference_kt': None,
    }
    if database and database.is_file() and not interval_gust.empty:
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            stored = pd.read_sql_query(
                """
                SELECT obs_time, wind_gust_max
                FROM awos_observations
                WHERE wind_gust_max IS NOT NULL
                ORDER BY obs_time
                """,
                conn,
            )
        stored['interval_end_utc'] = pd.to_datetime(stored['obs_time'], errors='coerce')
        stored['stored_gust'] = pd.to_numeric(stored['wind_gust_max'], errors='coerce')
        compared = stored.set_index('interval_end_utc')[['stored_gust']].join(interval_gust, how='inner').dropna()
        gust_error = (compared['stored_gust'] - compared['derived_gust']).abs()
        gust_reconciliation.update({
            'comparable_hours': int(len(compared)),
            'match_hours': int(gust_error.le(0.051).sum()),
            'mismatch_hours': int(gust_error.gt(0.051).sum()),
            'maximum_absolute_difference_kt': (
                round(float(gust_error.max()), 4) if len(gust_error) else None
            ),
        })

    def matches(series: pd.Series) -> int:
        return int(series.abs().le(0.051).sum())

    return {
        'semantics_version': SEMANTICS_VERSION,
        'generated_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        'hourly_file': str(hourly_file.resolve()),
        'minute_file_count': len(minute_files),
        'hourly_rows': int(len(hourly)),
        'minute_rows': int(len(minute)),
        'minute_duplicate_timestamp_count': int(
            sum(len(parse_1min_file(str(path))) for path in minute_files) - len(minute)
        ),
        'minute_day_count': int(len(minute_days)),
        'incomplete_minute_day_count': int(minute_days.lt(1440).sum()),
        'minute_gust_value_count': minute_gust_value_count,
        'paired_hour_boundaries': int(len(boundary)),
        'boundary_exact_match_count': matches(boundary_error),
        'boundary_match_rate': round(matches(boundary_error) / max(1, len(boundary)), 6),
        'forward_clock_hour_comparison': {
            'paired_hours': int(len(forward)),
            'sum_match_count': matches(forward['minute_sum'] - forward['hourly_rain_mm']),
            'max_match_count': matches(forward['minute_max'] - forward['hourly_rain_mm']),
            'first_match_count': matches(forward['minute_first'] - forward['hourly_rain_mm']),
            'last_match_count': matches(forward['minute_last'] - forward['hourly_rain_mm']),
        },
        'conclusion': AWOS_MINUTE_RAIN_SEMANTICS,
        'safe_use': 'Use hourly RA36 for amounts; never sum minute RA snapshots.',
        'gust_reconciliation': gust_reconciliation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hourly-file', type=Path, required=True)
    parser.add_argument('--minute-directory', type=Path, required=True)
    parser.add_argument('--minute-pattern', default='000OneMinute.*.dat')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    minute_files = sorted(args.minute_directory.glob(args.minute_pattern))
    payload = audit_raw_awos(args.hourly_file, minute_files, args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    main()
