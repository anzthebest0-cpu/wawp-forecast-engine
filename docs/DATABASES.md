# Databases

The active database is `wawp_forecasts.db`.

## Core Tables

- `meteologix_forecasts`: legacy-compatible forecast rows used by current dashboard consensus.
- `openmeteo_forecasts`: Open-Meteo forecast rows with CAPE, CIN, visibility, cloud, and lead-time fields.
- `awos_observations`: hourly AWOS observations.
- `awos_observations_1min`: 1-minute AWOS observations.
- `qm_training_pairs`: materialized forecast-observation pairs for QM training.
- `qm_cdfs`: trained quantile mapping tables by model, parameter, and lead bucket.

`meteologix_data.db` is an older/empty sibling retained for compatibility.
