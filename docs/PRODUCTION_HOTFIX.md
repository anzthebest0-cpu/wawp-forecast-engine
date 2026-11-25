# Production v1 hotfix

- Remove manual SIGWX comparison/import UI per user request; retain official product shortcuts.
- Rainfall matrix: shortened display labels with original model IDs on hover; explicit opaque first-column background in both themes, fixed minimum column width and horizontal scrolling within the panel. No rainfall data changed.
- Legacy lead-time accuracy withheld: exporter calculate_advanced_metrics fills missing Run_Init_UTC / lead times with zero and publishes no per-bin paired N; committed archive Month temperature contains Day 1 only. No engine, data or verification calculation changed. Revisit time normalization and cohort/count validation before reinstating.
- AWOS: production has no configured relay. Hide disabled live controls, distinguish not configured from network failure, retain direct official link and local import. No live service deployment, data transfer, or unconfirmed source semantics. Existing secure proxy support remains dormant.
