# Operational Forecast-Time Provenance

Status: Batch 7 hardening. Operational residuals and event timing remain
observe-only.

## Timestamp Contract

Open-Meteo Forecast API requests use `Asia/Makassar`, so the returned hourly
timestamps and the compatibility column `forecast_time` are WITA. AWOS hourly
timestamps are treated as UTC. Comparing those text values directly shifts the
physical verification by eight hours.

The canonical contract is now:

```text
forecast_time       = compatibility/display time
valid_time_utc      = physical forecast valid time used for verification
run_init_utc        = collection-cycle proxy, not confirmed model initialization
scraped_at          = actual UTC collection time
forecast_time_basis = source interpretation of forecast_time
```

Contract version: `openmeteo-valid-time-utc-v1`.

New operational rows store `valid_time_utc` explicitly. Existing operational
rows are backfilled from `forecast_time - 8 hours`. Existing continuous
historical rows are not rewritten because their `forecast_time` is already UTC;
the query contract uses it as a fallback. This avoids duplicating timestamps
across the large historical archive.

Only explicit operational UTC values are indexed. The historical `NULL`
fallback rows are excluded from that partial index so the time migration does
not create a second large timestamp index over the continuous archive.

Diurnal regime labels always use WITA. Historical UTC valid times are shifted
to WITA for `morning_06_11`, `convective_12_19`, and `night_20_05`; operational
Forecast API display times are already WITA.

## Pairing Gate

Operational forecast-observation pairs require:

- equal location;
- `valid_time_utc = obs_time`;
- non-negative lead hour;
- active, de-duplicated model label;
- no shifted-field or hard physical-range anomaly.

The residual artifact records both the corrected pair count and the old naive
text-join count. Artifacts without the current pairing-contract version are not
preserved, regardless of their apparent sample count.

## Timing-Weight Containment

`AdvancedEnsembleWeighter.calculate_timing_weights()` is retained for
reproducibility, but defaults to `deprecated_observe_only` and returns the base
weights unchanged. Its old independent peak-window scans are not the approved
one-to-one episode verifier.

`WAWP_LEGACY_TIMING_WEIGHTING_MODE=enabled` exists only for controlled tests
and is not an operational recommendation.

## Promotion Boundary

Correct timestamp pairing fixes the evidence stream; it does not itself prove
skill. Operational residuals remain disabled until sufficient new pairs pass
chronological validation, native-cadence review, meteorological approval, and a
manual promotion with rollback.
