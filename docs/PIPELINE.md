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

## Scheduled Run

GitHub Actions runs `python run_pipeline.py` on operational schedules and publishes `docs/` to GitHub Pages.
