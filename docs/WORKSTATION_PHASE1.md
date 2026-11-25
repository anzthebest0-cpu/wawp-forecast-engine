# TITAN workstation phase 1

Scope: browser presentation only. No model acquisition, model aggregation, forecast calculations, database, workflow schedule or preview safety contract changed. This is a raw-model research preview; automated TAF remains withheld.

- Group navigation by operational use, model preview, historical research and administration.
- Add observation-source shortcuts with explicit not-connected status. AWOSnet automation is deferred pending verified transport, access permission, schema, units and timestamps.
- Interpret preview valid times as WITA (UTC+8), matching src/model_preview.py; keep collection metadata UTC. Historical archive timestamps are not reinterpreted in this change.
- Replace summed rainfall bars with grouped alternatives; label model range as descriptive. Add pressure chart, matrix gust, full model valid dates and separate climatology units. Missing monthly-hourly rain cells remain null.
- Remove persistent PAT storage (including legacy localStorage key); this is not a secure backend and an authenticated ingestion workflow is still preferred.
- Browser validation required: UTC date rollover, chart rendering and axes, missing-data appearance, mobile navigation and external chart failures. Never use synthetic observation values or stale preview guidance.
- Follow-up: synchronized time range/toggle, wind barbs/runway components, verified observation collector, detailed multi-scale charts, historical scorecards, component-level refresh and automated browser testing.
