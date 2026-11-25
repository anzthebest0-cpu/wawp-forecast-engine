# TITAN workstation phase 2

Presentation only. No forecast data, database, provider retrieval, shadow evaluation, TAF safety or operational algorithms changed.

- Shared raw-preview UTC/WITA selector and 12/24/48/96-hour horizon; reload preserves active tab hash. Archive and regional product timestamps remain source-specific. All preview chart epoch conversions now use the WITA export contract.
- Model wind barbs, individual rainfall heatmap with explicit missing/zero distinction, temperature–dewpoint spread, and a descriptive sum of displayed precipitation amounts that stops at a missing or non-consecutive hour. The latter is not an operational accumulation forecast and may mix changing contributing models.
- No runway components until runway bearing and directional reference are verified. AWOSnet live retrieval still disconnected. No synthetic or stale live observation data.
- Run `node --check docs/preview-ui.js && node --check docs/dashboard.js && node --check docs/apexcharts-theme.js && node tests/test_preview_ui.js`. Browser testing still required for actual rendering, chart synchronization, mobile layout and external sources.
