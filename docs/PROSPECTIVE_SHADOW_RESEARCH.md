# Prospective WAWP shadow experiment

This experiment is separate from the raw-model dashboard, rolling database, operational forecasts, and TAF. It never updates operational weights or enables calibration in production. It samples explicit 00/12 UTC model initializations after an eight-hour allowance, at approximately 16:20/04:20 WITA. A delayed or manual run uses the latest eligible cycle; already issued forecasts are not replaced. Actual issue timestamps and receipt-relative lead times are stored, and past targets (including partially elapsed rain windows) are excluded.

## Predeclared candidates

The seven-model core is CMA, IFS 0.25°, IFS 9 km, GFS, ICON, ARPEGE, and UKMO. The related IFS members share one family's baseline weight. The two optional models are tested independently, not automatically added to production.

| Observation/target | Candidates | Baselines |
| --- | --- | --- |
| Temperature | Quantile mapping, core and core + GEM | Paired raw core, same-cohort raw, same-hour persistence |
| MSL pressure (AWOS QFF) | Median bias, core and core + JMA | Same |
| Dew point | MAE-weighted blend, core and core + JMA | Same |
| Wind speed | Mean bias, core | Same |
| Wind direction | Circular MAE-weighted blend, core | Same |
| Six-hour rain >= 1 mm | Raw family-weighted model exceedance fraction only | Same-cohort raw and same-hour persistence |

All continuous candidates compare rolling 14- and 30-day fitting windows by initialization-relative lead band (12–24, 30–48, 54–72, and 78–96 hours, sampled every six hours). Fit outcomes must precede the initialization. At least 40 complete paired rows, at least 70% distinct-day coverage, and an observation within 48 hours are required. Missing models, observations, or inadequate fitting support withhold the applicable candidate; the remaining available raw baselines are retained. The rain exceedance fraction is not a calibrated probability, and rain verification uses Brier score with event counts. Other targets use MAE (circular for direction). Persistence comparisons are separately paired on available observations. Archive data acquired before actual issuance may be used for fitting, but no future observation or retroactive forecast can enter the prospective scores.

## Storage and observation maintenance

Within the existing private archive root, `shadow-research/source` contains one original-byte compressed model-source bundle per cycle, `shadow-research/forecasts` contains immutable SHA-256-addressed forecasts, and `shadow-research/verification` holds dated score snapshots. Historical training downloads are limited to the recent 34-day input window; only status counts are uploaded as a temporary GitHub Actions artifact. The source and forecast are checksum-verified on readback. A 1 GiB free-space gate withholds a new run if storage is low. No operational data is deleted, re-encoded, or overwritten.

Keep the station's original `000HLY.YYYYMM.dat` file in `awos-inbox` updated as new hours become available. Replace/update the matching monthly file rather than uploading a duplicate with the same name. The workflow reads it at each run; it cannot independently fetch observations from AWOS. Calibration pauses when observations are stale; verification remains pending for missing observed hours. A new monthly file is needed when the reporting month changes.

## Review

Inspect actual source coverage, candidate eligibility, and paired scores on an independent prospective period (initial review after at least 30 days, longer if rain events or matched samples are insufficient). Consider actual receipt lead and weather regimes; do not promote solely on aggregate retrospective rankings. No automatic promotion, TAF generation, gust calibration, or operational warning decision is authorized by this workflow.
