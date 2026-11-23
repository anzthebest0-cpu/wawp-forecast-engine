# Phase 1: safe model preview and TAF hold

The November 23 audit demonstrated incomplete 24-hour TAFs, invented
weather from missing inputs, and dated products that could be mistaken for
current guidance. This stage does not attempt to repair and operationally
certify all TAF semantics at once. The old algorithms remain available for
offline experiments; **no automated TAF is available in the preview**.

The new raw-model preview selects the latest clean collection per model at
an absolute UTC cutoff, restricts it to this station and future canonical
valid times, excludes expired/future collections and inconsistent WITA
labels, and records an exact source-row digest and per-model expiry. The
internal maximum collection age of six hours (or 1.25 times the registered
provider update frequency, whichever is shorter) is a conservative preview
policy, not an official BMKG warning/TAF criterion. Re-fetching a provider
does not prove the underlying numerical model initialization; collection
cycle is explicitly a proxy.

The preview exposes raw individual models and an experimental equal-model
arithmetic mean (circular mean for wind direction), with the contributing
model count for every field/hour. It does not use QM or the previously
unverified learned weights. Categorical weather codes are not averaged;
missing visibility, precipitation and other fields remain null. Existing
stored precipitation totals are used only if explicitly provided: a legacy
zero derived from missing provider input cannot become a dry forecast. Both
live and historical Open-Meteo parsers now leave absent total precipitation
null instead of combining incomplete components; snowfall depth is not in
the same units as liquid-equivalent total precipitation. See the
[provider parameter documentation](https://open-meteo.com/en/docs).

The stable database key `ECMWF_HRES` remains unchanged to preserve historical
IDs, but its feed is correctly identified as `ecmwf_ifs025` (IFS 0.25° open
data), not native 9 km HRES. Its documented native interval is 3h, then 6h
past 144h; hourly API values are interpolated. Previously exported cadence-
sensitive verification metrics were not recomputed in this phase and must
not be interpreted as verified HRES skill. See the
[ECMWF model/API table](https://open-meteo.com/en/docs/ecmwf-api).

The independent gate reconstructs the complete numeric preview from the
database selection at the declared cutoff. Any mismatch, invalid JSON,
expired active preview, or TAF/shadow text fails. An intentionally
unavailable preview is passable because it contains no current forecast.
The dashboard refuses legacy payloads without the new manifest, removes
expired forecast rows from current views, keeps missing values blank, and
disables the automated TAF editor and copy feature. Historical metrics remain
research artifacts, not verified current operational skill.

The dedicated workflow replays the actual production compressed checkpoint
at its original historical cutoff and at the current time. On a reviewed
merge to main it can publish a static safety-only dashboard with the
explicitly unavailable payload; it does not run the legacy collection,
training or TAF issuance pipeline, and does not modify any database or
release. Leave `WAWP_LIVE_PIPELINE_ENABLED` unset. A fresh model acquisition
and independently tested model-only refresh are separate follow-up work.

Before any automated TAF release, agree station issuance windows, thresholds,
required field evidence and verification truth with the forecasters under
[BMKG TAF SOP 2025](https://web-aviation.bmkg.go.id/storage/files/15183/SOP_Aerodrome_Forecast_2025.pdf).
Independently verify complete 24-hour coverage, prevailing-state change
groups, significant weather, TS/CB, wind/gust, visibility and cloud encoding
against real adjudicated cases. Keep QM and verification development in
shadow mode until time-safe train/holdout pairing, event and continuous
metrics and independent performance checks are established.
