# Operational Database Compaction

## Purpose

`wawp_forecasts.db` is a rolling GitHub Release asset. It must stay small enough
to download, integrity-check, update, and upload during every scheduled
pipeline run. The complete raw one-minute AWOS archive is valuable, but keeping
millions of raw rows in that same rolling asset makes the operational workflow
slow and vulnerable to failed uploads.

The compaction tool first creates a separate candidate database. The production
workflow promotes that candidate only after its validation succeeds; it never
edits the restored release file in place.

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

Run this locally to inspect or rebuild a candidate outside GitHub:

```powershell
python src/build_compact_operational_db.py --source wawp_forecasts.db
```

The result and its JSON validation report are written below
`artifacts/operational/`, which is ignored by Git. The builder refuses to
overwrite an existing candidate unless `--overwrite` is supplied.

## Production workflow

Every normal GitHub workflow run now follows this order:

```text
restore release database
  -> build and validate compact candidate
  -> replace runner-local working copy with candidate
  -> run the normal forecast/QM/dashboard pipeline on the candidate
  -> integrity-check and upload new compact release asset
  -> retain the immediately previous release asset as rollback
```

This means the ordinary pipeline is the fresh operational smoke test for the
compact database. If compaction fails, the run stops before the pipeline or
release upload can modify the live asset.

For a downloadable JSON validation report, use **Actions -> WAWP Meteologix
Engine -> Run workflow**, tick **Upload the compact-database validation report
for this run**, then start it manually. The report is retained as an Actions
artifact for seven days.

## Acceptance checks before release replacement

1. The report says `"valid": true`.
2. Every retained table has the exact same row count as the source.
3. `awos_observations` has identical first/latest timestamps, rain total,
   gust-row count, and maximum gust.
4. Open-Meteo row count, date range, model count, and historical-row count
   match.
5. The normal pipeline runs successfully against the candidate, producing its
   dashboard export, QM handling, weights, and TAF guidance.
6. The new release asset uploads successfully.
7. The immediately previous release asset remains available as rollback; the
   complete raw-minute archive remains outside the rolling release database.

## Recovery

If compaction fails, the workflow exits before altering the release asset. If a
new compact release later proves unsuitable, restore the immediately previous
release asset as `wawp_forecasts.db` and rerun the workflow. The local full
database and original monthly minute AWOS files remain the long-term source
archive for detailed reprocessing.
