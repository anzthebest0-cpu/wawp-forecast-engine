"""
Shared parser for WAWP hourly AWOS .dat files.

The hourly files contain two wind direction/speed pairs before the final
rain column:

STN Date Hour QFE QFF Temp Dewp RH WD WS WD WS RA

Older code accidentally used the second WD column as rain. Keep this parser as
the single source of truth for hourly AWOS column layout and unit scaling.
"""
from __future__ import annotations

import pandas as pd


HOURLY_COLUMNS = ["Date", "Hour", "QFE", "QFF", "Temp", "Dewp", "RH", "WD", "WS", "Rain"]


def read_hourly_awos(path: str, encoding: str = "utf-8") -> pd.DataFrame:
    raw = pd.read_csv(
        path,
        sep=r"\s+",
        skiprows=4,
        header=None,
        encoding=encoding,
        na_values=["////", "/////", "///", "//"],
    )

    if raw.empty:
        return pd.DataFrame(columns=HOURLY_COLUMNS + ["UTC"])

    if raw.shape[1] >= 13:
        # 0=station, 1=date, 2=hour, 3=QFE, 4=QFF, 5=temp, 6=dew,
        # 7=RH, 8=WD, 9=WS, 10=secondary WD, 11=secondary WS, 12=rain.
        usecols = [1, 2, 3, 4, 5, 6, 7, 8, 9, 12]
    elif raw.shape[1] >= 11:
        # Compatibility for older exports that only had one wind pair.
        usecols = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    else:
        raise ValueError(f"Unexpected AWOS hourly column count {raw.shape[1]} in {path}")

    df = raw.iloc[:, usecols].copy()
    df.columns = HOURLY_COLUMNS

    for col in ["QFE", "QFF", "Temp", "Dewp", "Rain"]:
        df[col] = pd.to_numeric(df[col], errors="coerce") / 10.0
    for col in ["RH", "WD", "WS"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    date_str = pd.to_numeric(df["Date"], errors="coerce").astype("Int64").astype(str)
    hour_str = pd.to_numeric(df["Hour"], errors="coerce").astype("Int64").astype(str).str.zfill(2)
    df["UTC"] = pd.to_datetime(
        date_str + hour_str,
        format="%Y%m%d%H",
        errors="coerce",
    )
    return df.dropna(subset=["UTC"])
