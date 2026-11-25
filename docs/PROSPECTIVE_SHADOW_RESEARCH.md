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

Adaptive west-sector research: on the hourly 9–96h grid, compare same-local-hour 30-day observed frequency (minimum 15 distinct hourly labels, strict pre-initialization cutoff) and fixed 25% raw family vote + 75% frequency for 225–315° wind at >=2 kt. Calm valid observed speed is a non-event; missing speed or qualifying speed with missing direction is unknown. All seven model feeds and recent AWOS required. Brier verification on paired raw/frequency/blend rows; no calibrated warning probability or deterministic TAF shift. Historical month climatology remains a retrospective benchmark, not bundled station data in the public repository. Visibility is excluded.

Add a distinct 14-day diurnal temperature-minus-dewpoint spread candidate for 19–07 WITA, hourly 9–96 h bands; preserve exact paired raw spread and same-hour persistence strictly before initialization. Both constituent fields must be valid, nonnegative spread, and all seven members available. This is a derived near-saturation research diagnostic, **not a fog or visibility forecast**. No visibility, TAF or operational output changes.

Expanded April–November replay follow-up: retain all existing wind candidates and add a distinct 14-day local-hour-conditioned wind-speed correction on the hourly 9–24/25–48/49–72/73–96 h grid. Training and verification use those hourly bands rather than the earlier six-hour sample. Each candidate row embeds its own exact paired raw seven-model baseline. Missing/stale observations or inadequate training withhold the candidate; old archives remain readable. No operational wind or TAF changes.

November 25 retrospective follow-up: add core-only local-hour-conditioned mean bias candidates for pressure (21/14/7/7 days by the four lead bands) and dew point (14 days across bands). The bias is half all-hour and half matching local-hour when at least 12 matching rows exist, otherwise all-hour. Prior candidates and immutable forecasts remain valid and unchanged. The retrospective improvement is not proof of prospective skill; no automatic promotion.

## Integrated portfolio — fixed prospective comparison

The integrated backtest of November 25 found reductions in mean absolute errors for
dew point, QFF, and wind speed, but deterioration on some wet cases. Run three
simultaneous research variants on identical seven-core complete vectors:

| Variant | Definition |
| --- | --- |
| Raw | Unchanged family-equal temperature, Td, QFF, 10-minute wind speed, circular wind direction and raw six-hour rain ≥1 mm vote. |
| Corrected | Td 14-day diurnal bias, QFF 21/14/7/7-day diurnal bias, hourly wind speed 14-day diurnal bias; keep other fields raw. Bound Td by temperature, withhold physically inconsistent raw vectors. |
| Guarded | Use corrected variant unless the **forecast-side** six-hour raw family vote is ≥0.5, then use raw. This is an exploratory comparison, not an operational safety rule or an alert. |

Integrated vectors use the six-hour grid only where all six fields are available
and the full precipitation target interval lies in the future at actual final
issuance. Every vector records fit availability, training timestamps/counts,
individual fallback, cap and guard state. If a fit is ineligible, the affected
field falls back to raw and is marked inactive. The optional west-sector
diagnostic retains the short-lead 25/75 blend and raw at longer leads, and the
standalone climate/sector candidates continue to be scored separately. No
additional model acquisitions, checkpoint storage, or operational data changes.
Existing immutable archives are never retroactively populated with new vectors.

The private verification snapshot compares all three variants on a common complete
observational vector, overall and by lead band and **observed** wet/dry regime.
Observed rain ≥1 mm in the complete six-hour window defines wet for verification
only. It records paired MAE, circular MAE, Brier, guard contingencies, fit
coverage, and joint two-/three-variable improvement counts. Common missing
observations stay pending; event pair counts are not independent storms.
Earliest sample review requires 30 distinct verified UTC dates and 20 distinct
wet valid times **with all three fitted corrections active**. These are
research review minima, not evidence of sufficient skill or approval to promote.
The workflow's temporary status artifact contains only aggregate counts, not
predictions or measurement values. Original prospective runs before this
policy remain readable and eligible for their existing separate scores.

Inspect actual source coverage, candidate eligibility, and paired scores on an independent prospective period (initial review after at least 30 days, longer if rain events or matched samples are insufficient). Consider actual receipt lead and weather regimes; do not promote solely on aggregate retrospective rankings. No automatic promotion, TAF generation, gust calibration, or operational warning decision is authorized by this workflow.
