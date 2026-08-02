# WAWP Rainfall Audit Issue Register

Status: Batch 7 time-provenance hardening implemented. This register records verified
code behavior and open meteorological questions. It does not declare the
replacement metric operationally valid.

## Executive Finding

The observed-rain mismatch is primarily produced by metric transformation, not
by a second observation source. The current `3h_block` uses a centered rolling
maximum and counts every expanded wet sample. In the current monthly export,
eight strict wet hours become 22 `3h_block` wet samples.

## Findings

### RAIN-001: `3h_block` is not a three-hour accumulation

- Severity: Critical
- Confidence: Confirmed in source and current export
- Source: `src/event_window_verification.py:215-219`
- Behavior: centered rolling maximum over three available samples
- Impact: inflates wet-sample counts and changes POD, FAR, CSI, and HSS
- Containment: event-window influence defaults to observe-only
- Required fix: separate strict hourly, episode timing, and true three-hour sums
- Resolution: implemented in `event-window-v2-shadow`; operational influence
  remains disabled pending replay and meteorological approval

### RAIN-002: Inflated event counts affect weighting eligibility

- Severity: Critical
- Confidence: Confirmed in source
- Source: `src/event_window_verification.py:34-53`
- Behavior: `_event_count` takes the maximum observed count across strict,
  tolerance, and expanded-block products
- Impact: inflated counts reduce sample shrinkage and can make a model appear
  sufficiently supported earlier than it should
- Required fix: sample support must use invariant episode count or valid
  sample-aligned denominator for the metric being weighted
- Resolution: implemented using one-to-one episode counts; shadow-only

### RAIN-003: Tolerant matching is not one-to-one

- Severity: Critical
- Confidence: Confirmed in source
- Source: `src/event_window_verification.py:150-194`
- Behavior: each observed wet sample independently searches all forecast wet
  samples and vice versa
- Impact: one forecast sample can satisfy multiple observed samples; resulting
  counts are not a conventional contingency table, so HSS is not valid
- Required fix: one-to-one episode matching for timing metrics; retain HSS only
  for sample-aligned products
- Resolution: implemented with ordered maximum-cardinality, minimum timing-error
  matching; episode HSS is intentionally undefined

### RAIN-004: Operational residual join has a clock-alignment risk

- Severity: Critical
- Confidence: Confirmed and contained
- Sources: `src/scrape_openmeteo.py:112-136`,
  `src/operational_residuals.py:266-280`
- Previous behavior: live `forecast_time` was generated from `Asia/Makassar`
  timestamps while AWOS was treated as UTC; residual queries joined equal text
  timestamps.
- Resolution: `valid_time_utc` and `forecast_time_basis` now carry explicit
  provenance; residual and QM joins use canonical UTC valid time. Existing live
  rows are backfilled by minus eight hours, while historical UTC rows use a
  query fallback and are not duplicated.
- Current evidence: the local operational archive begins after the latest AWOS
  hour, so the physically aligned operational pair count is currently zero.
- Safety status: residuals remain observe-only and old artifacts without
  `openmeteo-valid-time-utc-v1` are rejected.

### RAIN-005: Multiple forecast runs collapse by row order

- Severity: High
- Confidence: Confirmed in source
- Sources: `src/export_dashboard_data.py:119-135`,
  `src/event_window_verification.py:232-244`
- Behavior: duplicate valid times are reduced with `last`/`keep="last"`
- Impact: model skill can depend on database row ordering rather than an as-of
  issuance or lead-time policy
- Required fix: select the latest run available before each verification issue
  time, preserving run and lead provenance
- Batch 7 containment: residual pairing preserves every collection cycle and
  exports collection, valid-time, and lead provenance. The broader dashboard
  consensus collapse policy remains open and must not be interpreted as
  provider initialization-aware.

### RAIN-006: Rainfall thresholds have different undocumented roles

- Severity: High
- Confidence: Confirmed in source
- Evidence: `0.1`, `1.0`, `1.5`, `4.0`, and `10.0 mm` appear in occurrence,
  weighting, condition, replay, and heavy-rain logic
- Impact: dashboards and reports may use the same word `rain event` for
  materially different events
- Required fix: versioned threshold registry with units and purpose

### RAIN-007: Database `rain` is semantically broader than its name

- Severity: High
- Confidence: Confirmed in current live scraper
- Source: `src/scrape_openmeteo.py:142-160`
- Behavior: when available, Open-Meteo total `precipitation` is stored in `rain`
- Impact: rain, showers, and total precipitation can be confused or counted
  twice in future code
- Required fix: migrate consumers toward explicitly named total and component
  fields while retaining a compatibility alias

### RAIN-008: Native model cadence is not the same as returned hourly grid

- Severity: High for event timing; Medium for broad consensus
- Confidence: Confirmed architectural limitation
- Source: `src/model_registry.py`
- Impact: apparent hourly timing precision may exceed native model information;
  accumulation values must not be linearly treated like instantaneous states
- Required fix: carry native cadence confidence into verification and aggregate
  precipitation conservatively

### RAIN-009: AWOS rain interval semantics need authoritative confirmation

- Severity: High
- Confidence: Open question
- Source: `src/awos_hourly_parser.py`
- Confirmed: final RA36 column is selected and divided by ten
- Unknown: whether the timestamp labels the beginning/end of accumulation,
  whether values are increments, and how resets/trace values are encoded
- Required fix: station/AWOS documentation or controlled gauge comparison

### RAIN-010: Historical and operational correction paths use different provenance

- Severity: High
- Confidence: Confirmed architecture
- Historical API rows are UTC continuous-stream values and are safe only as a
  non-lead-aware prior.
- Live rows contain collection-time/run-proxy information but require corrected
  UTC valid-time pairing before residual learning.
- Required fix: keep historical prior and operational residual labels separate;
  do not promote residual corrections until RAIN-004 is resolved.

### RAIN-011: The local archive contains shifted-field forecast rows

- Severity: Critical for replay/training; current-live impact depends on whether
  a newer valid scrape supersedes them
- Confidence: Confirmed in the audited database
- Evidence: 17,222 rows fail at least one hard range/category check; 8,420 of
  those have impossible lifted-index values above 100 in magnitude. Affected
  operational shifted-field signatures are concentrated in 4-7 July 2026.
- Example signature: cloud cover above 100%, visibility values occupying a
  cloud-percentage range, and lifted index containing visibility-scale values
- Likely cause: a prior ingestion/schema mapping version wrote API arrays into
  the wrong destination columns
- Required fix: quarantine affected model/run pairs, reconstruct provenance,
  and never train/replay from rows failing hard physical-range checks

### RAIN-012: One-minute rain cannot be summed as minute increments

- Severity: High
- Confidence: Confirmed by cross-grain reconciliation
- Evidence: for 3,713 paired wet hours, summing minute values matches hourly
  RA36 in only 1,865 cases, while the minute maximum matches in 3,095 cases;
  wet-hour minute sums total 189,461.7 versus 10,160.4 in hourly RA36
- Impact: minute summation would exaggerate rainfall by roughly an order of
  magnitude and corrupt accumulation verification
- Resolution: July cross-grain evidence identifies minute RA as a rolling
  one-hour accumulation snapshot; retain it for timing/QC and never sum it

### RAIN-013: Hourly rain and gust products are interval-ending

- Severity: Critical for timing verification
- Confidence: Confirmed empirically for rain; aligned to Open-Meteo definition for gust
- Evidence: all 743 July hourly boundaries match the one-minute rain value at
  the same timestamp
- Previous behavior: three-hour rain blocks and derived gust maxima used
  start-labelled/forward-looking windows
- Resolution: shadow blocks and future gust aggregation now use interval-end
  labels and preceding-hour windows
- Follow-up: rebuild stored `wind_gust_max` from retained minute assets

### RAIN-014: Global model cadence metadata was too optimistic

- Severity: High
- Confidence: Confirmed against official Open-Meteo model pages
- Previous behavior: provider-family update cadence was used for GFS, ICON,
  GEM, JMA, ARPEGE, and UKMO
- Impact: freshness and one-hour timing confidence were overstated
- Resolution: registry now carries WAWP-applicable update and native temporal cadence

### RAIN-015: Probability alias overstated available information

- Severity: High
- Confidence: Confirmed against Open-Meteo parameter definition
- Previous behavior: the same probability of more than 0.1 mm was copied into
  both `Prob_Precip_0.1` and `Prob_Precip_1.0`
- Resolution: the unsupported 1.0 mm probability alias is now null

### RAIN-016: A legacy timing-weight method still exists outside the shadow verifier

- Severity: High if reactivated
- Confidence: Confirmed in source
- Source: `AdvancedEnsembleWeighter.calculate_timing_weights()`
- Current runtime: the dashboard exporter calls CRPS weighting directly and
  does not call `calculate_fused_weights()`, so this path is dormant
- Risk: future callers could reintroduce independent wet-hour matching
- Resolution: explicitly deprecated. It returns base weights unchanged by
  default and records `deprecated_observe_only`; reactivation requires the
  controlled-test-only `WAWP_LEGACY_TIMING_WEIGHTING_MODE=enabled` switch.

### RAIN-017: Forecast-cycle selection lacked an issuance cutoff contract

- Severity: Critical for retrospective verification
- Confidence: Confirmed in repeated latest-scrape SQL paths
- Risk: a forecast collected after a historical TAF issuance could be selected
  for that issuance, or ties could depend on database ordering
- Resolution: `collection-asof-clean-cycle-v1` deterministically selects the
  latest clean WAWP collection at or before the cutoff, with explicit tie-breaks
  and no provider-init claim
- Current mode: observe-only; `WAWP_ASOF_SELECTION_MODE=enabled` is reserved for
  controlled replay and manual promotion
- Current release evidence (August 1 asset): seven latest model cycles are
  clean. JMA contains six `-0.1 mm` precipitation/rain/showers rows at leads
  158-163 h, so clean-cycle selection falls back only JMA. This is a small
  negative accumulation defect, not the earlier shifted-field signature.

### RAIN-018: Hourly gust provenance is sparse compared with rain provenance

- Severity: High for gust verification and TAF gust groups
- Confidence: Confirmed in local cross-grain audit
- Current July rain evidence: 743 of 743 comparable hour-boundary minute
  snapshots match hourly rain within 0.05 mm
- Current July gust evidence: the 32 supplied minute files contain zero
  populated WGS values, so no gust reconstruction is possible
- Resolution: export `awos-cross-grain-quality-v1-shadow`, retain interval-end
  gust reconstruction, and keep gust promotion blocked pending source review or
  reconstruction from authoritative retained minute assets

### RAIN-019: Missing one-minute gust was stored as a zero gust

- Severity: Critical for gust verification and residual training
- Confidence: Confirmed in the August 1 rolling release and raw July files
- Cause: `wind_gust_max` had a `DEFAULT 0.0`, while hourly ingestion omitted the
  column. Missing WGS therefore became a false observed calm gust.
- Impact: unfiltered gust verification produced zero observed gust events and
  thousands of false alarms, which is not meteorologically meaningful.
- Resolution: new hourly observations explicitly store unknown gust as `NULL`;
  ingestion clears hours without a populated minute WGS source; gust pairing
  accepts only positive derived observations; parameter-level quality keeps
  gust verification ineligible until at least 20 positive derived hours exist
  in the latest 60 days.

## Implemented Containment

- `WAWP_EVENT_WINDOW_WEIGHTING_MODE` defaults to `observe_only`.
- Event metrics and candidate event weights remain exported for diagnosis.
- `applied=false` prevents event diagnostics from blending dynamic weights.
- The same flag prevents event evidence from relaxing TAF rain/gust gates.
- Preserved dashboard diagnostics are re-contained before reuse.
- TAF guidance carries an audit warning while containment is active.

`WAWP_EVENT_WINDOW_WEIGHTING_MODE=enabled` is a rollback/controlled-test switch,
not a recommendation for operational reactivation.

## Reproducible Evidence

Run:

```powershell
python -m src.rainfall_audit --database wawp_forecasts.db --output-dir audit/rainfall/evidence
```

Outputs:

- `rainfall_audit_summary.json`: integrity, counts, forecast-field consistency,
  and UTC/WITA alignment comparison
- `rainfall_observation_ledger.csv`: timestamp-level hourly, episode, legacy
  expansion, and true three-hour-bin evidence
- `forecast_hard_anomalies.csv`: rows that fail non-negativity, cloud-cover,
  weather-code, or lifted-index physical checks

Raw-source semantics evidence:

```powershell
python -m src.awos_raw_semantics_audit `
  --hourly-file D:\UJI_PERFORMA_MODEL\JULY\000HLY.202607.dat `
  --minute-directory D:\UJI_PERFORMA_MODEL\JULY `
  --minute-pattern "000OneMinute.202607*.dat" `
  --output audit\rainfall\evidence\awos_july_2026_semantics.json
```
