# Bulk calibration research archive (WAWP)

Original-hourly WAWP observations in the verified September baseline contain
78.0 mm in April, 155.8 mm in May, 50.2 mm in June and 17.9 mm in July 2026.
The October-only experimental training cohort was dry, so no rainfall
calibration was eligible. Expand the source archive continuously from April 2
through September 30, the beginning of the documented multi-model run archive.

Request all nine independent feed identities at the common 00/12 UTC cycles;
the two phases have 1,620 and 1,656 planned combinations. Already verified
August/September IFS runs are reused, not requested again. Existing accepted
October/November archives and their 26 explicitly missing records are preserved.
This is a deliberately sampled initialization cohort for model comparison, not
a claim to recover every 06/18 UTC run. All returned horizon hours and fields
are preserved in checksummed, independently read-back ZIP archives. Missing
fields stay null, and rates and time bounds remain enforced.

The finite main-branch workflow starts after its file is merged. Phase jobs
are serialized: a provider throttle or time-budget pause fails that phase
after saving verified partial evidence and prevents the next queued phase.
Retry failed jobs only after the provider cooldown. No deletion, production
forecast, calibration, or operational TAF changes are authorized by this job.

Research design: test 14/30/60/90-day trailing calibration windows, equal
family and inverse-error weighting, lead/diurnal-specific weighting, model
selection, mean/median additive correction, and empirical quantile mapping.
Fit only on outcomes available strictly before the forecast's initialization;
use common paired samples and block-level uncertainty, separately assess wet
and dry regimes, and retain model-version changes (notably IFS cycle 50R1
effective 12 May 2026 06 UTC per provider documentation). November results
have already been inspected and cannot be treated as a fresh final holdout.
Archive initialization is not historical dissemination time. Actual availability
and real-time input compatibility require separate shadow verification.

Provider documentation: https://open-meteo.com/en/docs/single-runs-api

## Targeted archive repair (24 November 2026)

The first full extension accepted 2,838 of 3,276 planned common-cycle keys.
The 438 missing keys comprised 59 transport, 211 unavailable, and 168
validation failures. Of the last group, 161 were GEM. Direct representative
queries of GEM 2026-05-29, 2026-07-15 and 2026-08-15 00 UTC returned
241 time labels but zero temperature, wind speed, MSL pressure or
precipitation values. Other sampled rejected runs likewise had genuinely
missing core data (IFS 9 km 2026-06-23 00 UTC, and CMA 2026-04-12 12 UTC
with only six temperature and wind values). Do not relax the quality gate.

The targeted repair retries only transport (including prior rate-limit
pauses) and non-GEM validation errors; original accepted responses are
reused byte-for-byte. Deterministic failures are carried in new manifests
with original reasons. The bounded main push uses targeted mode, while a
manual dispatch with `retry_only: false` remains available for a deliberate
full retry if provider archive coverage improves. Evaluate the earlier
period with a predeclared common seven-feed cohort if GEM/JMA coverage is
insufficient, and report the nine-feed cohort separately. No operational
weights or TAF generation are changed by archive repair.
