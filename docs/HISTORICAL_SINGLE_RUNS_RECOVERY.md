# WAWP archived forecast recovery

The verified AWOS recovery trial has 2,324 new hourly observations from
2026-08-19 01 UTC through 2026-11-23 23 UTC. Original forecast collections
stop covering the observation period after 2026-10-01 except for the recent
live restart. Recover both ECMWF products (0.25° and actual 9 km) from
19 August, and all nine model products from 1 October through 23 November.
The registry-based plan has 2,180 model/initialization combinations, including
GEM's two daily cycles. Missing cycles are recorded rather than substituted.

The Single Runs endpoint is distinct from the live blended forecast API.
The simulation initialization is not the original public release time;
archival retrieval time is not an historical collection receipt. Lead-aware
research must apply an explicit availability assumption, event-window rules,
and a chronological independent holdout. Neither recovered forecasts nor
unauthenticated availability assumptions enter current TAF/calibration.

Run **WAWP Historical Single Runs Recovery** on main (manual dispatch resumes
verified batches). The first main workflow-file merge launches it. The job
uses compressed ZIP batches in `wawp:historical-single-runs`, containing exact
provider response bytes and a manifest, explicit UTC axes, field availability,
grid coordinates, per-run SHA-256, and errors. Each batch is downloaded and
verified before advancing. Previously accepted runs are not re-requested.
It pauses on provider HTTP 429 or the runner time budget. No deletions, no live
database writes, no release changes and no automatic scientific promotion.

The archived response hourly grid may include interpolation and null tails
when a particular run has a shorter horizon; preserve these rather than
filling or fabricating values. Use provider total precipitation, not a sum of
rain and showers or minute accumulation snapshots. Existing raw preview and
IFS-independent consensus policy remain unchanged.
