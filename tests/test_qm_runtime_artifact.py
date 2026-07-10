import json
import sqlite3

from src.qm_runtime_artifact import export_qm_runtime_artifact, import_qm_runtime_artifact
from src.quantile_mapper import _ensure_qm_schema, apply_qm_with_layers


def test_qm_runtime_artifact_round_trip(tmp_path):
    source_db = tmp_path / "source.sqlite"
    artifact_dir = tmp_path / "artifact"
    dashboard_dir = tmp_path / "dashboard"

    source = sqlite3.connect(source_db)
    _ensure_qm_schema(source)
    source.execute(
        """
        INSERT INTO qm_cdfs (
            model, parameter, lead_bucket, fcst_quantiles, obs_quantiles,
            n_samples, crps_before, crps_after, bias_before, bias_after,
            trained_at, enabled, method, low_confidence, metadata,
            source_type, correction_layer, regime, valid_period_start,
            valid_period_end, n_events, validation_method, mae_before,
            mae_after, skill_score, deprecated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ECMWF_HRES",
            "temperature",
            "GLOBAL",
            json.dumps([20.0, 30.0]),
            json.dumps([21.0, 31.0]),
            100,
            None,
            None,
            1.0,
            0.0,
            "2026-07-06 00:00:00",
            1,
            "empirical",
            0,
            "{}",
            "continuous_historical",
            "historical_prior",
            "ALL",
            "2023-01-01 00:00:00",
            "2026-06-30 10:00:00",
            100,
            "walk_forward",
            1.0,
            0.5,
            0.5,
            0,
        ),
    )
    source.commit()
    source.close()

    summary = export_qm_runtime_artifact(source_db, artifact_dir, dashboard_dir)
    assert summary["enabled_cdfs"] == 1
    assert (artifact_dir / "qm_runtime.sqlite").exists()
    assert (dashboard_dir / "qm_state_summary.json").exists()

    target = sqlite3.connect(":memory:")
    status = import_qm_runtime_artifact(
        target,
        artifact_dir / "qm_runtime.sqlite",
        artifact_dir / "qm_state_summary.json",
    )
    assert status["imported"] is True
    assert status["enabled_cdfs"] == 1
    status = import_qm_runtime_artifact(
        target,
        artifact_dir / "qm_runtime.sqlite",
        artifact_dir / "qm_state_summary.json",
    )
    assert status["imported"] is True
    assert status["replaced_existing_cdfs"] == 1
    assert target.execute("SELECT COUNT(*) FROM qm_cdfs").fetchone()[0] == 1

    correction = apply_qm_with_layers(25.0, "ECMWF_HRES", "temperature", 48.0, conn=target)
    assert correction["correction_layer_used"] == "historical_prior"
    assert correction["final_value"] == 26.0
    target.close()


def test_operational_residual_requires_explicit_promotion(tmp_path):
    db_path = tmp_path / "layers.sqlite"
    conn = sqlite3.connect(db_path)
    _ensure_qm_schema(conn)
    columns = """
        model, parameter, lead_bucket, fcst_quantiles, obs_quantiles,
        n_samples, trained_at, enabled, method, low_confidence, metadata,
        source_type, correction_layer, regime, deprecated
    """
    conn.execute(f"""
        INSERT INTO qm_cdfs ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "ECMWF_HRES", "temperature", "GLOBAL", json.dumps([20.0, 30.0]), json.dumps([21.0, 31.0]),
        200, "2026-07-10 00:00:00", 1, "empirical", 0, "{}",
        "continuous_historical", "historical_prior", "ALL", 0,
    ))
    conn.execute(f"""
        INSERT INTO qm_cdfs ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "ECMWF_HRES", "temperature", "L5_48plus", json.dumps([20.0, 30.0]), json.dumps([22.0, 32.0]),
        200, "2026-07-10 00:00:00", 1, "empirical", 0, "{}",
        "operational_multiinit", "operational_residual", "ALL", 0,
    ))
    conn.commit()

    observe_only = apply_qm_with_layers(25.0, "ECMWF_HRES", "temperature", 48.0, conn=conn)
    assert observe_only["operational_residual_available"] is True
    assert observe_only["correction_layer_used"] == "historical_prior"
    assert observe_only["final_value"] == 26.0

    promoted = apply_qm_with_layers(
        25.0, "ECMWF_HRES", "temperature", 48.0,
        conn=conn, allow_operational_residual=True,
    )
    assert promoted["correction_layer_used"] == "operational_residual"
    assert promoted["final_value"] == 28.0
    conn.close()
