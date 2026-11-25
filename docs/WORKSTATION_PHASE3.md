# TITAN workstation phase 3 — atmospheric analysis

Presentation-only source workspace. Four scales, 20 diagnostic entries, source links and six optional external images (ENSO, MJO, NCICS southern-band wave Hovmöller and two-day Kelvin panels, weekly SST anomaly, Himawari IR). Provider URLs were identified from public product pages; the Himawari PNG was retrieved during review, but GIFs could not be inspected by the review environment. Their availability, age, browser rendering and hotlink permissions must be checked; source links remain the fallback. The NCICS V1 graphics are explicitly legacy/experimental and must not be presumed current. Public radar is a link, not a raw-radar integration. BMKG images must keep source attribution and original logos.

The existing RASON/SIGWX/CB viewer is retained. Windy source links support manual surface pressure and forecast wind selection, not an embedded isobar/streamline engine. Dedicated moisture-flux convergence and CIN products are not integrated. Manual checklist is session-only, not operational certification. No observations, forecast data, collection logic, shadow research, database, existing forecast workflows or TAF safety contract changed.

Checks: node tests/test_analysis_workspace.js and node --check docs/analysis-workspace.js. Full browser testing and source validation still required.
