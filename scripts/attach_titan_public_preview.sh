#!/usr/bin/env bash
# Keep the root dashboard independent of the frozen preview.
set -euo pipefail
site="$1"
mode="${2:-read-only}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# Prefer the last committed rolling release. A missing rolling release is
# the only condition that permits the initial frozen seed fallback.
set +e
python scripts/titan_image_release.py attach "$site"
status=$?
set -e
if [ "$status" -eq 0 ]; then exit 0; fi
if [ "$status" -ne 3 ]; then exit "$status"; fi
tag=titan-frozen-preview-20261126
asset=titan-panels-20261126.zip
if gh release download "$tag" --pattern "$asset" --dir "$work"; then
  python scripts/package_titan_public_preview.py publish "$work/$asset" "$site"
  exit 0
fi
# Exact successful Actions run; never choose latest or download numerical data.
gh run download 45764124872 --name titan-auto-validated-site --dir "$work/source"
python scripts/package_titan_public_preview.py pack "$work/source" "$work/$asset"
python scripts/package_titan_public_preview.py publish "$work/$asset" "$site"
if [ "$mode" = seed-release ]; then
  # Separate release tag, never alter latest-db or its assets.
  if ! gh release view "$tag" >/dev/null 2>&1; then
    gh release create "$tag" --target "$GITHUB_SHA" --title 'TITAN frozen research preview — 26 Nov 2026' --notes 'Frozen historical visual forecast panels only. Not operational guidance.' --latest=false
  fi
  gh release upload "$tag" "$work/$asset"
  mkdir "$work/readback"
  gh release download "$tag" --pattern "$asset" --dir "$work/readback"
  cmp "$work/$asset" "$work/readback/$asset"
  echo 'TITAN_DURABLE_PUBLIC_IMAGE_RELEASE_PASS'
fi
