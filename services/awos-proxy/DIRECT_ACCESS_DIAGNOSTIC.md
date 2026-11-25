# Direct AWOSnet access diagnostic

The preceding dashboard and proxy tests passed on commit aeff696. To avoid creating a Cloudflare account unnecessarily, a one-shot Chromium diagnostic tests actual HTTP and HTTPS transport and browser-readable fetches from a synthetic page at the intended GitHub Pages origin. The browser is unmodified: no disabled web security, no insecure-content exception, no saved observation data. Transport results inspect status and CORS header only. This is not a test on the station's computer and does not authorize public redistribution.

Run only when explicitly requested, via a push whose commit message contains [awos-direct-test]. No cron or ongoing observation polling. HTTPS direct access requires readable response in standard browser security. HTTP direct access from an HTTPS page remains unsuitable even if transport succeeds. Keep default empty proxy endpoint and existing safety gates until a secure, authorized path is verified.
