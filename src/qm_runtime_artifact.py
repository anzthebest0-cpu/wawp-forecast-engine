import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.quantile_mapper import _ensure_qm_schema


RUNTIME_DB_NAME = "qm_runtime.sqlite"
SUMMARY_JSON_NAME = "qm_state_summary.json"


def default_artifact_dir(project_root: str | os.PathLike[str] | None = None) -> Path:
    configured = os.environ.get("WAWP_QM_ARTIFACT_DIR")
    if configured:
        return Path(configured)
    root = Path(project_root or Path(__file__).resolve().parents[1])
    return root / "artifacts" / "qm"


def runtime_db_path(project_root: str | os.PathLike[str] | None = None) -> Path:
    return default_artifact_dir(project_root) / RUNTIME_DB_NAME


def summary_json_path(project_root: str | os.PathLike[str] | None = None) -> Path:
    return default_artifact_dir(project_root) / SUMMARY_JSON_NAME


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _fetch_one(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(query, params).fetchone()
    return row[0] if row else None


def _json_array(rows: list[tuple[Any, ...]]) -> str:
    return json.dumps([r[0] for r in rows], ensure_ascii=True)


def _build_summary(source_conn: sqlite3.Connection, artifact_db: Path, source_db_path: Path) -> dict:
    enabled = int(_fetch_one(
        source_conn,
        "SELECT COUNT(*) FROM qm_cdfs WHERE enabled=1 AND COALESCE(deprecated,0)=0",
    ) or 0)
    total = int(_fetch_one(source_conn, "SELECT COUNT(*) FROM qm_cdfs") or 0)
    low_conf = int(_fetch_one(
        source_conn,
        "SELECT COUNT(*) FROM qm_cdfs WHERE enabled=1 AND COALESCE(deprecated,0)=0 AND COALESCE(low_confidence,0)=1",
    ) or 0)
    disabled = int(_fetch_one(
        source_conn,
        "SELECT COUNT(*) FROM qm_cdfs WHERE enabled=0 OR COALESCE(deprecated,0)=1",
    ) or 0)
    valid_start = _fetch_one(source_conn, "SELECT MIN(valid_period_start) FROM qm_cdfs")
    valid_end = _fetch_one(source_conn, "SELECT MAX(valid_period_end) FROM qm_cdfs")
    models = _json_array(source_conn.execute(
        "SELECT DISTINCT model FROM qm_cdfs ORDER BY model"
    ).fetchall())
    parameters = _json_array(source_conn.execute(
        "SELECT DISTINCT parameter FROM qm_cdfs ORDER BY parameter"
    ).fetchall())
    layer_rows = source_conn.execute("""
        SELECT COALESCE(correction_layer, 'unknown'), COUNT(*)
        FROM qm_cdfs
        WHERE enabled=1 AND COALESCE(deprecated,0)=0
        GROUP BY COALESCE(correction_layer, 'unknown')
        ORDER BY 1
    """).fetchall()
    parameter_rows = source_conn.execute("""
        SELECT parameter, COUNT(*)
        FROM qm_cdfs
        WHERE enabled=1 AND COALESCE(deprecated,0)=0
        GROUP BY parameter
        ORDER BY parameter
    """).fetchall()
    source_size_mb = round(source_db_path.stat().st_size / (1024 * 1024), 2) if source_db_path.exists() else None
    artifact_size_mb = round(artifact_db.stat().st_size / (1024 * 1024), 4) if artifact_db.exists() else None
    sha = _sha256_file(artifact_db) if artifact_db.exists() else None
    return {
        "artifact_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_db": str(artifact_db),
        "sha256_runtime_db": sha,
        "artifact_size_mb": artifact_size_mb,
        "source_db": str(source_db_path),
        "source_db_size_mb": source_size_mb,
        "training_window_start": valid_start,
        "training_window_end": valid_end,
        "total_cdfs": total,
        "enabled_cdfs": enabled,
        "low_confidence_cdfs": low_conf,
        "disabled_or_deprecated_cdfs": disabled,
        "model_list": json.loads(models),
        "parameter_list": json.loads(parameters),
        "enabled_by_layer": {str(k): int(v) for k, v in layer_rows},
        "enabled_by_parameter": {str(k): int(v) for k, v in parameter_rows},
        "runtime_label": "historical_prior_global_not_lead_aware",
        "rainfall_amount_qm": "disabled_or_low_confidence_until_strict_wet_wet_validation",
    }


def export_qm_runtime_artifact(
    source_db_path: str | os.PathLike[str],
    artifact_dir: str | os.PathLike[str] | None = None,
    dashboard_data_dir: str | os.PathLike[str] | None = None,
) -> dict:
    """Export compact QM runtime tables from the full archive DB."""
    source_db = Path(source_db_path)
    out_dir = Path(artifact_dir) if artifact_dir else default_artifact_dir(source_db.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = out_dir / RUNTIME_DB_NAME
    summary_path = out_dir / SUMMARY_JSON_NAME

    src = sqlite3.connect(source_db)
    try:
        _ensure_qm_schema(src)
        total = int(_fetch_one(src, "SELECT COUNT(*) FROM qm_cdfs") or 0)
        if total <= 0:
            summary = {
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "runtime_db": str(runtime_path),
                "source_db": str(source_db),
                "total_cdfs": 0,
                "enabled_cdfs": 0,
                "status": "empty",
                "reason": "source database has no qm_cdfs rows",
            }
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return summary

        fd, tmp_name = tempfile.mkstemp(prefix="qm_runtime_", suffix=".sqlite", dir=str(out_dir))
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            dst = sqlite3.connect(tmp_path)
            try:
                _ensure_qm_schema(dst)
                dst.execute("DELETE FROM qm_cdfs")
                cols = _table_columns(dst, "qm_cdfs")
                src_cols = set(_table_columns(src, "qm_cdfs"))
                copy_cols = [c for c in cols if c in src_cols]
                quoted = ", ".join(f'"{c}"' for c in copy_cols)
                placeholders = ", ".join("?" for _ in copy_cols)
                rows = src.execute(f"SELECT {quoted} FROM qm_cdfs").fetchall()
                dst.executemany(f"INSERT INTO qm_cdfs ({quoted}) VALUES ({placeholders})", rows)
                dst.execute("""
                    CREATE TABLE IF NOT EXISTS qm_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                dst.execute("DELETE FROM qm_metadata")
                dst.executemany(
                    "INSERT INTO qm_metadata (key, value) VALUES (?, ?)",
                    [
                        ("generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
                        ("source_db", str(source_db)),
                        ("schema", "qm_runtime_v1"),
                    ],
                )
                dst.commit()
                dst.execute("VACUUM")
                dst.commit()
            finally:
                dst.close()
            os.replace(tmp_path, runtime_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except PermissionError:
                    pass

        summary = _build_summary(src, runtime_path, source_db)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if dashboard_data_dir:
            dashboard_summary = dict(summary)
            dashboard_summary["runtime_db"] = RUNTIME_DB_NAME
            dashboard_summary["source_db"] = Path(str(summary.get("source_db", ""))).name
            dash_path = Path(dashboard_data_dir) / SUMMARY_JSON_NAME
            dash_path.parent.mkdir(parents=True, exist_ok=True)
            dash_path.write_text(json.dumps(dashboard_summary, indent=2), encoding="utf-8")
        return summary
    finally:
        src.close()


def import_qm_runtime_artifact(
    target_conn: sqlite3.Connection,
    artifact_db_path: str | os.PathLike[str] | None = None,
    summary_path: str | os.PathLike[str] | None = None,
) -> dict:
    """Import compact QM runtime CDFs into the active runner database."""
    artifact_path = Path(artifact_db_path) if artifact_db_path else runtime_db_path()
    status = {
        "available": artifact_path.exists(),
        "imported": False,
        "runtime_db": str(artifact_path),
        "imported_cdfs": 0,
        "enabled_cdfs": 0,
        "degraded": False,
        "reason": None,
    }
    if not artifact_path.exists():
        status.update({"degraded": True, "reason": "QM runtime artifact not found"})
        return status

    if summary_path:
        summary_file = Path(summary_path)
    else:
        summary_file = artifact_path.with_name(SUMMARY_JSON_NAME)
    if summary_file.exists():
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
            expected_sha = summary.get("sha256_runtime_db")
            actual_sha = _sha256_file(artifact_path)
            status["sha256_runtime_db"] = actual_sha
            if expected_sha and expected_sha != actual_sha:
                status.update({
                    "degraded": True,
                    "reason": "QM runtime artifact hash mismatch",
                    "artifact_corrupt": True,
                })
                return status
        except Exception as exc:
            status["summary_warning"] = str(exc)

    _ensure_qm_schema(target_conn)
    src = sqlite3.connect(artifact_path)
    try:
        _ensure_qm_schema(src)
        cols = [c for c in _table_columns(target_conn, "qm_cdfs") if c != "id"]
        src_cols = set(_table_columns(src, "qm_cdfs"))
        copy_cols = [c for c in cols if c in src_cols]
        if not copy_cols:
            status.update({"degraded": True, "reason": "QM runtime artifact has no compatible qm_cdfs columns"})
            return status
        quoted = ", ".join(f'"{c}"' for c in copy_cols)
        placeholders = ", ".join("?" for _ in copy_cols)
        rows = src.execute(f"SELECT {quoted} FROM qm_cdfs").fetchall()
        replaced_existing = int(target_conn.execute("SELECT COUNT(*) FROM qm_cdfs").fetchone()[0])
        target_conn.execute("DELETE FROM qm_cdfs")
        target_conn.executemany(
            f"INSERT INTO qm_cdfs ({quoted}) VALUES ({placeholders})",
            rows,
        )
        target_conn.commit()
        status.update({
            "imported": True,
            "imported_cdfs": len(rows),
            "replaced_existing_cdfs": replaced_existing,
            "enabled_cdfs": int(target_conn.execute(
                "SELECT COUNT(*) FROM qm_cdfs WHERE enabled=1 AND COALESCE(deprecated,0)=0"
            ).fetchone()[0]),
            "degraded": len(rows) == 0,
            "reason": None if rows else "QM runtime artifact contained zero CDF rows",
        })
    finally:
        src.close()
    return status
