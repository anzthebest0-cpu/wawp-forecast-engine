# Forecast Collection Selection Contract

Status: Batch 8 shadow implementation. Not enabled for operational TAF input.

## Purpose

Open-Meteo does not expose confirmed provider initialization time through the
Forecast API used by WAWP. The archive therefore distinguishes:

```text
run_init_utc = WAWP collection-cycle proxy
scraped_at   = actual time WAWP received the response
valid_time   = time the forecast describes
lead_hours   = valid UTC time minus collection-cycle proxy
```

For a historical issuance at time `T`, a forecast is eligible only when
`scraped_at <= T`. This prevents later collections from leaking into earlier
TAF verification.

## Cycle Selection

For each model:

1. Keep operational rows with non-negative lead time and collection at or
   before the cutoff.
2. Group rows by model, collection-cycle proxy, and scrape timestamp.
3. Reject the entire group when any row has a hard physical anomaly or the
   group contains fewer than 24 forecast rows.
4. Rank remaining groups by scrape time descending, then collection-cycle
   proxy descending.
5. Select exactly one complete group per model.

All groups remain archived. Selection only defines the evidence snapshot.

Environment control:

```text
WAWP_ASOF_SELECTION_MODE=observe_only  # default
WAWP_ASOF_SELECTION_MODE=enabled       # controlled test/promotion only
```

The dashboard exports the candidate and whether it differs from the existing
latest-scrape selection. Promotion requires replay against a current release
database, meteorological review, and rollback confirmation.
