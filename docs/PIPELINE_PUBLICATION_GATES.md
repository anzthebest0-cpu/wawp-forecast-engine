# Pipeline Publication Gates

Status: hard validation enabled before release upload and GitHub Pages commit.

The gate runs after `run_pipeline.py` and before any rolling database or
dashboard publication. It fails closed for:

- failed SQLite integrity;
- missing core database tables;
- fewer than six models with a complete clean collection cycle;
- blocked hourly AWOS quality;
- skipped dashboard export;
- missing or invalid required JSON artifacts;
- dashboard provenance that does not match the candidate database's latest
  model scrape;
- missing explicit exporter-success confirmation;
- missing or malformed TAF guidance payloads.

Stale AWOS is a warning when its values are otherwise physically safe. The
pipeline already freezes verification-driven weights and residual promotion in
that state. A latest model cycle with hard anomalies is also a warning when a
complete clean fallback exists and the six-model quorum remains satisfied.

Every run uploads `publication_gate.json` as a 30-day workflow artifact. A
failed gate prevents both rolling database replacement and Pages publication.

The separate shadow policy comparison is run with:

```text
python -m src.shadow_policy_comparison \
  --database wawp_forecasts.db \
  --output audit/shadow/policy_comparison.json
```

It compares the nominal latest snapshot with clean-cycle selection and scores
raw, historical-prior, and eligible residual candidates against hourly AWOS.
It is diagnostic only and cannot promote any correction.
