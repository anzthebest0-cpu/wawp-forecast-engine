# Repository Structure

```text
wawp-forecast-engine/
├── .github/workflows/          GitHub Actions schedules and deployment
├── Archives/                   Historical forecast exports
├── data/raw_obs/               Local AWOS input archives
├── docs/                       Static dashboard plus documentation
│   └── data/                   Generated dashboard JSON
├── src/                        Pipeline source modules
├── tests/                      Unit and integration tests
├── run_pipeline.py             Main scheduled pipeline entry point
├── update_dashboard.py         Dashboard utility script
├── check_starttime.py          Start-time helper
├── wawp_forecasts.db           Active SQLite database
└── requirements.txt            Python dependencies
```
