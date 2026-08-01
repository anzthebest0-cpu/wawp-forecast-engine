# Pipeline Execution

## Manual Run

```bash
python check_starttime.py
python run_pipeline.py
```

Optional historical setup:

```bash
python src/ingest_awos_1min.py --directory data/raw_obs/oneminute
python src/backfill_historical_forecast_api.py
python src/build_qm_training_pairs.py
python src/train_qm_multiparam.py
python src/diurnal_analysis.py
```

## AWOS Release Inbox

Raw hourly and one-minute AWOS `.dat` files can be uploaded to the
`awos-inbox` GitHub Release. Every pipeline run downloads those assets and
processes only filename/checksum combinations that are not already recorded in
`awos_source_ingest_manifest`.

The ingestion order is hourly first, minute second, then hourly maximum-gust
aggregation when the minute source contains populated WGS values. A missing WGS
series is reported and is never replaced with a wind-speed proxy. The following
compaction step preserves the hourly observations, any derived gusts, and the
ingestion manifest while removing raw minute rows from the rolling database.
The original `.dat` assets remain in `awos-inbox` as the reprocessing archive.

Local equivalent:

```bash
python -m src.ingest_awos_inbox \
  --directory artifacts/awos-inbox \
  --db wawp_forecasts.db \
  --source-tag awos-inbox \
  --report artifacts/awos-inbox/ingestion-report.json
```

## Scheduled Run

GitHub Actions runs `python run_pipeline.py` on operational schedules and publishes `docs/` to GitHub Pages.
