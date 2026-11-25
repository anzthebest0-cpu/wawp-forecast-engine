# TITAN final dashboard audit

Source review: main remains at 9b8a4f9; Phases 1–5 CI passed. Existing gated Pages publication workflow copies docs into a temporary artifact, regenerates preview payloads, validates against restored checkpoint, and only then deploys. Its push trigger matches its own workflow file, not dashboard-only changes. A dashboard merge is therefore not a deployment; manual gated publication on main is required after merge if authorized and enabled. Existing forecasting workflows and gate remain unchanged.

Audit changes: pin ApexCharts 3.54.1 (same version in browser CI); render preview meteograms only when visible; harden missing/invalid data and expiry behavior; prevent legacy freshness fallback; remove PAT/raw-file upload from public UI while retaining authorized external archive ingestion and manual in-memory observation import. Clear legacy token key; revoke any previously exposed token if applicable. No changes to forecast algorithms, data, or database.

Browser CI uses Playwright Chromium, local static server, synthetic temporary route fixtures, actual pinned ApexCharts, blocked external source requests. Tests cover fresh/expired/mismatched preview, 12-hour UTC matrix, chart rendering, rain missing vs zero, wind barbs, mobile navigation, external image failure, local snapshot import and clear, historical scorecard and DM interpretation, TAF withheld. Test screenshots are CI artifacts, not production data. Does not validate external endpoints, visual quality by human review, actual AWOS network, production payload accuracy or all chart interactions.

Local environment has Chromium but cannot resolve GitHub, so remote Actions runs provide execution evidence. Keep PR unmerged until browser test and review are complete; no deployment claimed.
