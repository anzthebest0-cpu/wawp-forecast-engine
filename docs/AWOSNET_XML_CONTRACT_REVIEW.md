# AWOSnet XML contract review

Connectivity independently confirmed on GitHub Actions run 45554805335: HTTP 200 XML root awosnet, HTTPS refused. User-provided sample confirms self-closing value/unit/time attributes and duplicate capitalization aliases for two-minute wind and ten-minute dewpoint. No raw captured response, exact live values, internal RMM fields or metadata are committed. Tests use invented values with the observed tag structure.

Display date/time and METAR coded UTC appear consistent, but numeric time attributes decode approximately eight hours later plus a small offset. They are not reliable UTC observation times. Adapter requires explicit confirmation of displayed UTC and true-north reference before conversion; stores the offset diagnostically, withholds measurements whose numeric metadata differs from the display clock metadata, and never corrects epoch by a guessed offset. Shared clock is not proof of individual sensor freshness. Alias duplicates must match value, unit and metadata; identical tag repeated is rejected. Missing gust is null, zero rain is legitimate. Altimeter setting remains separately labeled; QNH is not inferred. Current-hour accumulation is not a rolling one-hour rain rate or minute increment; do not sum it. METAR report time resolved from coded day/time, not XML epoch; report authenticity/full code not verified.

Local capture (only authorized trusted network, after source confirmation):
python scripts/normalize_awosnet_xml.py --fetch --allow-http --confirmed-source-semantics --output C:\private\awos-snapshot.json

Offline conversion:
python scripts/normalize_awosnet_xml.py C:\private\awos.xml --confirmed-source-semantics --output C:\private\awos-snapshot.json

The file must not already exist and must remain outside the repository. Import JSON into the dashboard. Fetch is one-shot, rejects stale displayed clock, no upload or automated alerts. This is not continuous live ingestion. For public Pages automated publishing, obtain explicit source redistribution authorization, agree polling cadence and transport security, and independently verify clocks and wind reference. HTTP is unauthenticated and unsuitable as sole safety-critical source.

The browser runs at commit 6559fc2 passed both desktop and mobile fixtures, but visual human review and actual external chart freshness remain separate. Forecast and publication safety gates unchanged.
