# Regional source audit — 2026-11-25

- Official BMKG medium/high SIGWX pages load and expose date selection; actual selected image URLs, flight levels and issue/valid times not verified. Do not synthesize file paths or apply a single generic WAFC altitude range to BMKG's regional chart.
- BMKG Upper Air Images page explicitly reports no recent data. Remove 14 guessed RASON image options and direct dated RASON path construction. Preserve official sounding map and upper-air recovery link.
- Legacy SIGWX and CB animation guessed paths could not be retrieved in this audit; not proof of permanent retirement. Replace with official SIGWX, WAFC CB forecast and weekly CB outlook links. Weekly outlook is not an animation or hourly forecast.
- BMKG CEWS two-day OLR image retrieved, generation 2026-11-20; Himawari EH image retrieved, printed 2026-11-25 04:50 UTC. Retain images, no broken placeholder; show source link and explicit unavailable state on failure. Receipt time is not meteorological validity.
- NOAA CPC legacy ENSO/SST and BoM RMM direct GIFs could not be verified by audit browser; switch to reachable official monitoring pages instead of claiming products discontinued. NCICS GIFs are unsupported by the web reader, not established broken; official V1 page still lists corresponding products, so retain those two with failure handling.
- No forecast data, engine, database, production deployment or AWOS configuration changed.
