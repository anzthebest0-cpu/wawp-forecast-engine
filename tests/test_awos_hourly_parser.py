from pathlib import Path

from src.awos_hourly_parser import read_hourly_awos


def test_hourly_awos_uses_final_rain_column(tmp_path: Path):
    sample = tmp_path / "sample.dat"
    sample.write_text(
        "\n".join([
            "Hourly Report for WAWP",
            "STN YYYYMMDD GG QFE36 QFF36 TEMP36 DEWP36 RH36 WD36 WS36 WD36 WS36 RA36",
            "xxx 10 10 60 60",
            "hPa hPa degC degC % deg kt deg kt mm",
            "000 20260630 10 10078 10093 277 241 80 319 4 314 4 0",
            "000 20260630 11 10078 10093 277 241 80 319 4 314 4 12",
        ]),
        encoding="utf-8",
    )

    df = read_hourly_awos(str(sample))

    assert df.loc[0, "Rain"] == 0.0
    assert df.loc[1, "Rain"] == 1.2
    assert df.loc[0, "WD"] == 319
