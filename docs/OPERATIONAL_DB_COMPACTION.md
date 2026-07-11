# Operational Database Compaction

## Purpose

`wawp_forecasts.db` is a rolling GitHub Release asset. It must stay small enough
to download, integrity-check, update, and upload during every scheduled
pipeline run. The complete raw one-minute AWOS archive is valuable, but keeping
millions of raw rows in that same rolling asset makes the operational workflow
slow and vulnerable to failed uploads.

The compaction tool creates a separate candidate database. It does not modify
the current database, publish a Release asset, or remove source files.

## What the candidate keeps

- All Open-Meteo forecast rows and model-run metadata.
- All hourly AWOS observations, including `wind_gust_max` derived from minute
  data.
- All QM CDFs, training pairs, correction audit rows, weights, and dashboard
  inputs.
- Every index and table schema required by the live pipeline.

## What it excludes from the rolling asset

`awos_observations_1min` is retained as an empty compatibility table. Its raw
historical rows remain in the original database and in the supplied monthly
AWOS source files. The candidate records the number of excluded rows in its
`operational_data_retention` manifest.

The live pipeline does not currently ingest minute files automatically, so a
future minute-data process must derive hourly/event summaries before adding
anything to the rolling database.

## Build a local candidate

Run this locally, never in the scheduled GitHub workflow:

```powershell
python src/build_compact_operational_db.py --source wawp_forecasts.db
```

The result and its JSON validation report are written below
`artifacts/operational/`, which is ignored by Git. The builder refuses to
overwrite an existing candidate unless `--overwrite` is supplied.

## Acceptance checks before release replacement

1. The report says `"valid": true`.
2. Every retained table has the exact same row count as the source.
3. `awos_observations` has identical first/latest timestamps, rain total,
   gust-row count, and maximum gust.
4. Open-Meteo row count, date range, model count, and historical-row count
   match.
5. Run `python run_pipeline.py` against a copy of the candidate and confirm
   dashboard export, QM artifact loading, weights, and TAF guidance complete.
6. Compare the candidate's current consensus and TAF result with the source
   for the same fetched inputs.
7. Keep the existing Release asset and local source database as recovery
   copies through several successful scheduled runs.

Only after all seven checks pass should a separate, reviewed change teach the
GitHub workflow to publish the compact asset. That future change must upload
the new asset first and retain the existing release asset as fallback.

## Recovery

No recovery action is needed while evaluating a candidate: the source database
and GitHub Release database are unchanged. If a candidate fails any check,
discard only the candidate under `artifacts/operational/` and investigate the
report.
