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

## Incremental archive worker

After the pipeline finishes, the optional archive step processes the oldest
correction ledger runs and older legacy forecast collections. It keeps all
native forecasts, observations, training pairs and live CDFs available to the
existing calculations. It retains the latest recorded ledger run, the current
native cohort (including a fallback when older), and the latest legacy run for
each model and location.

The worker writes an independent SQLite segment with the original IDs,
values, nulls and schema, plus a snapshot of the current CDF table and the
source checksum. A CDF snapshot records the current source state; it does not
resolve historical ledger IDs whose original CDF was already lost.
The segment is verified, losslessly compressed and named by the SHA-256 of
its compressed bytes.

The worker then:

1. Uploads the immutable segment and downloads it again for SHA-256 comparison.
2. Uploads its manifest as a commit marker and verifies the retrieved manifest.
3. Restores and integrity-checks the retrieved segment.
4. In a single SQLite transaction, verifies the source and every retiring
   partition, records the archive receipt in `wawp_archive_segments`, and
   removes only the verified archived IDs.
5. Reclaims the local free pages and prepares a normal rolling checkpoint.

An interrupted upload or mismatch leaves the source rows in the rolling DB.
A retry of identical input reuses the same immutable object and a no-op cycle
does not create a remote object. A run interrupted before publishing its
checkpoint can leave a remote segment without a checkpoint receipt. Preserve
such segments for recovery; a later reconciliation can determine duplicates
by exact row identities and source hashes. Existing release checkpoints are
never deleted by this worker. The next stage must establish native forecast
and observation revision archival and approved checkpoint-generation retention
before pruning release assets or evicting the complete native archive.

### Google Drive configuration

The designated destination is the private WAWP Forecast Engine folder.
Incremental objects will be stored in its private `incremental` subfolder.
Set the parent folder's ID as repository Actions variable
`WAWP_ARCHIVE_FOLDER_ID` instead of publishing the ID in source code.
The ChatGPT-connected Drive account is used for the manually verified backups.
GitHub Actions is a separate execution environment and needs its own authorized
Google OAuth configuration.

On the authorized account's machine, install rclone and configure a dedicated
remote named `wawp` using the Google Drive backend. Use an OAuth identity
approved for unattended access to that folder. Set the remote's root folder ID
to the folder ID above. Verify read/write access with a small temporary test
file before deployment. Use the resulting configuration as the repository
Actions secret `WAWP_ARCHIVE_RCLONE_CONFIG` in GitHub Settings → Secrets and
variables → Actions. Avoid sending the OAuth token or the configuration in
chat or committing them. The workflow forces the designated root folder ID.
See [rclone Drive setup](https://rclone.org/drive/) and
[rclone copyto](https://rclone.org/commands/rclone_copyto/).

For the Windows station computer, `scripts/configure_archive_drive.ps1`
automates the one-time setup, with explicit consent for installation,
Google authorization, and the GitHub secret transfer:

```powershell
powershell -NoProfile -File .\scripts\configure_archive_drive.ps1 -FolderId '<parent-folder-id>'
```

Use a dedicated archival Google account where practical: the Google Drive
OAuth scope allows access to the authorized account's Drive, whereas the
root folder ID limits the destination used by the archive worker. The script
checks that the expected `incremental/` directory exists and that a test file
can be uploaded and downloaded byte-exactly. It stores the dedicated
configuration locally outside the repository, then sends it directly to
GitHub Actions as a secret. Do not share that local file or its contents.
The script applies the `archive-connection-test` label to draft PR #4 to
trigger an isolated Actions workflow. That workflow installs rclone, checks
the configured folder, and performs a second byte-exact upload/readback into
`connection-tests/`. It never opens or modifies the production database.
Review the PR's Checks tab for a successful run before any archival cutover.

### Real historical recovery rehearsal

Label draft PR #4 `archive-validation-test` to execute the separate historical
validation workflow. It retrieves the three existing private Drive backup
parts, validates the pinned ZIP and original database hashes, applies the
incremental worker to a disposable copy, then independently downloads the
historical segment and operational checkpoint from `validation/run-<id>-<attempt>/`.
The rehearsal verifies the complete original table set and schema, the
retained/archived union, original SQLite sequence entries, retry idempotence,
and byte-exact checkpoint restoration. A frozen-clock product replay compares
the baseline and recovered operational DB. Only validation metadata is attached
to the Actions run; no DB or observation payload is published to GitHub. Test
objects remain isolated from the production `incremental/` prefix, and no
release, Pages, production repository variable, or live DB is changed.

The archive step is opt-in through the repository Actions variable
`WAWP_INCREMENTAL_ARCHIVE_ENABLED=true`. It is left disabled until the remote
configuration and a restore exercise are verified. Without any credential,
run a local integration exercise on a disposable DB:

```bash
python -m src.storage_incremental_archive \
  --database disposable-copy.db \
  --remote /absolute/path/to/test-archive \
  --work artifacts/archival-test \
  --report artifacts/archival-test/report.json
```

If archival transfer fails, the pipeline preserves the complete checkpoint
and reports the archive failure. A failed publication gate can also preserve
a structurally sound DB checkpoint for subsequent recovery; it still blocks
the dashboard commit and Pages deployment.

### Validation on the September 15 snapshot

The first exercise used a disposable copy of the 975,970,304-byte
ledger-separated baseline. The next two exercises advanced synthetic cycle
keys on copied rows to isolate storage behavior from model quality changes.

| Exercise | Legacy rows archived | Correction rows archived | Compressed delta | Operational database after archival |
| --- | ---: | ---: | ---: | ---: |
| Existing historical legacy data | 784,128 | 0 | 24,469,976 B | 706,949,120 B |
| Simulated next cycle | 2,688 | 18,432 | 561,018 B | 706,949,120 B |
| Simulated second cycle | 2,688 | 18,432 | 555,573 B | 706,949,120 B |

For all three exercises the remotely restored rows and the retained rows had
the same ordered logical hashes as their pre-archive union. Native forecast,
AWOS, training and CDF row hashes were unchanged by archival. Repeating an
already archived cycle created no additional objects. Fault-injection tests
confirmed that a corrupt download, changed source, or schema trigger prevents
eviction, and an interrupted transfer can be retried against its existing
immutable data object. The transport in these exercises was rclone against an
independent local test directory, not the configured Google Drive account.

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
