# WAWP rolling live AWOS history

The existing Cloudflare Worker has an optional D1 history mode. It remains disabled unless the operator sets AWOS_HISTORY_ENABLED=true, all existing source and public-distribution gates pass, and the AWOS_HISTORY D1 binding exists. This requires authorization for continuous collection and temporary storage as well as public display. CORS is not authentication. The upstream HTTP feed is unauthenticated; use supplemental guidance only.

## Activate (operator account)
1. Create D1 database titan-awos-history in Cloudflare (Storage & databases > D1 SQL Database).
2. Provide only the database ID (UUID), not credentials or API tokens. Bind it in services/awos-proxy/wrangler.toml with [[d1_databases]], binding = "AWOS_HISTORY", database_name = "titan-awos-history", and the actual database_id.
3. Apply migrations/0001_awos_history.sql through D1 Console or Wrangler remote migration. Verify both tables exist.
4. Add [triggers] crons = ["* * * * *"] to the same Wrangler file. Keep keep_vars=true and preview_urls=false. No cron or placeholder database ID is enabled in source until the binding is known.
5. After the new Worker deploys, add runtime plain-text AWOS_HISTORY_ENABLED=true only with confirmed authorization. The collector runs on the next cron tick. /awos then reads the recent database snapshot instead of polling upstream per browser. If the collector fails or the snapshot is stale, live values are withheld; no per-client fallback in enabled history mode. To return to on-demand behavior set AWOS_HISTORY_ENABLED=false and deploy.
6. Verify /awos and /awos-history?hours=6 with Origin: https://anzthebest0-cpu.github.io, then check the dashboard. Empty history at startup is expected; one full day accumulates progressively.

## Retention and interpretation
One collection per minute; unique source timestamps, latest validated retrieval for duplicates. Unique METAR reports stored separately. Prune by observation time every scheduled run, even when the collection flag is disabled while cron and binding remain deployed. Queries always exclude records outside 24 hours. Outages may delay physical cleanup. D1 Time Travel can retain recoverable data seven days on Free (longer on Paid); 24 hours is active/queryable retention, not guaranteed irreversible deletion. No permanent raw XML, GitHub, or Drive archive. If policy demands hard deletion within 24 hours, do not activate this D1 design.

Dashboard polls history every five minutes only in relevant visible workspaces. Default six hours; 3/6/12/24-hour selection. Direction is raw source bearing, reference unverified; calm excluded, no magnetic/true conversion or runway-relative use. Direction points are not joined across north; changes use shortest signed angular difference. Rain is the source current-hour accumulation and is not summed. Observations and eligible future model guidance are distinct traces, not verification or automatically adjusted forecasts.

Unit tests use synthetic observations and an in-memory D1 adapter. Real Cloudflare D1 migration, binding, cron and live retention still require operator acceptance tests.
