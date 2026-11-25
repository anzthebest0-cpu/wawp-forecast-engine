# Ariflab public endpoint continuity register

Audit: 2026-11-25. Source: https://ariflab.meteopenerbangan.com/

**Reference inventory only — no permission to poll, republish, or mirror is implied.** The URLs are generated products, not confirmed original service APIs. No credentials, private endpoints, provider content, or live ingestion included. Before any machine-to-machine integration, obtain documented API access and appropriate permission. BMKG terms updated 2026-07-08: https://www.bmkg.go.id/ketentuan-penggunaan .

Prior diagnostic: https://github.com/anzthebest0-cpu/wawp-forecast-engine/actions/runs/45570590554 (five files HTTP 200 but browser fetch blocked; twelve PNGs load). `all_metar.txt?v=1` was the tested URL. No continuous availability, operational validity or freshness guarantee. CSV/XLSX may have multi-hour cache lifetime. Do not mistake a retrieved old forecast for current guidance.

## Publicly linked product references (50)

### OPMET

| File | URL | Evidence |
|---|---|---|
| all_metar.txt | https://ariflab.meteopenerbangan.com/all_metar.txt | Previous one-shot server/browser probe |
| avail_TAF.txt | https://ariflab.meteopenerbangan.com/avail_TAF.txt | Previous one-shot server/browser probe |
| speci.csv | https://ariflab.meteopenerbangan.com/speci.csv | Previous one-shot server/browser probe |
| taf_hourly_board.html | https://ariflab.meteopenerbangan.com/taf_hourly_board.html | Public web retrieval; completeness/freshness not guaranteed |
| metar_webgis_interactive.html | https://ariflab.meteopenerbangan.com/metar_webgis_interactive.html | Public web retrieval; completeness/freshness not guaranteed |

### Warnings

| File | URL | Evidence |
|---|---|---|
| active_warnings.xlsx | https://ariflab.meteopenerbangan.com/active_warnings.xlsx | Previous one-shot server/browser probe |
| warning_24.xlsx | https://ariflab.meteopenerbangan.com/warning_24.xlsx | Catalog-linked; binary content not inspected |
| sigmet.csv | https://ariflab.meteopenerbangan.com/sigmet.csv | Previous one-shot server/browser probe |
| sigmet.xlsx | https://ariflab.meteopenerbangan.com/sigmet.xlsx | Catalog-linked; binary content not inspected |
| terjemahan.txt | https://ariflab.meteopenerbangan.com/terjemahan.txt | Public web retrieval; completeness/freshness not guaranteed |
| sigmet_spatial_analysis.txt | https://ariflab.meteopenerbangan.com/sigmet_spatial_analysis.txt | Public web retrieval; completeness/freshness not guaranteed |

### Surface observations

| File | URL | Evidence |
|---|---|---|
| synop.txt | https://ariflab.meteopenerbangan.com/synop.txt | Public web retrieval; completeness/freshness not guaranteed |
| synop.xlsx | https://ariflab.meteopenerbangan.com/synop.xlsx | Catalog-linked; binary content not inspected |
| laporan_SYNOP.txt | https://ariflab.meteopenerbangan.com/laporan_SYNOP.txt | Public web retrieval; completeness/freshness not guaranteed |
| webgis_synop.html | https://ariflab.meteopenerbangan.com/webgis_synop.html | Public web retrieval; completeness/freshness not guaranteed |

### Rolling 24-hour observation tables

| File | URL | Evidence |
|---|---|---|
| weather_24h.xlsx | https://ariflab.meteopenerbangan.com/weather_24h.xlsx | Catalog-linked; binary content not inspected |
| wind_speed_24h.xlsx | https://ariflab.meteopenerbangan.com/wind_speed_24h.xlsx | Catalog-linked; binary content not inspected |
| visibility_24h.xlsx | https://ariflab.meteopenerbangan.com/visibility_24h.xlsx | Catalog-linked; binary content not inspected |
| cld_convective_24h.xlsx | https://ariflab.meteopenerbangan.com/cld_convective_24h.xlsx | Catalog-linked; binary content not inspected |
| cld_amount_24h.xlsx | https://ariflab.meteopenerbangan.com/cld_amount_24h.xlsx | Catalog-linked; binary content not inspected |
| cld_base_24h.xlsx | https://ariflab.meteopenerbangan.com/cld_base_24h.xlsx | Catalog-linked; binary content not inspected |
| temperature_24h.xlsx | https://ariflab.meteopenerbangan.com/temperature_24h.xlsx | Catalog-linked; binary content not inspected |
| pressure_24h.xlsx | https://ariflab.meteopenerbangan.com/pressure_24h.xlsx | Catalog-linked; binary content not inspected |
| trend_24h.xlsx | https://ariflab.meteopenerbangan.com/trend_24h.xlsx | Catalog-linked; binary content not inspected |

### Upper air

| File | URL | Evidence |
|---|---|---|
| rason_webgis.html | https://ariflab.meteopenerbangan.com/rason_webgis.html | Public web retrieval; completeness/freshness not guaranteed |
| wind_streamlines_925p0_cubic.png | https://ariflab.meteopenerbangan.com/wind_streamlines_925p0_cubic.png | Previous one-shot server/browser probe |
| RH_850p0_cubic.png | https://ariflab.meteopenerbangan.com/RH_850p0_cubic.png | Previous one-shot server/browser probe |
| RH_700p0_cubic.png | https://ariflab.meteopenerbangan.com/RH_700p0_cubic.png | Previous one-shot server/browser probe |
| LI_Surface_cubic.png | https://ariflab.meteopenerbangan.com/LI_Surface_cubic.png | Previous one-shot server/browser probe |
| freezing_level_surface_cubic.png | https://ariflab.meteopenerbangan.com/freezing_level_surface_cubic.png | Previous one-shot server/browser probe |
| Wind_Speed_Surface_cubic.png | https://ariflab.meteopenerbangan.com/Wind_Speed_Surface_cubic.png | Previous one-shot server/browser probe |
| SI_Surface_cubic.png | https://ariflab.meteopenerbangan.com/SI_Surface_cubic.png | Previous one-shot server/browser probe |
| KI_Surface_cubic.png | https://ariflab.meteopenerbangan.com/KI_Surface_cubic.png | Previous one-shot server/browser probe |
| Wind_Speed_925p0_cubic.png | https://ariflab.meteopenerbangan.com/Wind_Speed_925p0_cubic.png | Previous one-shot server/browser probe |

### CB forecast

| File | URL | Evidence |
|---|---|---|
| CB_PRED_H1_BANDARA.png | https://ariflab.meteopenerbangan.com/CB_PRED_H1_BANDARA.png | Previous one-shot server/browser probe |
| CB_PRED_H2_BANDARA.png | https://ariflab.meteopenerbangan.com/CB_PRED_H2_BANDARA.png | Previous one-shot server/browser probe |
| CB_PRED_H3_BANDARA.png | https://ariflab.meteopenerbangan.com/CB_PRED_H3_BANDARA.png | Previous one-shot server/browser probe |
| CB_CLASS_H1.txt | https://ariflab.meteopenerbangan.com/CB_CLASS_H1.txt | Public web retrieval; completeness/freshness not guaranteed |
| CB_CLASS_H2.txt | https://ariflab.meteopenerbangan.com/CB_CLASS_H2.txt | Public web retrieval; completeness/freshness not guaranteed |
| CB_CLASS_H3.txt | https://ariflab.meteopenerbangan.com/CB_CLASS_H3.txt | Public web retrieval; completeness/freshness not guaranteed |

### Quality control

| File | URL | Evidence |
|---|---|---|
| METAR_Comparison.txt | https://ariflab.meteopenerbangan.com/METAR_Comparison.txt | Public web retrieval; completeness/freshness not guaranteed |
| SPECI_Comparison.txt | https://ariflab.meteopenerbangan.com/SPECI_Comparison.txt | Public web retrieval; completeness/freshness not guaranteed |
| TAF_Comparison.txt | https://ariflab.meteopenerbangan.com/TAF_Comparison.txt | Public web retrieval; completeness/freshness not guaranteed |
| trend_report.txt | https://ariflab.meteopenerbangan.com/trend_report.txt | Public web retrieval; completeness/freshness not guaranteed |
| koreksi_metar.txt | https://ariflab.meteopenerbangan.com/koreksi_metar.txt | Public web retrieval; completeness/freshness not guaranteed |
| metarbasi.txt | https://ariflab.meteopenerbangan.com/metarbasi.txt | Public web retrieval; completeness/freshness not guaranteed |
| koreksi_SYNOP.txt | https://ariflab.meteopenerbangan.com/koreksi_SYNOP.txt | Public web retrieval; completeness/freshness not guaranteed |
| waspada_distribution.txt | https://ariflab.meteopenerbangan.com/waspada_distribution.txt | Public web retrieval; completeness/freshness not guaranteed |
| koreksi.txt | https://ariflab.meteopenerbangan.com/koreksi.txt | Public web retrieval; completeness/freshness not guaranteed |

### Metadata

| File | URL | Evidence |
|---|---|---|
| bandara.txt | https://ariflab.meteopenerbangan.com/bandara.txt | Public web retrieval; completeness/freshness not guaranteed |

## Upstream continuity

Ariflab comparison files explicitly name Web Aviation and BMKGsatu. Indonesia's ICAO MET SG/29 IP/27 (Aug 2025) identifies BMKGsatu as the collection system and INA-Switching as dissemination. https://www.icao.int/sites/default/files/APAC/Meetings/2025/2025%20MET%20SG29/4-Information%20Papers/IP27-AI.5-IDN-INDONESIA-OPMET-CHANGES.pdf

Official human-facing sources: https://web-aviation.bmkg.go.id/web/metar_speci.php and https://web-aviation.bmkg.go.id/web/taf.php . Exact upstream API and entitlement remain unknown. Request read-only official API documentation, rate limits, retention and redistribution rights, and full corrections/amendment/cancellation history. Prefer station-owned issued-report archive for WAWP. Preserve only authorized local datasets, record issuance/validity, retrieval, source and checksums; no stale observations or expired TAF presented as live. Do not use a proxy to circumvent access restrictions. Derived CB classification text uses daily rainfall thresholds, not direct CB detection; see CB_CLASS_H1.txt. No dependency on the inventory for operational forecasts.
