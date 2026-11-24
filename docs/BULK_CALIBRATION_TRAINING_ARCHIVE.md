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
