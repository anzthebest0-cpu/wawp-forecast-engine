# TITAN proposed product scope for forecaster review

2026-11-25. Proposed product inventory, not a production release. Storage/retention and operational refresh cadence deferred until user review. Prior GFS pilot passed (45585601901); ECMWF native field indexes verified (45599975062, 45600628840); Ariflab connectivity test (45570590554). No live AWOS relay or authorized automated Ariflab ingestion. No autonomous TAF issuance; no independently issued public nowcast warning.

| Workspace | Product | Source / status |
|---|---|---|
| Forecaster overview | WAWP METAR/SPECI, issued TAF and amendments | Ariflab all_metar.txt, avail_TAF.txt, speci.csv; server accessible, CORS blocked; prefer authorized official OPMET feed |
| Forecaster overview | AWOS live wind/gust, T/Td, RH, pressure, rainfall, solar, density altitude | Existing manual import; HTTP relay and UTC/wind semantics unresolved |
| Forecaster overview | METAR/TAF QC, stale report, source comparison | Ariflab koreksi_metar.txt, koreksi.txt, metarbasi.txt and three Comparison.txt files, links pending permission |
| Local guidance | Existing model time series, individual models, wind barbs, rainfall matrix, T-Td, pressure, cloud, uncertainty | Already existing TITAN; keep independent of spatial products |
| Global drivers | ENSO, IOD, MJO, equatorial waves, SST, OLR | Existing external source cards; verify timestamps |
| Observed regional | Himawari IR 30-minute loop, visible imagery when daylight, radar, lightning, SYNOP, radiosonde | Official sources and Ariflab rason_webgis.html, synop.txt / webgis_synop.html; availability and reuse vary; no raw radar entitlement assumed |
| Ariflab upper air | 925 streamline, 850/700 RH, LI, SI, KI, freezing level, surface/925 wind speed | Nine PNGs browser-load tested; observational interpolation, timestamp and permission checks |
| Ariflab outlook | CB forecast H+1/H+2/H+3 | Three PNGs browser-load tested; GFS rainfall proxy, not observed CB or validated probability |
| Independent regional | 925/850 streamline, 850/700 RH, MSLP | GFS tested, 3 leads; IFS 0.25/0.1 index verified; AIFS RH requires derived calculation |
| Independent moisture | Native PWV, PWV tendency, 925/850 q-vector and moisture flux convergence | GFS/IFS native PWV pilot; AIFS tcw is not tcwv; tendency and convergence pending |
| Independent instability | CAPE/CIN, LI/SI/KI, freezing level, vertical shear, 500 hPa geopotential | IFS mucape and required subset of pressure fields verified by index; diagnostic definitions and additional source fields pending |
| Independent forecast imagery | GFS simulated composite reflectivity; true synthetic IR from ECMWF or future WRF+RTTOV | GFS REFC pilot; ECMWF IR existing human-facing chart, automated access unverified; OLR must remain distinct |
| Independent local maps | Rain accumulation, gust, T/Td, cloud, model difference and agreement | Planned; align accumulation windows, don't label deterministic agreement probability |
| Hazards | Official SIGMET, aerodrome warning, medium/high SIGWX provider links, BMKG CAP | Ariflab CSV/XLSX server accessible CORS blocked; parse, freshness, rights pending; no manual SIGWX panel |
| Regional public guidance | BMKG village forecasts, official Kolaka warnings | Documented public endpoints; integration pending |
| Research | Historical WAWP observations/TAF, rolling 24h Ariflab tables, model verification, climatology | Archive completeness and reuse pending; visibility verification withheld; invalid legacy lead-time withheld |
| Source health | Issue/valid/retrieval time, model cycle, source availability, stale/missing, permissions | Required for all new products |

Models: GFS 0.25, IFS 0.25/0.1 (one family), AIFS 0.25 (native RH and tcwv absent in tested indexes), ICON and ACCESS-G later, GEFS/IFS ENS later. Regional extent 110–132 E, 10 S–5 N provisional. Same valid time and color scales; native grid for display and common grid only for differences. Initial review H+0/6/12; horizon and interval not finalized. No raw GRIB retained by the pilot; artifact review only, not storage architecture decision. Ariflab register on separate branch at 0875bd81faecaafc99e45ae6e1947352948b9231. 
