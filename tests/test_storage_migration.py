import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from src.storage_ledger_split import split
from src.storage_snapshot import manifest_path, pack, restore, select_asset, verify_asset


def test_compression_roundtrip_and_tampering(tmp_path):
    database = tmp_path / "original.db"
    archive = tmp_path / "snapshot.db.zst"
    recovered = tmp_path / "recovered.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("CREATE TABLE data (id INTEGER, value REAL, note TEXT)")
        conn.executemany("INSERT INTO data VALUES (?,?,?)", [(1, 0.1, "UTC"), (2, None, None)])
        conn.commit()
    report = pack(database, archive)
    assert report["database_sha256"] == hashlib.sha256(database.read_bytes()).hexdigest()
    restore(archive, recovered)
    assert recovered.read_bytes() == database.read_bytes()
    with archive.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="manifest"):
        restore(archive, tmp_path / "bad.db")
    assert not (tmp_path / "bad.db").exists()
    assert manifest_path(archive).exists()


def test_open_wal_is_rejected(tmp_path):
    database = tmp_path / "active.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE data (value REAL)")
        conn.commit()
        with pytest.raises(ValueError, match="close its writer"):
            pack(database, tmp_path / "snapshot.db.zst")


def test_release_choice_excludes_incomplete_uploads(tmp_path):
    names = [
        "wawp_forecasts-1-1.db", "wawp_forecasts-2-1.db.zst",
        "wawp_forecasts-2-1.db.zst.json", "wawp_forecasts-3-1.db.zst",
        "wawp_forecasts-4-1.db.zst", "wawp_forecasts-4-1.db.zst.json",
    ]
    assets = [
        {"name": name, "id": index, "created_at": f"2026-09-{index + 1:02}T00:00:00Z",
         "size": 1, "state": "uploaded"}
        for index, name in enumerate(names)
    ]
    assets[-1]["state"] = "starter"
    assert select_asset([assets[:3], assets[3:]]) == names[1]
    assert select_asset([[assets[0], assets[3]]]) == names[0]
    assert select_asset([[assets[3]]]) == ""
    local = tmp_path / names[1]
    local.write_bytes(b"a")
    assets[1]["digest"] = "sha256:" + hashlib.sha256(b"a").hexdigest()
    verify_asset([assets], local)
    local.write_bytes(b"b")
    with pytest.raises(ValueError, match="digest"):
        verify_asset([assets], local)


def test_ledger_split_preserves_every_row_and_sequence(tmp_path):
    source = tmp_path / "source.db"
    with closing(sqlite3.connect(source)) as conn:
        conn.executescript("""
            CREATE TABLE qm_corrections_applied (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_run_id TEXT NOT NULL, value REAL,
                UNIQUE(pipeline_run_id,value)
            );
            CREATE TABLE openmeteo_forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at TEXT, run_init_utc TEXT, lead_hours REAL
            );
            CREATE TABLE awos_observations (temperature REAL, rain REAL);
            CREATE TABLE qm_training_pairs (valid_time TEXT, obs REAL, fcst REAL);
            INSERT INTO qm_training_pairs VALUES ('a', 1, 1), ('b', NULL, 2);
            CREATE TABLE qm_cdfs (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT);
            INSERT INTO qm_cdfs(payload) VALUES ('coefficients');
            CREATE TABLE meteologix_forecasts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              location TEXT, model TEXT, run_init_utc TEXT, visibility REAL
            );
            INSERT INTO meteologix_forecasts(location,model,run_init_utc,visibility)
              VALUES ('WAWP','A','1',1200),('WAWP','A','2',NULL),
                     ('WAWP','A','2',9000),('WAWP','B','1',1000),
                     ('other','A','1',3000);
            CREATE INDEX idx_awos ON awos_observations(temperature);
            INSERT INTO openmeteo_forecasts(scraped_at,run_init_utc,lead_hours)
                VALUES ('new', 'proxy', 2);
            INSERT INTO qm_corrections_applied(id,pipeline_run_id,value) VALUES
                (1,'old',NULL), (2,'new',1.1), (3,'old',0.0), (4,'old',2.2);
            DELETE FROM qm_corrections_applied WHERE id=4;
            INSERT INTO sqlite_sequence VALUES ('qm_corrections_applied',8);
            INSERT INTO awos_observations VALUES (28.5,NULL),(28.5,NULL),(29.5,0);
        """)
        conn.commit()
    original = source.read_bytes()
    output = tmp_path / "output"
    report = split(source, output, archive_legacy=True, archive_training=True)
    assert source.read_bytes() == original
    assert report["ledger"]["operational"]["rows"] == 1
    assert report["ledger"]["archive"]["rows"] == 2
    assert report["tables"]["awos_observations"]["rows"] == 3
    assert report["legacy_forecasts"]["operational"]["rows"] == 4
    assert report["legacy_forecasts"]["archive"]["rows"] == 1
    assert report["training_pairs"]["archive"]["rows"] == 2
    assert report["training_pairs"]["operational"]["rows"] == 0
    with closing(sqlite3.connect(output / "operational.db")) as hot:
        assert hot.execute("SELECT count(*) FROM qm_training_pairs").fetchone() == (0,)
        assert hot.execute("SELECT payload FROM qm_cdfs").fetchone() == ("coefficients",)
        hot.execute("INSERT INTO qm_corrections_applied(pipeline_run_id,value) VALUES ('next',4)")
        assert hot.execute("SELECT MAX(id) FROM qm_corrections_applied").fetchone() == (9,)
    with closing(sqlite3.connect(output / "historical_tables.db")) as cold:
        assert cold.execute("SELECT id,value FROM qm_corrections_applied ORDER BY id").fetchall() == [(1,None),(3,0.0)]
        assert cold.execute("SELECT id,visibility FROM meteologix_forecasts").fetchall() == [(1,1200.0)]
        assert cold.execute("SELECT * FROM qm_training_pairs ORDER BY rowid").fetchall() == [
            ("a",1.0,1.0), ("b",None,2.0)
        ]
        assert cold.execute("SELECT payload FROM qm_cdfs").fetchone() == ("coefficients",)
    assert json.loads((output / "manifest.json").read_text()) == report
