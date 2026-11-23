import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src import storage_incremental_archive as archive
from src.storage_snapshot import file_digest, restore


def make_database(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
        CREATE TABLE qm_corrections_applied (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_run_id TEXT NOT NULL,
            value REAL, UNIQUE(pipeline_run_id, value)
        );
        CREATE TABLE meteologix_forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL, model TEXT NOT NULL,
            run_init_utc TEXT NOT NULL, visibility REAL
        );
        CREATE INDEX idx_legacy ON meteologix_forecasts(location, model, run_init_utc);
        CREATE TABLE qm_cdfs (id INTEGER PRIMARY KEY, payload BLOB);
        CREATE TABLE openmeteo_forecasts (
            id INTEGER PRIMARY KEY, model TEXT, run_init_utc TEXT,
            scraped_at TEXT, lead_hours REAL
        );
        CREATE TABLE awos_observations (id INTEGER PRIMARY KEY, rainfall REAL);
        CREATE TABLE qm_training_pairs (temperature REAL);
        INSERT INTO qm_corrections_applied(pipeline_run_id,value)
            VALUES ('2026-11-22 12:00:00', NULL), ('2026-11-23 00:00:00', 1.5);
        INSERT INTO meteologix_forecasts(location,model,run_init_utc,visibility)
            VALUES ('WAWP','ECMWF_HRES','2026-11-22 06:00:00',NULL),
                   ('WAWP','ECMWF_HRES','2026-11-23 00:00:00',9000),
                   ('WAWP','GFS_GLOBAL','2026-11-22 06:00:00',2500),
                   ('other','ECMWF_HRES','2026-11-22 06:00:00',3000);
        INSERT INTO qm_cdfs VALUES (1,X'0001');
        INSERT INTO openmeteo_forecasts VALUES
            (1,'ECMWF_HRES','proxy','2026-11-23 00:00:00',0);
        INSERT INTO awos_observations VALUES (1,0.0),(2,NULL);
        INSERT INTO qm_training_pairs VALUES (29.5),(30.0);
        """)
        conn.commit()


def rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def test_archive_two_cycles_and_retry(tmp_path):
    db, remote, work = tmp_path / "operational.db", tmp_path / "remote", tmp_path / "work"
    make_database(db)
    archive_tables = archive.RETIRED_TABLES
    before = {t: rows(db, t) for t in archive_tables}
    protected = {t: rows(db, t) for t in (
        "qm_cdfs", "openmeteo_forecasts", "awos_observations", "qm_training_pairs"
    )}
    report = archive.archive_incrementally(db, str(remote), work)
    assert report["status"] == "archived"
    assert report["retired"][archive.LEDGER]["rows"] == 1
    assert report["retired"][archive.LEGACY]["rows"] == 1
    restored = tmp_path / "archive.db"
    restore(remote / report["archive_name"], restored)
    for table in archive_tables:
        assert sorted(rows(restored, table) + rows(db, table)) == before[table]
    for table, original in protected.items():
        assert rows(db, table) == original
    assert len(rows(db, "wawp_archive_segments")) == 1
    file_count = len(list(remote.iterdir()))
    assert archive.archive_incrementally(db, str(remote), work)["status"] == "no_new_history"
    assert len(list(remote.iterdir())) == file_count

    # New issuance with same model/location; only newly closed rows move.
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO qm_corrections_applied(pipeline_run_id,value) VALUES (?,?)",
                     ("2026-11-23 06:00:00", 2.0))
        conn.execute("INSERT INTO meteologix_forecasts(location,model,run_init_utc,visibility) VALUES (?,?,?,?)",
                     ("WAWP", "ECMWF_HRES", "2026-11-23 06:00:00", 4000))
        conn.execute("UPDATE openmeteo_forecasts SET scraped_at='2026-11-23 06:00:00'")
        conn.commit()
    result = archive.archive_incrementally(db, str(remote), work)
    assert result["retired"][archive.LEDGER]["rows"] == 1
    assert result["retired"][archive.LEGACY]["rows"] == 1
    assert rows(db, archive.LEDGER)[0][0] == 3
    assert len(rows(db, "wawp_archive_segments")) == 2
    assert len(list(remote.iterdir())) == file_count + 2


def test_remote_readback_corruption_never_evicts(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    make_database(source)
    before = file_digest(source)
    real_copy = archive.rclone_copy

    def corrupt_download(src, dst):
        real_copy(src, dst)
        if "/readback/" in dst and dst.endswith(".db.zst"):
            with Path(dst).open("ab") as stream:
                stream.write(b"corrupt")

    monkeypatch.setattr(archive, "rclone_copy", corrupt_download)
    with pytest.raises(ValueError, match="Remote archive payload differs"):
        archive.archive_incrementally(source, str(tmp_path / "remote"), tmp_path / "work")
    assert file_digest(source) == before
    assert not list((tmp_path / "remote").glob("*.json"))
    # Retry against the existing immutable data object after the interrupted
    # upload; the identical input produces the same segment identifier.
    data_objects = list((tmp_path / "remote").glob("*.db.zst"))
    assert len(data_objects) == 1
    monkeypatch.setattr(archive, "rclone_copy", real_copy)
    result = archive.archive_incrementally(source, str(tmp_path / "remote"), tmp_path / "work")
    assert result["archive_name"] == data_objects[0].name
    assert len(list((tmp_path / "remote").iterdir())) == 2


def test_source_changes_before_eviction_never_delete(tmp_path):
    source = tmp_path / "source.db"
    make_database(source)
    staged, manifest = archive.stage_delta(source, tmp_path)
    verified = tmp_path / "verified.db"
    restore(staged, verified)
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("UPDATE qm_corrections_applied SET value=3.7 WHERE id=1")
        conn.commit()
    before = file_digest(source)
    with pytest.raises(ValueError, match="Source changed"):
        archive.evict_verified(source, verified, manifest, "test:archive")
    assert file_digest(source) == before
    assert len(rows(source, archive.LEDGER)) == 2


def test_fallback_run_remains_in_hot_ledger(tmp_path):
    source = tmp_path / "source.db"
    make_database(source)
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("UPDATE openmeteo_forecasts SET scraped_at='2026-11-22 12:00:00'")
        conn.commit()
    result = archive.archive_incrementally(source, str(tmp_path / "remote"), tmp_path / "work")
    assert result["retired"][archive.LEDGER]["rows"] == 0
    assert len(rows(source, archive.LEDGER)) == 2


def test_retirement_rejects_new_cascading_dependency(tmp_path):
    source = tmp_path / "source.db"
    make_database(source)
    with closing(sqlite3.connect(source)) as conn:
        conn.execute(
            "CREATE TRIGGER delete_observations AFTER DELETE ON qm_corrections_applied "
            "BEGIN DELETE FROM awos_observations; END"
        )
        conn.commit()
    before = file_digest(source)
    with pytest.raises(ValueError, match="archive-table triggers"):
        archive.archive_incrementally(source, str(tmp_path / "remote"), tmp_path / "work")
    assert file_digest(source) == before
    assert not (tmp_path / "remote").exists()
