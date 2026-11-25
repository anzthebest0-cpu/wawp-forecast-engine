# Phase 4 — observation readiness (not live ingestion)

AWOSnet XML/plain-text endpoint access could not be verified from the review environment. No unverified parser, automated polling, data publication, credentials, or model learning changes were enabled. BMKG Aviation and WAWP weather links and the Tolitoli BMKG METAR mirror are supplemental reference links.

## Authorized station test
From a station workstation with Python 3, run `python scripts/probe_awosnet.py`. If HTTPS fails and station authorization and network trust permit HTTP, run `python scripts/probe_awosnet.py --allow-http`. It prints DNS/HTTP status, response type, size/hash and XML tag names only, not field values. No redirects, bounded response, timeout, no repository writes. Optional `--capture-dir <existing-approved-private-directory>` saves raw responses outside this repo; never commit or share restricted local MET REPORT/SPECIAL or credentials. Probe success does not establish timestamps, data integrity, field semantics or redistribution permission.

## Local browser import
The dashboard accepts manually normalized JSON in memory only, no upload. Required keys:
- schema: "titan-local-awos-v1"; station: "WAWP"; source: "AWOSnet"
- observed_at_utc: full ISO timestamp YYYY-MM-DDTHH:mm:ssZ, from sensor not retrieval
- wind_average_minutes: 2; wind_reference: "true"; units_confirmed: true
- nullable numeric keys: wind_speed_kt, wind_direction_deg, gust_kt, temperature_c, dewpoint_c, qnh_hpa. At least one valid value. Gust interval must be understood from source; no crosswind or gust derivation. Never infer QNH from generic pressure. Missing stays null; zero is not missing.

Only prepare the JSON after verifying source semantics. Do not treat this as a validated XML adapter. The 5-minute sensor and 60-minute METAR display expiry thresholds are internal conservative UI policies, not regulatory validity criteria. METAR/SPECI paste requires full UTC date, WAWP and matching report day/time; does not fully decode or authenticate the report. Both displays are manually supplied and session-only. No visibility claim, no runway components, no official report issuance.

Next: inspect authorized payload samples privately, verify field units/reference/aggregation/timezone and update behavior against station console, agree publication permissions, then build a bounded collector and independent freshness/quality tests. Existing AWOS quality contract applies to archived data and is unchanged.
