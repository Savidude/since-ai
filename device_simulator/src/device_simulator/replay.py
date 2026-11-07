from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_START = "2025-10-09T00:00:00Z"


@dataclass(frozen=True)
class ReplayMinute:
    timestamp: pd.Timestamp
    telemetry: dict[str, object]
    features: dict[str, object]
    is_warmup: bool


def _utc_timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError(f"Invalid timestamp: {value!r}")
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def list_telemetry_assets(data_dir: str | os.PathLike[str]) -> tuple[str, ...]:
    data_path = Path(data_dir)
    assets: set[str] = set()

    telemetry_dir = data_path / "telemetry"
    if telemetry_dir.is_dir():
        for partition_dir in telemetry_dir.glob("asset_id=*"):
            if partition_dir.is_dir():
                asset = partition_dir.name.partition("=")[2]
                if asset:
                    assets.add(asset)

    csv_path = data_path / "telemetry.csv.gz"
    if csv_path.is_file():
        columns = pd.read_csv(csv_path, nrows=0).columns
        if "asset_id" not in columns:
            raise ValueError(f"Telemetry CSV {csv_path} is missing the 'asset_id' column.")
        csv_assets = pd.read_csv(csv_path, usecols=["asset_id"])["asset_id"]
        assets.update(str(asset) for asset in csv_assets.dropna() if str(asset))

    if not assets:
        raise ValueError(f"No telemetry assets found in {data_path!s}.")
    return tuple(sorted(assets))


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


def _plain_value(value: object, *, round_float: bool = False) -> object:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and round_float:
        return float(f"{value:.6g}")
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _mode(values: pd.Series, default: str) -> object:
    present = values.dropna()
    if present.empty:
        return default
    modes = present.mode()
    return modes.iloc[0] if not modes.empty else present.iloc[0]


def _quality_flags(group: pd.DataFrame) -> int:
    if "quality_flags" not in group.columns:
        return 0
    values = pd.to_numeric(group["quality_flags"], errors="coerce").dropna()
    result = 0
    for value in values:
        result |= int(value)
    return result


def _aggregate_minute(
    group: pd.DataFrame,
    *,
    asset: str,
    minute_start: pd.Timestamp,
    is_warmup: bool,
) -> ReplayMinute:
    telemetry: dict[str, object] = {"timestamp_utc": minute_start.isoformat()}
    features: dict[str, object] = {
        "asset_id": asset,
        "window_start_utc": minute_start,
        "rows": int(len(group)),
    }

    metadata = {"asset_id", "day", "timestamp_utc", "quality_flags"}
    numeric_columns = [
        column
        for column in group.select_dtypes(include="number").columns
        if column not in metadata
    ]
    for column in numeric_columns:
        values = pd.to_numeric(group[column], errors="coerce")
        stats = values.agg(["mean", "std", "min", "max", "count"])
        for statistic in ("mean", "std", "min", "max", "count"):
            features[f"{column}__{statistic}"] = _plain_value(stats[statistic])
        mean = _plain_value(stats["mean"], round_float=True)
        if mean is not None:
            telemetry[column] = mean

    if "operating_mode" in group.columns:
        mode = _mode(group["operating_mode"], "NO_DATA")
        features["operating_mode"] = str(mode)
        features["running_fraction"] = float(
            (group["operating_mode"] == "RUNNING").fillna(False).mean()
        )
        telemetry["operating_mode"] = str(mode)
    else:
        features["operating_mode"] = "RUNNING"
        features["running_fraction"] = 1.0

    quality_flags = _quality_flags(group)
    features["quality_any"] = quality_flags
    if "quality_flags" in group.columns:
        telemetry["quality_flags"] = quality_flags

    for column in group.columns:
        if column in metadata or column in numeric_columns or column == "operating_mode":
            continue
        value = _plain_value(_mode(group[column], ""))
        if value is not None:
            telemetry[column] = value

    return ReplayMinute(
        timestamp=minute_start,
        telemetry=telemetry,
        features=features,
        is_warmup=is_warmup,
    )


def iter_replay_minutes(
    data_dir: str | os.PathLike[str],
    asset: str,
    start: str | None = DEFAULT_START,
    end: str | None = None,
    limit: int | None = None,
    warmup_hours: int = 0,
    history_start: str | None = None,
) -> Iterator[ReplayMinute]:
    if warmup_hours < 0:
        raise ValueError("warmup_hours must be non-negative.")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative.")

    replay_start = _utc_timestamp(start) if start else None
    replay_minute = replay_start.floor("min") if replay_start is not None else None
    if (warmup_hours or history_start is not None) and replay_minute is None:
        raise ValueError("A replay start timestamp is required when loading model history.")

    load_start = start
    if history_start is not None:
        if replay_minute is None:
            raise ValueError("A replay start timestamp is required when loading model history.")
        history_timestamp = _utc_timestamp(history_start)
        if history_timestamp > replay_minute:
            raise ValueError("Model history start must be at or before the replay start.")
        load_start = history_timestamp.isoformat()
    elif warmup_hours and replay_minute is not None:
        load_start = (replay_minute - pd.Timedelta(hours=warmup_hours)).isoformat()
    df = _load(data_dir, asset, load_start, end)

    if replay_minute is not None and (warmup_hours or history_start is not None):
        warmup_df = df[df["timestamp_utc"] < replay_minute]
        replay_df = df[df["timestamp_utc"] >= replay_start]
    else:
        warmup_df = df.iloc[0:0]
        replay_df = df

    for is_warmup, frame in ((True, warmup_df), (False, replay_df)):
        if frame.empty:
            continue
        replay_count = 0
        grouped = frame.groupby(frame["timestamp_utc"].dt.floor("min"), sort=True)
        for minute_start, group in grouped:
            if not is_warmup:
                if limit is not None and replay_count >= limit:
                    return
                replay_count += 1
            yield _aggregate_minute(
                group,
                asset=asset,
                minute_start=minute_start,
                is_warmup=is_warmup,
            )


def iter_replay_payloads(
    data_dir: str | os.PathLike[str],
    asset: str,
    start: str | None = DEFAULT_START,
    end: str | None = None,
    limit: int | None = None,
) -> Iterator[dict[str, object]]:
    for minute in iter_replay_minutes(data_dir, asset, start, end, limit):
        yield minute.telemetry
