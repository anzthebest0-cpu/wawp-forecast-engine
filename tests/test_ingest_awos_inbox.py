import sqlite3
from pathlib import Path

import pytest

from src.build_compact_operational_db import build_compact_operational_db
from src.awos_quality import AWOS_QUALITY_CONTRACT_VERSION
from src.ingest_awos_inbox import MANIFEST_TABLE, ingest_awos_inbox


def _write_hourly(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Hourly Report for WAWP",
                "STN YYYYMMDD GG QFE36 QFF36 TEMP36 DEWP36 RH36 WD36 WS36 WD36 WS36 RA36",
                "xxx 10 10 60 60",
                "hPa hPa degC degC % deg kt deg kt mm",
                "000 20260701 00 10078 10093 277 241 80 319 4 314 4 0",
                "000 20260701 01 10077 10092 276 240 80 320 5 315 5 12",
            ]
        ),
        encoding="utf-8",
    )


def _write_minute(path: Path, rows: list[str]) -> None:
    path.write_text(
        "\n".join(
            [
                "One Minute Report for WAWP",
                "header",
                "header",
                "header",
                *rows,
            ]
        ),
        encoding="utf-8",
    )


def _minute_row(minute: int, gust: int) -> str:
    return f"000 20260701 00 {minute:02d} 4 319 {gust} 314 277 241 80 10093 0 0 500"


def test_inbox_ingests_both_products_and_is_idempotent(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_hourly(inbox / "000HLY.202607.dat")
    _write_minute(
        inbox / "000OneMinute.20260701.dat",
        [_minute_row(0, 8), _minute_row(1, 14)],
    )
    db_path = tmp_path / "wawp.db"

    first = ingest_awos_inbox(inbox, db_path)
    second = ingest_awos_inbox(inbox, db_path)

    assert first["discovered_assets"] == 2
    assert first["applied_assets"] == 2
    assert first["hourly_gust_rows_updated"] == 2
    assert first["quality_state"]["metadata"]["quality_contract_version"] == AWOS_QUALITY_CONTRACT_VERSION
    assert first["quality_state"]["cross_grain"]["gust_match_hours"] == 2
    assert second["applied_assets"] == 0
    assert second["skipped_assets"] == 2

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM awos_observations").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM awos_observations_1min").fetchone()[0] == 2
        gust_00 = conn.execute(
            "SELECT wind_gust_max FROM awos_observations WHERE obs_time='2026-07-01 00:00:00'"
        ).fetchone()[0]
        gust_01 = conn.execute(
            "SELECT wind_gust_max FROM awos_observations WHERE obs_time='2026-07-01 01:00:00'"
        ).fetchone()[0]
        assert gust_00 == 8
        assert gust_01 == 14
        assert conn.execute(f"SELECT COUNT(*) FROM {MANIFEST_TABLE}").fetchone()[0] == 2


def test_compaction_keeps_manifest_and_derived_gust(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_hourly(inbox / "000HLY.202607.dat")
    _write_minute(inbox / "000OneMinute.20260701.dat", [_minute_row(0, 17)])
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    ingest_awos_inbox(inbox, source)

    report = build_compact_operational_db(source, candidate)

    assert report["valid"] is True
    assert report["raw_minute_source_rows"] == 1
    assert report["raw_minute_candidate_rows"] == 0
    with sqlite3.connect(candidate) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {MANIFEST_TABLE}").fetchone()[0] == 2
        assert conn.execute("SELECT MAX(wind_gust_max) FROM awos_observations").fetchone()[0] == 17


def test_missing_minute_wgs_remains_unknown_not_zero(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_hourly(inbox / "000HLY.202607.dat")
    _write_minute(
        inbox / "000OneMinute.20260701.dat",
        [_minute_row(0, 8).replace(" 8 314 ", " /// 314 ")],
    )
    db_path = tmp_path / "wawp.db"

    report = ingest_awos_inbox(inbox, db_path)

    assert report["minute_wind_gust_values"] == 0
    assert report["hourly_gust_rows_updated"] == 0
    assert report["hourly_gust_rows_cleared_missing_source"] >= 1
    with sqlite3.connect(db_path) as conn:
        values = conn.execute(
            "SELECT wind_gust_max FROM awos_observations ORDER BY obs_time"
        ).fetchall()
    assert values == [(None,), (None,)]


def test_inbox_rejects_unsupported_minute_filename(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_minute(inbox / "000OneMinute.20260701.dat", [_minute_row(0, 10)])
    duplicate = inbox / "000OneMinute.20260701.dat.copy.dat"
    duplicate.write_text((inbox / "000OneMinute.20260701.dat").read_text(), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported one-minute filename"):
        ingest_awos_inbox(inbox, tmp_path / "wawp.db")
