# Rainfall Shadow Replay

`src/rainfall_shadow_replay.py` is the Batch 6, audit-only replay for rainfall
verification semantics. It does not alter operational weights, QM artifacts,
consensus values, or TAF guidance.

## Fixed Split

- Development: `2023-01-01 00:00 UTC` through `2025-12-31 23:00 UTC`.
- Independent holdout: `2026-01-01 00:00 UTC` through
  `2026-06-30 23:00 UTC`, subject to actual observation availability.
- Model selection uses development rows only. The selected models are frozen
  before the holdout is scored.

## Compared Products

- strict hourly occurrence;
- one-to-one episode matching at plus/minus one hour;
- one-to-one episode matching at plus/minus two hours;
- cadence-aware episode tolerance, capped at plus/minus two hours;
- complete UTC three-hour sums with the same numeric threshold;
- complete UTC three-hour sums with a rate-equivalent threshold of three times
  the hourly threshold.

The same-numeric three-hour threshold is a sensitivity test, not an assertion
that `1.5 mm/h` and `1.5 mm/3h` represent the same event.

## Safety Gates

- Exact UTC forecast-observation timestamps only.
- Continuous historical API rows only.
- Duplicate model/valid-time keys are quarantined, not resolved by row order.
- Missing and negative rainfall values are quarantined.
- Rows with hard-invalid cloud, weather-code, or lifted-index fields are also
  quarantined because they may carry a shifted-field ingestion signature.
- Missing hours break episodes and incomplete three-hour blocks are excluded.
- The legacy centered rolling maximum is reported only to expose denominator
  inflation. It is future-leaking and is never ranked.

## Run

```powershell
python -m src.rainfall_shadow_replay `
  --database wawp_forecasts.db `
  --output-dir audit/rainfall/replay
```

The output directory contains detailed metrics, amount diagnostics, model-rank
stability, frozen development selections, holdout ensemble comparisons, paired
seven-day block bootstrap intervals, a machine-readable summary, and a concise
Markdown report.

## Interpretation Boundary

The Historical Forecast API is a continuous stitched stream. Results can
support a local historical occurrence prior and verification-method decisions,
but cannot establish individual run or lead-time skill. Any operational use
still requires live multi-init shadow evidence, meteorological review, manual
promotion, and rollback.
