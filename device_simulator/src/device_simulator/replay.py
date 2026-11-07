from __future__ import annotations

import math
import os
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

DEFAULT_START = "2025-10-09T00:00:00Z"


def _utc_timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError(f"Invalid timestamp: {value!r}")
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _load(
    data_dir: str | os.PathLike[str],
    asset: str,
    start: str | None = DEFAULT_START,
    end: str | None = None,
) -> pd.DataFrame:
    data_path = Path(data_dir)
    telemetry_dir = data_path / "telemetry" / f"asset_id={asset}"
    if telemetry_dir.is_dir():
        df = pd.read_parquet(telemetry_dir)
    else:
        csv_path = data_path / "telemetry.csv.gz"
        if not csv_path.is_file():
            raise ValueError(f"No telemetry data found for asset {asset!r} in {data_dir!s}.")
        df = pd.read_csv(csv_path)
        if "asset_id" not in df.columns:
            raise ValueError(f"Telemetry CSV {csv_path} is missing the 'asset_id' column.")
        df = df[df["asset_id"] == asset]

    if "timestamp_utc" not in df.columns:
        raise ValueError("Telemetry data is missing the 'timestamp_utc' column.")
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    if start:
        df = df[df["timestamp_utc"] >= _utc_timestamp(start)]
    if end:
        df = df[df["timestamp_utc"] < _utc_timestamp(end)]
    return df.sort_values("timestamp_utc")


def iter_replay_payloads(
    data_dir: str | os.PathLike[str],
    asset: str,
    start: str | None = DEFAULT_START,
    end: str | None = None,
    limit: int | None = None,
) -> Iterator[dict[str, object]]:
    df = _load(data_dir, asset, start, end)
    if limit:
        df = df.head(int(limit))

    columns = [column for column in df.columns if column not in ("asset_id", "day")]
    for record in df[columns].itertuples(index=False):
        payload: dict[str, object] = {}
        for column, value in zip(columns, record):
            if column == "timestamp_utc":
                payload[column] = value.isoformat()
                continue
            if hasattr(value, "item"):
                value = value.item()
            if isinstance(value, float):
                if math.isnan(value):
                    continue
                payload[column] = float(f"{value:.6g}")
            else:
                payload[column] = value if isinstance(value, (int, str)) else str(value)
        yield payload
