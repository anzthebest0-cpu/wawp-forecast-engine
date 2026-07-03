# Automation & CI

The workflow in `.github/workflows/run_wawp_engine.yml` installs Python dependencies, runs the pipeline, commits generated dashboard data, and deploys GitHub Pages.

The scheduled times are aligned to WAWP TAF issuance windows:

- 00:30 UTC
- 06:30 UTC
- 12:30 UTC
- 18:30 UTC

The workflow should keep database and generated data updates serialized to avoid concurrent writes.
