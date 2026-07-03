# Modules

- `src/scrape_openmeteo.py`: primary Open-Meteo forecast client.
- `src/scrape_meteologix.py`: legacy Meteologix scraper retained for archive review only.
- `src/backfill_historical_forecast_api.py`: Open-Meteo historical forecast backfill from 2023-01-01 onward.
- `src/db_manager.py`: SQLite schema, migrations, and ingestion helpers.
- `src/ingest_awos.py`: current hourly AWOS file ingestion.
- `src/ingest_awos_1min.py`: historical 1-minute AWOS ingestion and hourly gust aggregation.
- `src/build_qm_training_pairs.py`: materialized forecast-observation pair builder.
- `src/quantile_mapper.py`: legacy rainfall QM plus multi-parameter QM.
- `src/train_qm_multiparam.py`: trains SQLite-backed QM CDFs.
- `src/diurnal_analysis.py`: hourly and seasonal observational climatology.
- `src/vis_cloud_proxy.py`: visibility, weather phenomenon, and cloud proxy logic.
- `src/taf_core.py`: pure TAF change-group engine.
- `src/tafor_generator.py`: TAF text and narration assembly.
- `src/export_dashboard_data.py`: dashboard JSON exporter.
