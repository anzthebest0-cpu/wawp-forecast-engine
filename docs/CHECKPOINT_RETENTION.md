# Scheduled model-only collection and bounded checkpoint generations

The new scheduled workflow runs four times per day at 00:30, 06:30,
12:30 and 18:30 UTC (02:30, 08:30, 14:30 and 20:30 WITA). A collection
may start late on GitHub Actions; it does not change the six-hour absolute
expiry policy. During gaps or provider outages the dashboard may be
unavailable, rather than showing an expired forecast. Manual refresh and
separate read-only publication remain available. The legacy TAF/QM pipeline
remains gated OFF. No forecast weights or correction calculations change.

Every successful cycle archives exact raw source responses in `source-responses/`,
appends native forecasts without changing any previous rows, preserves a
compressed rolling database in Drive `checkpoints/rolling/` and GitHub
`latest-db`, reads both back and independently restores the new checkpoint.
The publisher accepts only the gated model-only preview with TAF withheld.

## Retention boundary

- Keep the newest **three registered managed generations** on both GitHub
  and Drive, plus all pre-policy backups. The two original raw releases,
  the verified storage cutover and first model-only checkpoint, original
  backups, historical delta segments, validation runs, and raw source
  responses are never retirement targets.
- Only new checkpoints in the dedicated `checkpoints/rolling/` folder
  with an immutable registration of the exact GitHub asset metadata are
  eligible. Incomplete/unregistered uploads and name collisions require
  manual review, not automatic deletion.
- Before retirement, independently download and restore the selected
  newest and proposed old Drive checkpoint. Verify both release payload
  and manifest identities. Compare all prior table row hashes and native
  record IDs with the new candidate, including old collection receipts,
  schema and non-decreasing SQLite sequences. A changed old value stops
  retirement. The current release selection and registered asset IDs are
  checked again before deleting the old GitHub manifest and payload.
- Only after confirming those exact redundant GitHub assets are gone is
  the compressed old Drive payload moved to Trash. Keep its manifest and
  a retirement proof identifying the newer checkpoint and row hashes.
  No permanent Drive deletion or whole-trash emptying is performed.
  Drive Trash may still consume quota until its normal expiry. Do not
  clear unrelated user files. If free space is limited, review the actual
  managed retired files and account quota separately.
- If retention cannot be verified, keep the candidate and report a warning;
  the currently valid model preview may still publish. Six active
  managed payloads trigger backpressure and require review before any
  further acquisition. The Drive capacity preflight also requires at least
  1 GiB reported free space where a quota is provided. No source-response or historical-table row is
  discarded in order to bound checkpoint copies.

At the initial 74 MB checkpoint size the active managed generations occupy
roughly 222 MB per backend, plus pinned pre-policy assets. The full database
can still grow as forecast history grows; this avoids multiplying identical
historical data in every checkpoint without deleting scientific history.
At four ~74 MB checkpoints/day, a 30-day Trash window can temporarily hold
roughly 8.9 GB of superseded payloads before Google removes them. Do not
empty all Trash; review targeted managed files and quota as needed.
Native-archive partitioning and longer-term quota tuning remain separate work.
