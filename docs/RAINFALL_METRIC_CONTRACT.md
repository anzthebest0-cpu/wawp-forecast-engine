# WAWP Rainfall Metric Contract

Status: implemented as `event-window-v2-shadow`. Meteorological approval and
leakage-safe replay are required before this becomes an operational standard.

## Safety Boundary

Rainfall event-window diagnostics are observe-only while this contract is being
validated. They may be displayed and replayed, but they must not alter model
weights or TAF change-group logic by default.

## Time Standards

| Product | Stored clock | Current evidence | Required action |
|---|---|---|---|
| Hourly AWOS `obs_time` | UTC, timezone-naive text | Diurnal code explicitly adds 8 h to obtain WITA | Confirm against AWOS export documentation |
| One-minute AWOS `obs_time` | UTC, timezone-naive text | Parsed with the same source convention | Confirm against AWOS export documentation |
| Historical Forecast API `forecast_time` | UTC, timezone-naive text | Historical request explicitly uses UTC | Retain |
| Live Forecast API `forecast_time` | WITA, timezone-naive text | Request uses `Asia/Makassar` | Do not directly join to UTC AWOS text |
| `run_init_utc` for live collection | Scrape-hour proxy in UTC | Forecast endpoint does not expose true model initialization | Label as collection reference, not true init |

All new calculation interfaces should use timezone-aware timestamps internally.
Serialization may remain text, but the field name or metadata must identify UTC
or WITA explicitly.

## Observation Fields

| Field | Current parser meaning | Unit | Status |
|---|---|---|---|
| Hourly `RA36` | Final column in the 13-column AWOS hourly record, divided by 10 | mm | Preceding one-hour rolling accumulation ending at timestamp |
| `rain_1h` | Parsed `RA36` value | mm/preceding hour | Confirmed empirically at 743 July boundaries |
| `rain_1min` | One-minute snapshot of rolling one-hour rain accumulation | mm/preceding hour | Never sum as minute increments |

The supplied July files establish the interval convention empirically. Station
documentation remains desirable, but hourly `rain_1h` may be summed across
consecutive complete end-labelled periods. One-minute rain must not be summed.

## Forecast Fields

| Field | Required meaning |
|---|---|
| `precipitation` | Total liquid-equivalent precipitation returned by Open-Meteo |
| `rain` API component | Large-scale/liquid rain component when separately provided |
| `showers` | Convective shower component when separately provided |
| database `rain` | Current WAWP consensus amount field; live scraper stores total precipitation here |
| `precipitation_probability` | Probability field from the selected Open-Meteo model, not probability of exceeding every WAWP threshold |

The database `rain` name is historically ambiguous because it contains total
precipitation in the current live scraper. Audit outputs must therefore label it
`forecast_total_precipitation_mm` until the schema is versioned.

## Distinct Verification Questions

### Strict Hourly Occurrence

One sample represents one valid clock hour. Forecast and observation must share
the same physical hour. This supports a conventional contingency table and
POD, FAR, CSI, frequency bias, HSS, and accuracy.

### Rain Episode Timing

Consecutive wet hours belong to one episode unless separated by a dry or
missing hour. Forecast and observed episodes are matched one-to-one. A timing
tolerance may change hit/miss classification, but it must never change the
number of observed episodes.

### Three-Hour Accumulation

Three interval-ending hourly amounts are summed into an explicitly aligned
three-hour period. A block ending 03 UTC uses reports labelled 01, 02, and 03.
Only complete periods are scored unless a separately labelled partial-period
product is approved. The threshold unit is `mm/3h`, not `mm/h`.

The legacy centered rolling maximum is not a three-hour accumulation and must
not be labelled or counted as one.

## Threshold Registry

| Value | Current use | Contract status |
|---:|---|---|
| `0.1 mm/h` | Measurable/wet-hour checks and condition labels | Candidate trace threshold; confirm gauge precision |
| `1.0 mm/h` | TAF consensus rain gate and replay logic | Operational decision threshold; verify independently |
| `1.5 mm/h` | Dynamic event verification and weighting | Under audit; observe-only |
| `4.0 mm/h` | Heavy-rain verification in replay code | Research threshold; not interchangeable with TAF significance |
| `10.0 mm/h` | Heavy-rain condition and weighting tier | High-impact threshold; requires sample-size reporting |

Thresholds may differ by purpose, but every output must carry the threshold,
unit, aggregation interval, denominator, and version. A single undocumented
number must not silently serve multiple purposes.

## Missing Data

- Missing hours are unknown, not dry.
- A missing hour breaks an episode.
- A three-hour accumulation is incomplete when any constituent hour is missing.
- Rolling operations must use elapsed time, not merely the previous available rows.
- Duplicate forecast valid times must be resolved by an explicit as-of issuance
  policy, not by arbitrary row order.

## Promotion Requirements

Any rainfall metric that can affect weights or TAF guidance requires:

1. Synthetic invariant tests.
2. Timestamp-level reconciliation against raw AWOS evidence.
3. As-of historical replay without future-run leakage.
4. Independent holdout performance with uncertainty intervals.
5. Meteorological review of thresholds and event definitions.
6. Shadow operation and explicit manual promotion.
