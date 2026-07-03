# Data Flow

```text
Open-Meteo / AWOS files
        |
src/scrape_openmeteo.py, src/ingest_awos*.py
        |
wawp_forecasts.db
        |
training pairs, dynamic weights, QM CDFs, diurnal analysis
        |
consensus forecast
        |
TAF generator
        |
docs/data/*.json
        |
GitHub Pages dashboard
```

The pipeline is designed to be idempotent. Forecast and observation tables use uniqueness constraints so repeated runs skip duplicates.
