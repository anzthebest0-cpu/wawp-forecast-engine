# TITAN forecast watchdog — operator handover

## Purpose
A separate cron-only Worker checks the already published WAWP raw-model preview every 15 minutes. It does not acquire forecasts, access Drive, write weather records, issue TAF, or change the six-hour freshness rules. It never invokes the AWOS proxy. GitHub's native schedule remains enabled.

Deploy the new Worker **titan-forecast-watchdog**, not the existing AWOS Worker. It has no public HTTP route; the handler returns 404. The SQLite Durable Object is private and retains only a bounded list of recent dispatch-attempt timestamps. Do not bind the AWOS database.

## Decision rules
* Unset/unknown WATCHDOG_MODE: disabled.
* observe: inspect and log; no dispatch.
* active: if a valid public manifest is unavailable, expired, or within 120 minutes of expiry, check the target workflow state and active/pending point refresh and publication runs. Suppress dispatch if any is active or a refresh started in the last 45 minutes.
* Reserve an atomic persistent cooldown slot before sending a POST to GitHub. Minimum 90 minutes between attempts; maximum eight attempts in any rolling 24 hours. Failed or ambiguous dispatches also consume a slot. No automatic retries of a POST. The GitHub run itself remains responsible for archive evidence, source validation and deployment.
* Invalid manifest, HTTP failures, missing token or invalid state: do not dispatch. Race with a native GitHub trigger is still possible; the existing shared concurrency group remains authoritative. GitHub may replace a pending run, so inspect overlapping runs during acceptance testing.

## Deploy from a local PowerShell session
From a fresh directory (choose a different clone destination if it exists):

    gh repo clone anzthebest0-cpu/wawp-forecast-engine titan-watchdog-setup
    Set-Location titan-watchdog-setup/services/forecast-watchdog
    npm test
    npx wrangler login
    npx wrangler deploy

Check the deployment output identifies **titan-forecast-watchdog** and the Durable Object migration, not the AWOS Worker. If you have multiple Cloudflare accounts, select the intended account. Do not proceed if tests or deployment fail.

Create a fine-grained GitHub personal access token at https://github.com/settings/personal-access-tokens/new. Select the owner of this repository, only the wawp-forecast-engine repository, Actions: Read and write, and an appropriate limited expiry (for example 30 days). Metadata read is automatic. No additional permissions or classic PAT needed. Do not send the token in chat, put it in source control, or type it as an inline CLI argument. Paste it only into Wrangler's interactive secret prompt:

    npx wrangler secret put GITHUB_ACTIONS_TOKEN

Then enable observation-only mode via another interactive prompt:

    npx wrangler secret put WATCHDOG_MODE

Enter **observe**. The Worker remains disabled if the variable is absent. Cloudflare Cron Trigger propagation can take time. Inspect the new Worker's Observability logs for event titan_forecast_watchdog; expected outcomes include fresh, busy, recent_refresh or would_dispatch. Observe mode checks GitHub only when guidance is due. It does not prove dispatch permissions.

## Activation and acceptance
Only after reviewing the observation-mode result and confirming the preview workflow is still the intended production workflow (WAWP_LIVE_PIPELINE_ENABLED is not true), set WATCHDOG_MODE again with the same secret-put command, entering **active**. Leave the existing GitHub schedule in place. Verify an actual dispatched event, a corresponding GitHub workflow_dispatch run on main, and a successful publication with a renewed eligible manifest. A dispatched event alone is not a successful forecast refresh.

Review 24–48 hours of freshness, refresh frequency, overlap and failures. If required, set WATCHDOG_MODE to **disabled** using the same secret-put command. Revoke the token when no longer required; a revoked or expired token fails closed. No AWOS changes are necessary.

## Limits
The fallback does not solve provider failures, Google Drive rate limits, archive verification failures, or GitHub Actions capacity limits. It never extends stale data or publishes without the existing gate. There is no live Cloudflare deployment or credential in this repository; the operator must deploy and activate it. Tests use synthetic manifests and mocked API calls, not live credentials. Keep the token private. Check GitHub and Cloudflare account quotas and token expiry.
