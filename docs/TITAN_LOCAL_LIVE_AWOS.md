# Private live AWOSnet connection

The existing public TITAN site is not altered or granted access to station measurements. The local bridge serves the same published forecast data and spatial maps via HTTPS from GitHub Pages; AWOSnet is fetched by the local Python process from its fixed HTTP endpoint. The browser polls only while its page is visible, every 60 seconds after each request. No database, permanent cache, public sensor upload, or scheduled observation job.

## Start on an authorized workstation

Install Python 3, clone/pull the repository, and open PowerShell in the repository directory. Before using the flags, verify that the station permits access on a trusted network, that the AWOSnet display clock is UTC, the units are correct, and the two-minute wind direction is true north. The source HTTP leg is unauthenticated and unencrypted; this is supplemental guidance, not a certified observation system.

First test one actual snapshot:

```powershell
python scripts/serve_titan_live_awos.py --allow-http --confirmed-source-semantics --check
```

Then run:

```powershell
python scripts/serve_titan_live_awos.py --allow-http --confirmed-source-semantics
```

The browser opens http://127.0.0.1:8767/. Go to Observation & Nowcast. Do not use the public Pages URL for this local session. Close the browser tab to stop polling, or Ctrl+C to stop the relay. Only one request per minute across tabs is permitted, so use one tab. Missing, stale, or invalid observations remain withheld. Embedded METAR is independently timestamp checked. The local connection only serves the workstation and does not require Cloudflare.

For future public or multi-device HTTPS integration, deploy the separate existing Worker only with authorized hosting and redistribution permission. No public endpoint is enabled here. No actual station-network test has been run by the code author; the --check command is the acceptance test.
