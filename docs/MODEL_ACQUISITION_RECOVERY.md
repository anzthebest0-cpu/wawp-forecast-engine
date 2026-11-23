# Phase 2: fresh model acquisition rehearsal

This workflow never generates TAFs, trains QM, commits forecast data, updates
the rolling release, or deploys Pages. A same-repository draft PR explicitly
labelled `fresh-model-rehearsal` restores the independently verified production
checkpoint, obtains actual provider responses and exercises the complete
model-only path on a disposable database copy.

The request contract has an explicit 240-second collection budget, at most
two attempts per request, provider `Retry-After` handling, and a circuit stop
after rate-limit exhaustion. A rejected full parameter profile can fall back
to the core profile only on an explicit 400/422 request error. HTTP 429 is
never bypassed with a different endpoint or account. The model preview
requests at most seven days (less for shorter registered native horizons),
with hourly output and UTC labels. Collection time is the actual receipt
time, not confirmed NWP model initialization. See Open-Meteo's
[API documentation](https://open-meteo.com/en/docs),
[model status](https://open-meteo.com/en/docs/model-updates), and
[terms](https://open-meteo.com/en/terms).

The strict decoder checks the grid location, UTC offset, continuous unique
time axis, array lengths and field units. Unsupported/missing fields stay
unknown and impossible physical values are masked, not substituted. It
preserves exact source bytes, request parameters, SHA-256, receipt time,
available fields and coverage in a private, content-addressed source bundle.
Raw bytes are uploaded and downloaded independently before ingestion into
the disposable DB. The second-resolution receipt proxy preserves separate
same-hour collections instead of overwriting their rows, and the receipt
transaction refuses a different response at an existing identity. Replaying
the identical source bundle creates no additional rows.

The existing preview gate independently reconstructs the numeric products,
checks expiry and withholds all automated TAF. Every original table row hash
is rechecked against the original baseline; only new native collection rows
and receipt metadata are allowed. The source backup remains byte-identical.
The actual new observations/forecasts are not attached to public workflow
artifacts; only the bounded acquisition and preservation metadata is exposed.

A safe unavailable outcome is distinct from a functioning provider feed.
The readiness step requires actual newly accepted data and a passing preview
before a later production model-only cutover. When the provider is unavailable,
the existing preview expiry policy permits only previously verified unexpired
collections, never an expired fallback. The legacy live pipeline remains on
hold. Live publication, checkpoint retention, independent AWOS ingestion and
verification-based calibration promotion are separate follow-up stages.
