# WAWP Rainfall Shadow Replay

## Standing

- Audit only: no live weighting, QM, consensus, or TAF behavior was changed.
- Source: Open-Meteo continuous historical forecast stream; not model-run or lead-aware.
- Development: 2023-01-01 through 2025-12-31 UTC.
- Independent holdout: 2026-01-01 through 2026-06-30 UTC.
- Missing hours are unknown, episodes break at gaps, and incomplete 3-hour blocks are excluded.

## Data Gate

- Exact joined rows: 240,448
- Usable rows: 239,550
- Quarantined rows: 898
- Duplicate model/valid-time rows: 0

## Why Counts Differ

Strict wet hours, contiguous episodes, and complete 3-hour accumulations are different verification units. The legacy centered maximum expands an event into neighboring hours and uses future information; it is retained only as a diagnostic denominator and is never ranked.

| Split | Threshold | Strict Hours | Episodes | Legacy Expanded | Complete 3h Wet |
|---|---:|---:|---:|---:|---:|
| development_2023_2025 | 1.5 mm/h | 716 | 458 | 1,602 | 565 |
| holdout_2026_h1 | 1.5 mm/h | 79 | 56 | 191 | 77 |

## Holdout Top Three by Product at 1.5 mm/h

| Product | Rank | Model | CSI | POD | FAR | Frequency Bias |
|---|---:|---|---:|---:|---:|---:|
| cadence_aware_episode | 1 | GEM_GLOBAL | 0.1765 | 0.2679 | 0.6591 | 0.78571 |
| cadence_aware_episode | 2 | UKMO_GLOBAL_10KM | 0.129 | 0.2857 | 0.8095 | 1.5 |
| cadence_aware_episode | 3 | ECMWF_HRES | 0.1031 | 0.1786 | 0.8039 | 0.91071 |
| complete_3h_rate_equivalent | 1 | GEM_GLOBAL | 0.1039 | 0.25 | 0.8491 | 1.65625 |
| complete_3h_rate_equivalent | 2 | ECMWF_HRES | 0.093 | 0.25 | 0.871 | 1.9375 |
| complete_3h_rate_equivalent | 3 | GFS_GLOBAL | 0.0889 | 0.25 | 0.8788 | 2.0625 |
| complete_3h_same_numeric | 1 | GEM_GLOBAL | 0.1667 | 0.4286 | 0.7857 | 2.0 |
| complete_3h_same_numeric | 2 | ECMWF_HRES | 0.1667 | 0.5195 | 0.803 | 2.63636 |
| complete_3h_same_numeric | 3 | UKMO_GLOBAL_10KM | 0.1392 | 0.3506 | 0.8125 | 1.87013 |
| episode_pm1h | 1 | UKMO_GLOBAL_10KM | 0.129 | 0.2857 | 0.8095 | 1.5 |
| episode_pm1h | 2 | GEM_GLOBAL | 0.1111 | 0.1786 | 0.7727 | 0.78571 |
| episode_pm1h | 3 | ECMWF_HRES | 0.1031 | 0.1786 | 0.8039 | 0.91071 |
| episode_pm2h | 1 | UKMO_GLOBAL_10KM | 0.1864 | 0.3929 | 0.7381 | 1.5 |
| episode_pm2h | 2 | GEM_GLOBAL | 0.1765 | 0.2679 | 0.6591 | 0.78571 |
| episode_pm2h | 3 | ECMWF_HRES | 0.1758 | 0.2857 | 0.6863 | 0.91071 |
| strict_hourly | 1 | GEM_GLOBAL | 0.0955 | 0.2658 | 0.8704 | 2.05063 |
| strict_hourly | 2 | UKMO_GLOBAL_10KM | 0.0805 | 0.2405 | 0.892 | 2.22785 |
| strict_hourly | 3 | ECMWF_HRES | 0.0723 | 0.2278 | 0.9043 | 2.37975 |

## Frozen Ensemble Holdout Comparison at 1.5 mm/h

| Product | Candidate | CSI | POD | FAR | Models |
|---|---|---:|---:|---:|---|
| cadence_aware_episode | development_selected_top3_median | 0.1684 | 0.2857 | 0.7091 | GFS_GLOBAL,CMA_GRAPES_GLOBAL,METEOFRANCE_ARPEGE_WORLD |
| cadence_aware_episode | all8_median_baseline | 0.1406 | 0.1607 | 0.4706 | ECMWF_HRES,GFS_GLOBAL,ICON_SEAMLESS,GEM_GLOBAL,CMA_GRAPES_GLOBAL,JMA_GSM,METEOFRANCE_ARPEGE_WORLD,UKMO_GLOBAL_10KM |
| complete_3h_rate_equivalent | development_selected_top3_median | 0.0893 | 0.1562 | 0.8276 | METEOFRANCE_ARPEGE_WORLD,GFS_GLOBAL,GEM_GLOBAL |
| complete_3h_rate_equivalent | all8_median_baseline | 0.0526 | 0.0625 | 0.75 | ECMWF_HRES,GFS_GLOBAL,ICON_SEAMLESS,GEM_GLOBAL,CMA_GRAPES_GLOBAL,JMA_GSM,METEOFRANCE_ARPEGE_WORLD,UKMO_GLOBAL_10KM |
| complete_3h_same_numeric | development_selected_top3_median | 0.1814 | 0.5584 | 0.7882 | METEOFRANCE_ARPEGE_WORLD,GFS_GLOBAL,ICON_SEAMLESS |
| complete_3h_same_numeric | all8_median_baseline | 0.2162 | 0.4156 | 0.6893 | ECMWF_HRES,GFS_GLOBAL,ICON_SEAMLESS,GEM_GLOBAL,CMA_GRAPES_GLOBAL,JMA_GSM,METEOFRANCE_ARPEGE_WORLD,UKMO_GLOBAL_10KM |
| episode_pm1h | development_selected_top3_median | 0.1277 | 0.2143 | 0.76 | GFS_GLOBAL,METEOFRANCE_ARPEGE_WORLD,UKMO_GLOBAL_10KM |
| episode_pm1h | all8_median_baseline | 0.0896 | 0.1071 | 0.6471 | ECMWF_HRES,GFS_GLOBAL,ICON_SEAMLESS,GEM_GLOBAL,CMA_GRAPES_GLOBAL,JMA_GSM,METEOFRANCE_ARPEGE_WORLD,UKMO_GLOBAL_10KM |
| episode_pm2h | development_selected_top3_median | 0.1522 | 0.25 | 0.72 | GFS_GLOBAL,METEOFRANCE_ARPEGE_WORLD,UKMO_GLOBAL_10KM |
| episode_pm2h | all8_median_baseline | 0.1406 | 0.1607 | 0.4706 | ECMWF_HRES,GFS_GLOBAL,ICON_SEAMLESS,GEM_GLOBAL,CMA_GRAPES_GLOBAL,JMA_GSM,METEOFRANCE_ARPEGE_WORLD,UKMO_GLOBAL_10KM |
| strict_hourly | development_selected_top3_median | 0.0695 | 0.1646 | 0.8926 | METEOFRANCE_ARPEGE_WORLD,GFS_GLOBAL,CMA_GRAPES_GLOBAL |
| strict_hourly | all8_median_baseline | 0.0472 | 0.0633 | 0.8438 | ECMWF_HRES,GFS_GLOBAL,ICON_SEAMLESS,GEM_GLOBAL,CMA_GRAPES_GLOBAL,JMA_GSM,METEOFRANCE_ARPEGE_WORLD,UKMO_GLOBAL_10KM |

## Rank Stability at 1.5 mm/h

| Product | Development Top Three Retained in Holdout | Mean Absolute Rank Change |
|---|---:|---:|
| cadence_aware_episode | 0 of 3 | 2.75 |
| complete_3h_rate_equivalent | 2 of 3 | 2.50 |
| complete_3h_same_numeric | 0 of 3 | 3.00 |
| episode_pm1h | 1 of 3 | 2.75 |
| episode_pm2h | 1 of 3 | 2.75 |
| strict_hourly | 0 of 3 | 3.00 |

## Paired Holdout Bootstrap Delta at 1.5 mm/h

Delta is development-selected top-three median minus all-eight median. Positive POD/CSI/HSS is favorable; negative FAR is favorable. An interval crossing zero is inconclusive.

| Product | Metric | Mean Delta | 95% CI |
|---|---|---:|---:|
| strict_hourly | POD | 0.10031 | 0.03333 to 0.17732 |
| strict_hourly | FAR | 0.04985 | -0.04043 to 0.1609 |
| strict_hourly | CSI | 0.02216 | -0.00849 to 0.05825 |
| strict_hourly | HSS | 0.02938 | -0.02636 to 0.09419 |
| episode_pm1h | POD | 0.10697 | 0.02598 to 0.2 |
| episode_pm1h | FAR | 0.10787 | -0.08145 to 0.32154 |
| episode_pm1h | CSI | 0.03872 | -0.01315 to 0.09466 |
| episode_pm2h | POD | 0.08689 | -0.00847 to 0.20588 |
| episode_pm2h | FAR | 0.24035 | 0.07161 to 0.40343 |
| episode_pm2h | CSI | 0.01181 | -0.05888 to 0.08515 |
| complete_3h_rate_equivalent | POD | 0.09238 | 0.0 to 0.17143 |
| complete_3h_rate_equivalent | FAR | 0.04935 | -0.23077 to 0.48096 |
| complete_3h_rate_equivalent | CSI | 0.039 | -0.03453 to 0.10663 |
| complete_3h_rate_equivalent | HSS | 0.05995 | -0.07039 to 0.17876 |

## Operational Verdict

**do not promote rainfall event-window weights from this replay; retain strict, episode, and complete-block products as separate shadow diagnostics**

The development-selected ensemble improves detection in some products, but most CSI/HSS intervals cross zero, false-alarm rates remain high, and rankings move materially between development and holdout. The plus/minus two-hour selected ensemble has a significantly worse FAR than the all-eight median. This evidence does not justify activating event-window weights.

## Interpretation Boundary

The holdout can compare rainfall occurrence and timing definitions for this continuous historical stream. It cannot establish model-run lead skill, because archived initialization and lead provenance are absent. No candidate should influence operations until meteorological review and live multi-init shadow evidence agree.
