"""Independently reconstruct the exact public raw-model preview from SQLite."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from src.model_preview import CONTRACT, WINDOWS, preview_payloads, selection_manifest, utc
from src.model_registry import PREVIEW_MODEL_REGISTRY as MODEL_REGISTRY


def validate(database, directory, *, now=None, models=tuple(MODEL_REGISTRY)):
    now = now or datetime.now(timezone.utc)
    root = Path(directory)
    problems = []
    try:
        def read(name):
            def reject(value):
                raise ValueError(f"Nonfinite JSON: {value}")
            return json.loads((root / name).read_text(), parse_constant=reject)
        manifest = read("model_preview_manifest.json")
        cutoff = utc(manifest.get("as_of_utc"))
        if manifest.get("contract") != CONTRACT or cutoff is None or cutoff > now:
            raise ValueError("Unknown contract or future/invalid cutoff")
        expiry = utc(manifest.get("expires_at_utc"))
        if manifest.get("status") == "model_only" and (expiry is None or expiry <= now):
            problems.append("expired_model_preview")
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite integrity failed")
            frame, expected, _ = selection_manifest(conn, models, now=cutoff)
        if manifest != expected:
            problems.append("exact_source_selection_mismatch")
        guidance, individual = preview_payloads(frame, expected)
        if read("taf_guidance.json") != guidance or read("individual_models.json") != individual:
            problems.append("numeric_product_mismatch")
        taf = read("tafor_intel.json")
        if set(taf) != set(WINDOWS) or any(
            item.get("status") != "withheld" or item.get("taf_text") is not None or item.get("base_group") is not None
            for item in taf.values()
        ):
            problems.append("unsafe_taf_payload")
        if read("tafor_shadow.json") != {"status": "withheld", "issuances": {}}:
            problems.append("unsafe_shadow_payload")
        return {"passed": not problems, "issues": problems, "status": manifest["status"],
                "model_count": len(expected["models"]), "source_rows": expected["row_count"],
                "source_rows_sha256": expected["rows_sha256"], "taf_status": expected["taf_status"]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
        return {"passed": False, "issues": [*problems, f"invalid_preview:{type(exc).__name__}"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = validate(args.database, args.directory, now=args.as_of)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()
