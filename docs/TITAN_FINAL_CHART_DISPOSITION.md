# Final presentation pass — chart disposition
Scope: existing source/code inventory, not a manual screenshot audit. Preserve every underlying dataset and current forecast gates.
| Product | Decision | Reason |
|---|---|---|
| Wind and gust | Primary, shared axis | Same units and joint timing; solid vs dashed |
| Wind direction barbs | Retain directly below wind, compact | Circular direction not a linear scalar; selectable valid hour |
| Hourly rain | Primary, bars | Exact hourly amounts; no smoothing |
| T and Td | Primary, shared axis | Joint thermodynamic context |
| Low/mid/high cloud cover | Primary, line tracks | Explicit percent values; no ceiling inference |
| Cumulative displayed rainfall | Advanced disclosure | Changes in contributing models and gaps; not independent accumulated forecast |
| Pressure, T−Td | Advanced disclosure | Secondary diagnostic; not visibility prediction |
| Aligned hourly table | Disclosure, retain direct matrix view | Exact numerical inspection, no duplicative default table |
| Individual model traces | Disagreement tab, param filter | Stable model palette; no recalibrated mean or skill ranking |
| Rain model heatmap | High in disagreement | Alternative forecasts, not combined |
| Temperature/wind/T−Td quantiles | Retain below comparison | Descriptive spread only; no probability intervals |
| Individual model table | Retain secondary | Unique metadata and exact values |
| Spatial 25 products | Retain all, map-first mode | Existing immutable image pixels/legends, exact valid windows, native availability |
| Historical diurnal temperature, rain, wind/gust, sector frequencies, monthly-hourly rain | Reference | Useful local context but not current guidance |
| Fog/low-cloud historical proxy | Reference only | Not verified visibility |
| Archived scorecard and verification plots | Reference | Explicit coverage/metric limitations |
| Archived weight/drift, system health, experimental TAF | Utilities | Not operational current skill |
| Remote satellite/radar/lightning/Ariflab | Observation workspace | Source timestamps and link-only limitations preserved |

Explicit shared valid-time state drives plots via SVG grid coordinates and the selected epoch, numeric readout, wind barb selection and spatial rain deep link. All core plots use matching x-domain and fixed label width. Advanced charts render only when opened. Graphical model traces preserve actual values and missing data, use stable model identifiers and never recompute forecast means. Spatial map-first toggle retains source validity and original image legends; previous/next exact windows available without the control rail. No synchronized image pan or common colorbar attempted. Actual populated regression tests must pass before deployment. Historical presentation remains source-audited, not independently visually reviewed.