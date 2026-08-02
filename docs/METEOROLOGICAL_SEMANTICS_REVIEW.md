# WAWP Meteorological Semantics Review

Status: Batch 5 complete for shadow verification. This review does not approve
automatic weighting or TAF issuance changes.

## Executive Standing

WAWP can validly compare hourly AWOS `RA36` with Open-Meteo `precipitation`
after converting both timestamps to the same time zone. Both represent the
amount in the preceding hour and are labelled at the end of that interval.

The previous three-hour transformation and forward-looking gust aggregation
were inconsistent with that convention. The shadow verifier and AWOS gust
aggregation now use interval-ending windows.

The most important remaining blockers are:

1. Normalize operational forecast valid time from WITA to UTC before pairing.
2. Quarantine physically invalid forecast rows from July 2026.
3. Select forecast runs with a leakage-safe as-of rule.
4. Validate rainfall thresholds by independent replay rather than treating any
   current threshold as an aviation standard.
5. Reduce timing claims for model hours interpolated from 3- or 6-hourly data.

## AWOS Rain Semantics

The hourly source header declares `RA36` in millimetres. The parser selects the
last column and divides the integer representation by ten.

July 2026 provides direct cross-grain evidence:

- 743 hourly timestamps had a matching one-minute record at the same boundary.
- In all 743 cases, one-minute `RA` at the exact hour boundary equalled hourly
  `RA36` to the source precision.
- Minute values remain unchanged or evolve gradually through a rain episode.
- Summing minute values grossly exaggerates the amount.

Standing:

```text
hourly RA36 at time T = rolling one-hour rain accumulation ending at T
minute RA at time t   = snapshot of that rolling one-hour accumulation at t
```

This is strongly confirmed empirically for the supplied WAWP files. It should
still be checked against the AWOS manufacturer/export specification when that
document becomes available.

Safe usage:

- Use hourly `RA36` for hourly and multi-hour rainfall accumulation.
- Do not sum one-minute `RA` values.
- Retain one-minute `RA` only for timing/QC evidence.
- A three-hour period ending 03 UTC sums reports labelled 01, 02, and 03 UTC.

## AWOS Gust Semantics

Open-Meteo defines `wind_gusts_10m` as the maximum in the preceding hour. WAWP
therefore aggregates minute gusts over `(T-1h, T]` and stores the result on the
hourly observation labelled `T`.

The former implementation used `[T, T+1h)`, shifting observed gust maxima one
hour earlier relative to the model product. This has been corrected for future
ingestion. Existing derived gust rows must be rebuilt from retained minute
assets before gust verification is trusted.

## Open-Meteo Precipitation Semantics

The official [Forecast API documentation](https://open-meteo.com/en/docs)
defines:

| Field | Interval | Meaning |
|---|---|---|
| `precipitation` | preceding-hour sum | total precipitation: rain, showers, and snow |
| `rain` | preceding-hour sum | large-scale rain component |
| `showers` | preceding-hour sum | convective precipitation component |
| `precipitation_probability` | preceding-hour probability | probability of more than 0.1 mm |
| `wind_gusts_10m` | preceding-hour maximum | maximum 10 m gust |

WAWP intentionally stores `precipitation` in the compatibility column `rain`.
Audit and dashboard labels should call it total precipitation. It must not be
added to `showers` again.

The probability field is specifically an exceedance probability for 0.1 mm in
the preceding hour. It is not a probability of exceeding 1.0 mm or 10.0 mm.
The former legacy alias that copied it into `Prob_Precip_1.0` has been removed.

## Historical Forecast Meaning

Open-Meteo describes the [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api)
as a continuous series stitched from the first hours of successive runs. It is
appropriate for a local historical prior, but not for lead-aware verification.

The same documentation now distinguishes:

- Previous Runs API for fixed 1-7 day lead offsets.
- Single Runs API for complete archived model runs.

This supports WAWP's current architecture: historical prior from the continuous
stream, operational residuals from genuinely archived run/valid-time pairs.

## Native Model Cadence at WAWP

Open-Meteo always returns an hourly response grid, but many values are
interpolated. The registry now records the global model actually applicable at
WAWP instead of a provider-family headline cadence.

| WAWP model | Update | Native temporal information relevant to WAWP |
|---|---:|---|
| ECMWF HRES | 6 h | hourly to 90 h, 3-hourly after 90 h, 6-hourly after 144 h |
| GFS Global | 6 h | hourly to 120 h, then 3-hourly |
| ICON Global | 6 h | hourly to 78 h, then 3-hourly |
| GEM Global | 12 h | 3-hourly, interpolated to hourly |
| CMA GRAPES Global | 6 h | 3-hourly, returned on an hourly grid |
| JMA GSM | 6 h | 6-hourly, interpolated to hourly |
| ARPEGE World | 6 h | hourly to 48 h, then 3-hourly |
| UKMO Global | 6 h | hourly to 54 h, 3-hourly after 54 h, 6-hourly after 144 h |

Primary model references:

- [ECMWF API](https://open-meteo.com/en/docs/ecmwf-api)
- [GFS API](https://open-meteo.com/en/docs/gfs-api)
- [ICON API](https://open-meteo.com/en/docs/dwd-api)
- [GEM API](https://open-meteo.com/en/docs/gem-api)
- [CMA API](https://open-meteo.com/en/docs/cma-api)
- [JMA API](https://open-meteo.com/en/docs/jma-api)
- [Meteo-France API](https://open-meteo.com/en/docs/meteofrance-api)
- [UKMO API](https://open-meteo.com/en/docs/ukmo-api)

Operational consequence: an interpolated JMA hourly peak is not independent
one-hour timing evidence. Batch 6 must compare timing skill by native-cadence
class and lead range before any timing component is promoted.

## Threshold Standing

No single rainfall threshold should serve detection, model weighting, TAF
phenomena, and heavy-rain warnings simultaneously.

| Threshold | Current standing |
|---:|---|
| 0.1 mm/h | source resolution/measurable-rain and Open-Meteo probability threshold |
| 1.0 mm/h | operational experiment threshold; requires replay |
| 1.5 mm/h | current shadow event threshold; locally motivated, not an ICAO standard |
| 1.5 mm/3h | provisional shadow accumulation threshold; test by sensitivity |
| 5, 10, 20 mm/h | intensity/impact tiers for replay, not automatic TAF codes |

The WMO surface-verification exchange specification recommends precipitation
contingency thresholds of 1, 5, and 25 mm for six-hour amounts and 1, 10, and
50 mm for 24-hour amounts. Those values support multi-threshold reporting but
must not be copied directly into a three-hour tropical airport product.

Batch 6 should replay `0.1, 1.0, 1.5, 5.0, 10.0, 20.0 mm/h` and report event
counts, POD, FAR, CSI, HSS, frequency bias, bootstrap uncertainty, and TAF
decision impact separately.

## Event Definition

Current shadow definition:

```text
wet episode = consecutive valid hours at or above threshold
dry hour     = ends episode
missing hour = ends episode and is never treated as dry
matching     = ordered one-to-one maximum-cardinality matching
```

The primary timing tolerance is not yet approved. Batch 6 must compare strict,
plus/minus one hour, and plus/minus two hours. A one-dry-hour bridge may be
tested only as a labelled sensitivity, never mixed into the primary score.

## Provenance and Promotion

Open-Meteo's [model-update metadata](https://open-meteo.com/en/docs/model-updates)
provides model initialization, modification, and availability timestamps. The
current live scraper stores scrape hour as a run proxy. A later operational
hardening batch should ingest the metadata initialization time and retain scrape
time separately.

No rainfall or gust timing metric may influence production until it passes:

1. UTC-normalized pairing.
2. Physical-range quarantine.
3. As-of run selection.
4. Native-cadence stratification.
5. Leakage-safe replay and independent holdout.
6. Manual meteorological approval with rollback.
