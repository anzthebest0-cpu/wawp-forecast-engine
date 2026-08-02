# WAWP Current Release Readiness Audit

## Scope

- Release tag: `latest-db`
- Asset: `wawp_forecasts-30715296086-1.db`
- Uploaded: 2026-08-01 19:44 UTC
- Size: 996,491,264 bytes
- Release digest: `sha256:59301a0e162baa05ebd7a757de746d51d95c6db35434b903be62f8682c711728`
- Audit access: isolated, read-only database connection

## Database And Forecast Cycles

SQLite integrity is `ok`. Seven latest model cycles contain 384 clean rows.
JMA's latest cycle contains six consecutive `-0.1 mm` values in total
precipitation, rain, and showers at leads 158-163 hours. Clean-cycle selection
therefore retains all eight models but uses JMA's previous clean cycle.

This is not the earlier shifted-column incident. It is a small physically
invalid negative accumulation. The source values remain archived; no silent
clipping was introduced.

## AWOS Findings

The rolling database contains 43,193 distinct hourly observations through
2026-07-31 23:00 UTC with no negative hourly rain or impossible core ranges.
The compact database intentionally contains no raw minute rows.

The retained July source files contain 744 hourly rows and 46,066 minute rows.
All 743 comparable hourly rain boundaries match the minute rolling-accumulation
snapshot within 0.05 mm. This confirms hourly RA36 as the accumulation source
and confirms minute rain must not be summed.

The same 32 minute files contain zero populated WGS values. The database had
stored `wind_gust_max=0.0` for these hours because missing data inherited a
zero default. This is not evidence of calm gust. The ingestion contract now
stores unavailable gust as null, clears unsupported derived values, and blocks
gust verification until sufficient positive WGS evidence exists.

## Shadow Correction Results

The 60-day read-only comparison rejected 185 operational-residual CDF rows
because they lack the current UTC pairing contract. No residual correction was
applied or promoted.

Historical-prior comparison on clean operational pairs:

| Parameter | Raw MAE | Prior MAE | Direction |
|---|---:|---:|---|
| Temperature | 1.7505 C | 1.4169 C | Improved about 19.1% |
| Dewpoint | 1.0877 C | 1.1830 C | Degraded about 8.8% |
| Pressure | 0.5981 hPa | 0.5700 hPa | Improved about 4.7% |
| Wind speed | 3.3741 kt | 2.6874 kt | Improved about 20.4% |
| Wind direction | 59.5552 deg | 54.8584 deg | Improved about 7.9% |

Rainfall has no active historical amount correction. Per-model raw 0.1 mm
occurrence rows have POD 0.1677, FAR 0.9488, CSI 0.0409, and HSS 0.0473. These
are repeated model-row diagnostics, not consensus or TAF scores, and cannot be
used as a promotion result. Gust is unscored because the recent observation
source is unavailable.

## Publication Verdict

The release database itself is structurally usable with JMA fallback. The
checked-in dashboard JSON is older than this downloaded database and does not
carry an explicit exporter-success field, so this database/JSON combination is
correctly rejected by the strengthened publication gate.

The next workflow run must regenerate dashboard JSON from its restored and
updated database. Publication is permitted only if the exporter reports
success, JSON provenance matches the database's latest scrape, at least six
complete clean models remain, AWOS has no hard-quality failure, and all TAF
payloads are present.

## Promotion Decision

- Clean-cycle selection: remain observe-only until several current workflow
  runs demonstrate stable fallback behavior.
- Historical prior: do not promote as one global package. Temperature,
  pressure, wind speed, and wind direction merit independent holdout review;
  dewpoint currently fails the direction-of-skill check.
- Operational residuals: remain disabled; current artifacts fail the new time
  provenance requirement.
- Rainfall amount/event weighting: remain observe-only.
- Gust weighting and TAF gust relaxation: blocked until authoritative WGS data
  becomes available.
