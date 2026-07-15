# WAWP Codex Instructions

These instructions apply to all work under `D:\UJI_PERFORMA_MODEL\meteologix-wawp-main`.

## Operating Priorities

1. Preserve aviation-meteorological meaning, issuance provenance, and auditability.
2. Treat current forecast and observation data, code, configuration, tests, and
   generated validation evidence as the implementation source of truth.
3. Treat approved decisions in the BMKG knowledge vault as durable product
   policy unless the user explicitly replaces them.
4. Do not present guidance as an official issued TAF, a verified observation,
   or a lead-aware QM correction when the provenance does not support that claim.

## Automatic Context Bootstrap

For substantive work, run this read-only check before making changes:

```powershell
powershell -ExecutionPolicy Bypass -File .codex\context-bootstrap.ps1
```

Then read only the task-relevant notes. Start with:

- `D:\BMKG_KNOWLEDGE\Home.md`
- `D:\BMKG_KNOWLEDGE\20 Projects\WAWP\WAWP Overview.md`
- `D:\BMKG_KNOWLEDGE\20 Projects\WAWP\Operational Workflow.md`
- `D:\BMKG_KNOWLEDGE\20 Projects\WAWP\Decision Log.md`
- `D:\BMKG_KNOWLEDGE\20 Projects\WAWP\Known Limitations.md`

Do not load the whole vault. The vault is read-only unless the task explicitly
includes an approved decision, SOP, incident, handover, or documentation update.

## Graphify

Use Graphify for substantive engineering, cross-module debugging, architecture,
workflow, data-acquisition, dashboard, consensus, QM, TAF, or release-database
impact analysis. Do not use it for wording-only edits, isolated data inspection,
or simple commands.

The project graph is `graphify-out/`. If it is stale, refresh it before relying
on it:

```powershell
graphify update .
graphify cluster-only . --no-label
```

For a local full rebuild after major structural changes:

```powershell
graphify extract . --code-only
graphify cluster-only . --no-label
```

Use `C:\Users\MY ASUS\.local\bin\graphify.exe` if `graphify` is not available
on the active PATH. Graphify is a navigation aid only: verify its conclusions
against current source, configuration, tests, and generated products. Refresh
the graph after validated structural changes; do not install an automatic Git hook.

## Context Priority

When sources conflict, use this order and disclose material conflicts:

1. Current user instruction.
2. Current official source data and verified product evidence.
3. Current code, configuration, and tests.
4. Approved decision-log and SOP entries.
5. Other vault notes and historical reports.
6. Graphify inferred relationships.
