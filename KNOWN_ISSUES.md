# Known Issues

## Historical Forecast API Coverage

The Open-Meteo historical backfill has been completed with `src/backfill_historical_forecast_api.py` using the official Historical Forecast API endpoint:

```text
https://historical-forecast-api.open-meteo.com/v1/forecast
```

Coverage now starts at `2023-01-01 00:00 UTC` and runs through the latest ingested AWOS historical date, currently `2026-06-30 23:00 UTC`.

The active historical model set is:

- `ECMWF_HRES`
- `GFS_GLOBAL`
- `GFS_SEAMLESS`
- `ICON_GLOBAL`
- `ICON_SEAMLESS`
- `GEM_GLOBAL`
- `GEM_SEAMLESS`
- `CMA_GRAPES_GLOBAL`
- `JMA_GSM`
- `BOM_ACCESS_GLOBAL`
- `METEOFRANCE_ARPEGE_WORLD`
- `UKMO_GLOBAL_10KM`
- `ERA5_SEAMLESS`

Current caveats:

- The Historical Forecast API provides a continuous historical forecast time series, not archived individual model-run lead horizons. The trained CDFs therefore represent the available historical forecast stream and are currently concentrated in the `L1_0_6h` lead bucket.
- `JMA_GSM` does not currently have an enabled wind-gust CDF because the available historical forecast stream did not provide usable gust samples for that model.
- `src/backfill_openmeteo.py` remains in the tree as the Single Runs API experiment, but it is not the active backfill path.
