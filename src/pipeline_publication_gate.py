"""Fail-closed validation for database and dashboard publication candidates."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.awos_quality import build_awos_quality_state
from src.db_manager import LOCATION_NAME
from src.forecast_selection import select_latest_clean_collections
from src.model_registry import MODEL_REGISTRY


GATE_VERSION = "pipeline-publication-gate-v1"
REQUIRED_JSON = (
    "db_health.json",
    "individual_models.json",
    "latest_weights.json",
    "pipeline_health.json",
    "taf_guidance.json",
    "tafor_intel.json",
)


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    if not path.is_file() or path.stat().st_size == 0:
        return None, "missing_or_empty"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"invalid_json: {exc}"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _timestamp_distance_seconds(left: Any, right: Any) -> float | None:
    try:
        a = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
        if b.tzinfo is None:
            b = b.replace(tzinfo=timezone.utc)
        return abs((a - b).total_seconds())
    except (TypeError, ValueError):
        return None


def _validate_taf_payload(payload: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return ["tafor_intel_not_object"]
    for window in ("2300", "0500", "1100", "1700", "default"):
        item = payload.get(window)
        taf = item.get("taf_text") if isinstance(item, dict) else None
        if not isinstance(taf, str) or "TAF WAWP" not in taf or not taf.strip().endswith("="):
            issues.append(f"invalid_taf_payload_{window}")
    return issues


def validate_publication_candidate(
    database: str | Path,
    data_dir: str | Path,
    *,
    minimum_clean_models: int = 6,
    minimum_cycle_rows: int = 24,
    models: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return a machine-readable pass/fail report without modifying the DB."""
    database = Path(database).resolve()
    data_dir = Path(data_dir).resolve()
    hard_failures: list[str] = []
    warnings: list[str] = []
    json_checks: dict[str, Any] = {}
    payloads: dict[str, Any] = {}

    if not database.is_file() or database.stat().st_size == 0:
        hard_failures.append("database_missing_or_empty")
        return {
            "gate_version": GATE_VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "passed": False,
            "hard_failures": hard_failures,
            "warnings": warnings,
            "database": str(database),
            "data_dir": str(data_dir),
        }

    for name in REQUIRED_JSON:
        payload, error = _read_json(data_dir / name)
        json_checks[name] = {"valid": error is None, "error": error}
        if error:
            hard_failures.append(f"required_json_{name}_{error}")
        else:
            payloads[name] = payload

    selected_models = models or tuple(MODEL_REGISTRY)
    uri = f"file:{database.as_posix()}?mode=ro"
    database_latest_scrape = None
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            hard_failures.append(f"sqlite_integrity_{integrity}")

        missing_tables = [
            table for table in ("openmeteo_forecasts", "awos_observations")
            if not _table_exists(conn, table)
        ]
        if missing_tables:
            hard_failures.extend(f"missing_table_{table}" for table in missing_tables)
            selection = {}
            awos_quality = {}
        else:
            database_latest_scrape = conn.execute(
                """
                SELECT MAX(scraped_at) FROM openmeteo_forecasts
                WHERE run_init_utc <> 'historical_forecast_api' AND lead_hours>=0
                """
            ).fetchone()[0]
            _, selection = select_latest_clean_collections(
                conn,
                selected_models,
                minimum_cycle_rows=minimum_cycle_rows,
            )
            awos_quality = build_awos_quality_state(conn, location=LOCATION_NAME)
            if selection["selected_model_count"] < minimum_clean_models:
                hard_failures.append(
                    f"clean_model_quorum_{selection['selected_model_count']}_below_{minimum_clean_models}"
                )
            if selection["models_without_clean_cycle"]:
                warnings.append(
                    "models_without_clean_cycle: "
                    + ", ".join(selection["models_without_clean_cycle"])
                )
            if selection["selection_changed_models"]:
                warnings.append(
                    "latest_cycle_quarantined: "
                    + ", ".join(selection["selection_changed_models"])
                )
            if awos_quality.get("status") == "blocked":
                hard_failures.append("awos_quality_blocked")
            elif not awos_quality.get("verification_eligible", False):
                warnings.append("awos_verification_not_eligible")
            for parameter, state in awos_quality.get("parameter_eligibility", {}).items():
                if not state.get("eligible", False):
                    warnings.append(f"parameter_verification_not_eligible: {parameter}")

    pipeline_health = payloads.get("pipeline_health.json")
    if isinstance(pipeline_health, dict):
        if pipeline_health.get("dashboard_export_skipped"):
            hard_failures.append("dashboard_export_skipped")
        if pipeline_health.get("dashboard_export_succeeded") is not True:
            hard_failures.append("dashboard_export_not_confirmed_successful")

    taf_issues = _validate_taf_payload(payloads.get("tafor_intel.json"))
    hard_failures.extend(taf_issues)

    db_health = payloads.get("db_health.json")
    if isinstance(db_health, dict):
        exported_pull = db_health.get("latest_data_pull_utc")
        if not exported_pull:
            hard_failures.append("db_health_missing_latest_data_pull_utc")
        else:
            distance = _timestamp_distance_seconds(exported_pull, database_latest_scrape)
            if distance is None or distance > 600:
                hard_failures.append("dashboard_database_provenance_mismatch")

    return {
        "gate_version": GATE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": not hard_failures,
        "promotion_eligible": False,
        "database": str(database),
        "database_size_bytes": database.stat().st_size,
        "data_dir": str(data_dir),
        "sqlite_integrity": integrity,
        "database_latest_scrape_utc": database_latest_scrape,
        "minimum_clean_models": minimum_clean_models,
        "minimum_cycle_rows": minimum_cycle_rows,
        "selection": selection,
        "awos_quality": awos_quality,
        "json_checks": json_checks,
        "taf_issues": taf_issues,
        "hard_failures": hard_failures,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="wawp_forecasts.db")
    parser.add_argument("--data-dir", default="docs/data")
    parser.add_argument("--output", default="artifacts/validation/publication_gate.json")
    parser.add_argument("--minimum-clean-models", type=int, default=6)
    args = parser.parse_args()
    report = validate_publication_candidate(
        args.database,
        args.data_dir,
        minimum_clean_models=args.minimum_clean_models,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"],
        "hard_failures": report["hard_failures"],
        "warnings": report["warnings"],
        "report": str(output),
    }, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
