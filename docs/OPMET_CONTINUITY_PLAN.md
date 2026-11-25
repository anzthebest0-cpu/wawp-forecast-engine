# TITAN aviation-source continuity plan — 2026-11-25

Scope: source independence, not a forecast-engine change. Companion: ARIFLAB_SOURCE_REGISTER.md. Do not mirror Ariflab or automate BMKG web-page retrieval without authorized channels. See https://www.bmkg.go.id/ketentuan-penggunaan (8 July 2026). Request written authorization, permitted machine-to-machine endpoints, rate limits, operational availability and redistribution scope from the responsible BMKG units. Public access does not authorize a relay.

## Priority and fallback matrix

| Product | Primary | Fallback candidate | Acceptance condition |
|---|---|---|---|
| WAWP issued METAR/SPECI/TAF | Authorized station-issued records, including issuance history | Authorized BMKGsatu/Web Aviation OPMET interface | Exact bulletin/report text, dated UTC issue time, correction/amendment/cancellation history, collection completeness |
| Continuous sensors | Authorized station AWOS acquisition | Manual station snapshot or issued METAR for report-resolution fields only | Confirm UTC clock, measurement interval, units and wind reference; no replacement of high-frequency sensors by METAR |
| Regional sounding analysis | Authorized BMKG sounding feed | NOAA IGRA v2.2 https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive | Verify Indonesian station roster, actual latest soundings, reporting delay, levels, and data rights. Daily archive updates do not guarantee same-cycle data |
| Regional gridded analysis/forecast | GFS 0.25 via documented NOMADS GRIB filter https://nomads.ncep.noaa.gov/ | Other verified global-model gridded products | Correct cycle, lead, field units, domain, data availability; never label NWP as observed radiosonde analysis |
| SIGMET/aerodrome warning | Authorized official distribution | Official provider viewing links/manual workflow | Valid area/time, cancellation/replacement; unavailable != no warning |
| Ariflab charts | Provider links for human viewing pending permission | Independently generated regional charts | Read issue/valid time, distinguish observed interpolations and NWP |
| CB forecast | Existing official products and independent validated guidance | Research-only convective environment diagnostics | No rainfall-only proxy treated as observed CB or probability of CB |

## Stage A — preserve station-owned OPMET evidence

Existing repository: docs/DATABASES.md documents forecast/AWOS/QM tables; src/legacy_taf_verification.py reads Jan–May 2026 historical TAF workbooks and canonical METAR extracts; src/taf_native_verification.py describes the June human-issued TAF experiment. The actual complete historical record, archive paths, format, and July–November coverage have not been independently inventoried. Do not create a competing archive or insert OPMET into the rolling forecast SQLite database.

First inventory station-approved storage (local office storage or access-controlled Drive; outside GitHub Pages). Reconcile existing historical extracts with source bulletins, identify gaps by expected scheduled observations/issuances, and preserve every SPECI and amendment. Request raw text exports or original files, full date/time metadata in UTC, and source identifiers. A month/day token alone is not an adequate historical timestamp.

Proposed separate evidence layout (adapt to existing storage instead of duplicating):
```
OPMET_WAWP/
  raw/YYYY/MM/    # immutable original files, access controlled
  manifests/YYYY/MM/ # SHA256, byte length, source, acquisition time UTC, provenance
  index/          # derived report metadata, no overwriting prior issue
  qc/             # gaps, parse errors, conflicting versions, audit
```
Index fields: station, type, report issue UTC (full date), raw text, original bulletin heading where available, validity start/end UTC for TAF, correction/amendment/cancellation/NIL marker, source, acquisition UTC, original file hash, record locator. Keep transmission/reception time separate from meteorological report time. Identical normalized reports can be deduplicated for display; preserve raw evidence and distinct versions. Conflicting same-issue versions require review, never silent overwrite. Do not reconstruct omitted fields as zero. Do not claim no SPECI from an incomplete collection. Do not infer current TAF by choosing only the newest issue without validity and supersession checks. No new production data, no archive writes until existing storage is inventoried and approval obtained.

## Stage B — independent regional chart pilot

Start with GFS 0.25 GRIB subsets for an explicitly provisional regional domain 110–132 E, 10 S–5 N; adjust after forecaster review. Fetch analysis f000 and forecast f006/f012 from a single verified cycle as a bounded experiment using documented NOMADS filters (no full global files). Candidate levels: 1000/925/850/700/500 hPa; variables U/V, RH, temperature, geopotential height; MSLP separately. Confirm availability, terrain masking, units, longitude convention, GRIB timestamps, actual grid, and transport limits first. Chart 925 hPa wind streamlines, 850/700 hPa RH, surface pressure, and 850 hPa circulation. Display explicit model, initialization, lead, valid UTC, units, legend and geographic reference. Mask below-ground pressure levels. Model f000 is an analysis estimate, not observed sounding analysis.

For observation-based products: inventory actual IGRA v2.2 station coverage (Makassar and other regional sites only after verifying IDs and recent profiles). Use pressure-coordinate interpolation within each observed sounding, never extrapolate beyond measured support; calculate indices with explicitly documented parcel method, temperature/dewpoint units and QC. Plot station points and actual observation times first. Only interpolate spatially after leave-one-station-out validation and mask unsupported areas; do not use national cubic interpolation as established truth. Do not substitute ERA5 as verification truth. No assertion of current regional coverage until checked. Freezing level, LI, SI, KI, CAPE and shear are subsequent pilots; distinguish parcel choices and multiple crossings. Do not emulate rainfall thresholds as CB observations.

## Stage C — authorized operational interface

Request official read-only OPMET and sounding API documentation, authentication, update cadence, historical replay, status/cancellation semantics, quotas, availability, cache controls and approved scope (internal dashboard versus public GitHub Pages). Keep secrets outside browser and repository. Only after approval implement provider adapter and bounded polling, station WAWP filtering, UTC/date validation, explicit stale/unavailable states, no silent fallback across report types, and clear provenance. A relay must not circumvent provider restrictions. Tests: date rollover, delayed issue, stale report, expired TAF, AMD/COR/CNL/NIL, missing fields, provider failure, cache and duplicate conflicts. No automated TAF issuance.

## Acceptance and release sequence

1. Confirm station-approved archive location; inspect existing records and report completeness (not yet done).
2. Obtain authorized access or choose documented public alternatives; verify actual source data and latency.
3. Run a one-cycle regional GFS chart pilot outside production; validate fields and labels.
4. Demonstrate independent observation-based charts only when sounding coverage supports them.
5. Review permissions and independent QA before integration; retain rollback to existing dashboard. No change to rolling checkpoint, current guidance calculation, verification, or public publication during this plan.
