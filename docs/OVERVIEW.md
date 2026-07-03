# Overview

The WAWP forecast engine is a Python-based system that ingests meteorological forecasts and AWOS observations, stores them in SQLite, generates TAF guidance, verifies model skill, trains quantile-mapping corrections, and publishes dashboard-ready artifacts.

## Goals

- Automate WAWP TAF guidance generation.
- Provide reproducible model assessment and weighting.
- Preserve historical forecast and observation data for retraining.
- Publish operational JSON artifacts for the static dashboard.
