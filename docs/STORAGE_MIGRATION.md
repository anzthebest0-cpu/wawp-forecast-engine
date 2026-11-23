# Lossless WAWP storage migration

Baseline commit: `e29cd248d208009dd875d4395a5e6c95d160f59c`.

## Stage 1 — compressed rolling checkpoint

The proposed workflow accepts existing raw `.db` release assets and new
`.db.zst` assets paired with `.db.zst.json` manifests. It checks GitHub asset
size and digest (when provided), the compressed-file SHA-256, decompressed
database SHA-256, and SQLite integrity. The upload is staged in the order
compressed checkpoint, read-back byte comparison, manifest, read-back comparison.
An interrupted upload without a completed manifest is ignored when restoring.
Use Zstandard 3; the Python helper uses only the standard library and the
`zstd` executable.

The workflow preserves the existing rolling release assets. This is an interim
recovery bridge: before sustained scheduled runs, establish an independent
archive, document its retention policy, and implement reviewed pruning of older
checkpoint generations. A restored repository backup and a verified remote
manifest are prerequisites for retirement of a generation.

Local byte-exact backup / restore (source must be a closed, consistent DB):

```bash
python -m src.storage_snapshot pack wawp_forecasts.db baseline.db.zst
python -m src.storage_snapshot restore baseline.db.zst restored.db
```

For an actively written SQLite database first use SQLite's Online Backup API
or stop/checkpoint the writer. The packer rejects a nonempty WAL.

## Stages 2–4 — offline candidates

These are read-only-source migration utilities. The production workflow still
uses the existing full-history forecast and calibration calculations.
Run each on an independent, verified full snapshot, never on the station's
only copy. Destination directories must not exist.

```bash
python -m src.storage_ledger_split baseline.db split-ledger
python -m src.storage_ledger_split split-ledger/operational.db split-legacy --archive-legacy
python -m src.storage_ledger_split split-legacy/operational.db split-training --archive-training
python -m src.storage_calibration split-training/historical_tables.db qm-artifact
```

Each split validates all retained rows with an ordered SHA-256, verifies the
disjoint archived partitions and checks SQLite integrity. Preserve all three
archive DBs and manifests together with the full baseline snapshot. A complete
restore can use the original byte-exact baseline. The three historical
partitions retain original row IDs, all values and all missingness; the
calibration archive also has an identical seed CDF table. The hot checkpoint
retains the current ledger run for rerun deduplication and the latest legacy
collection for each model/station, preserving the existing latest-run query.
AUTOINCREMENT high-water marks are retained even when their original rows
are in the archive.

The native forecast history and all hourly AWOS data stay available. Historical
legacy rows are archived as originally recorded because the mirror and native
representations differ on some fields despite matching forecast keys.

The detached trainer calls the same existing fit and export routines, records
the full training-sample digest, seed CDF digest, runtime artifact digest,
software version and the underlying trainer source digest. The candidate
checkpoint with an empty training-pair cache is an exporter-only prototype.
The normal pipeline would rebuild that table. A separate incremental pairing
worker and an approved-artifact promotion mechanism must be implemented and
tested before activating the split checkpoint in the scheduled workflow.

## Reproducibility and activation

```bash
PYTHONPATH=. python tests/replay_storage_equivalence.py baseline-copy.db candidate-copy.db comparison \
  --timestamp 2026-09-15T17:16:35+00:00
```

The replay takes disposable copies because the exporter writes audit records.
It starts each run with the same previous public JSON, clock and software
environment and compares all top-level product files, exempting the intentionally
different physical DB size. It does not call the forecast API or publish.
Compare training results separately on the same full sample and seed artifact.

Before cutover, restore and validate the archive in an independent approved
station storage destination. Configure Google Shared Drive only after selecting
the authorized folder, access identity and quota. Perform historical
archive/reconstruction and fresh-data replay, including late observations and
fallback model runs. Archive and validate each new partition before evicting
it from hot storage. The full original snapshot remains the rollback path.

Do not enable the scheduled workflow solely because compression succeeds.
Address the aviation guidance validity/coverage and stale-data findings from
the 23 November 2026 audit before routine TAF guidance is exposed.
