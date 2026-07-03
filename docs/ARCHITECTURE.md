# Architecture

## Logical Layers

1. Ingestion: Open-Meteo forecast API and AWOS files.
2. Persistence: SQLite tables for forecasts, observations, QM CDFs, and training pairs.
3. Processing: consensus generation, model weighting, quantile mapping, and diurnal analysis.
4. Forecasting: TAF base group and change-group generation.
5. Assessment: forecast-observation pairing and skill metrics.
6. Presentation: JSON and static dashboard assets under `docs/`.
7. Automation: GitHub Actions scheduled pipeline runs.

```mermaid
flowchart TD
    A["External forecast APIs"] --> B["Ingestion clients"]
    C["AWOS hourly and 1-minute files"] --> B
    B --> D["SQLite storage"]
    D --> E["QM training pairs"]
    E --> F["Multi-parameter QM CDFs"]
    D --> G["Dynamic weights and metrics"]
    F --> H["Consensus forecast"]
    G --> H
    H --> I["TAF generator"]
    D --> J["Diurnal climatology"]
    I --> K["Dashboard JSON"]
    J --> K
```
