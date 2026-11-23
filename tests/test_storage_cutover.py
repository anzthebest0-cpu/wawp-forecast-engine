import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from scripts import promote_verified_storage as cutover
from src.storage_incremental_archive import archive_incrementally
from src.storage_snapshot import file_digest, pack, restore


def test_pinned_release_fails_closed():
    asset = {"id": 1, "name": cutover.BASELINE_ASSET, "state": "uploaded",
             "size": cutover.BASELINE_BYTES, "digest": f"sha256:{cutover.BASELINE_SHA}",
             "created_at": "2026-09-15T17:21:34Z"}
    cutover.require_pinned_release([[asset]])
    for altered in ({**asset, "digest": None}, {**asset, "size": 1}, {**asset, "digest": "sha256:wrong"}):
        with pytest.raises(ValueError):
            cutover.require_pinned_release([[altered]])
    newer = {**asset, "id": 2, "name": "wawp_forecasts-2-1.db", "created_at": "2026-11-23T00:00:00Z"}
    with pytest.raises(ValueError, match="changed"):
        cutover.require_pinned_release([[asset, newer]])


def test_receipt_mismatch_cannot_modify_candidate(tmp_path):
    candidate = tmp_path / "candidate.db"
    manifest = {"archive_name": "history.db.zst"}
    with closing(sqlite3.connect(candidate)) as conn:
        conn.execute("CREATE TABLE wawp_archive_segments(archive_name TEXT PRIMARY KEY,remote_path TEXT,manifest_json TEXT,verified_at_utc TEXT)")
        conn.execute("INSERT INTO wawp_archive_segments VALUES(?,?,?,?)", ("history.db.zst", "unexpected:location", json.dumps(manifest), "old"))
        conn.commit()
    before = file_digest(candidate)
    with pytest.raises(ValueError, match="Receipt is not"):
        cutover.promote_receipt(candidate, manifest, "wawp:validation/history.db.zst", "wawp:incremental/history.db.zst")
    assert file_digest(candidate) == before


def test_complete_promotion_is_lossless_on_independent_backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "rclone.conf"
    config.write_text("[wawp]\ntype = local\n")
    monkeypatch.setenv("RCLONE_CONFIG", str(config))
    source = tmp_path / "original.db"
    with closing(sqlite3.connect(source)) as conn:
        conn.executescript("""
        CREATE TABLE qm_corrections_applied(id INTEGER PRIMARY KEY AUTOINCREMENT,pipeline_run_id TEXT NOT NULL,value REAL);
        INSERT INTO qm_corrections_applied(pipeline_run_id,value) VALUES ('2026-09-15 00:00:00',1),('2026-09-15 12:00:00',2);
        CREATE TABLE meteologix_forecasts(id INTEGER PRIMARY KEY AUTOINCREMENT,location TEXT,model TEXT,run_init_utc TEXT);
        INSERT INTO meteologix_forecasts(location,model,run_init_utc) VALUES ('WAWP','GFS_GLOBAL','2026-09-15 00:00:00'),('WAWP','GFS_GLOBAL','2026-09-15 12:00:00');
        CREATE TABLE qm_cdfs(id INTEGER PRIMARY KEY,payload BLOB);
        INSERT INTO qm_cdfs VALUES(1,X'0001');
        CREATE TABLE openmeteo_forecasts(id INTEGER PRIMARY KEY,model TEXT,run_init_utc TEXT,scraped_at TEXT,lead_hours REAL);
        INSERT INTO openmeteo_forecasts VALUES(1,'GFS_GLOBAL','proxy','2026-09-15 12:00:00',0);
        CREATE TABLE awos_observations(id INTEGER PRIMARY KEY,rain REAL);
        INSERT INTO awos_observations VALUES(1,NULL);
        """)
        conn.commit()
    candidate = tmp_path / "candidate.db"
    shutil.copyfile(source, candidate)
    validation = "wawp:validation/test"
    archive = archive_incrementally(candidate, validation, tmp_path / "stage")
    checkpoint = pack(candidate, tmp_path / "validation/test/operational.db.zst")
    replacements = {
        "BASELINE_BYTES": source.stat().st_size, "BASELINE_SHA": file_digest(source),
        "VALIDATION": validation, "SEGMENT_NAME": archive["archive_name"],
        "SEGMENT_SHA": archive["archive_sha256"], "CANDIDATE_SHA": checkpoint["database_sha256"],
        "CANDIDATE_ARCHIVE_SHA": checkpoint["archive_sha256"],
        "EXPECTED_ROWS": {"qm_corrections_applied": 1, "meteologix_forecasts": 1},
    }
    for name, value in replacements.items():
        monkeypatch.setattr(cutover, name, value)
    output = tmp_path / "wawp_forecasts-2-1.db.zst"
    report = cutover.promote(source, output, tmp_path / "promotion")
    assert report["status"] == "passed" and report["receipt_only_update"]
    assert (tmp_path / "incremental" / archive["archive_name"]).is_file()
    assert (tmp_path / "checkpoints" / output.name).is_file()
    recovered = tmp_path / "final.db"
    restore(output, recovered)
    with closing(sqlite3.connect(recovered)) as conn:
        assert conn.execute("SELECT remote_path FROM wawp_archive_segments").fetchone()[0] == f"wawp:incremental/{archive['archive_name']}"
    assert file_digest(source) == replacements["BASELINE_SHA"]
