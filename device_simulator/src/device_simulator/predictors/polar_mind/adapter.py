from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from ...replay import ReplayMinute
from .model import PolarMindStream, attach_spectrum, load_spectrum


class PolarMindAdapter:
    def __init__(self, data_dir: Path, asset: str) -> None:
        assets_path = data_dir / "assets.csv"
        if not assets_path.is_file():
            raise ValueError(f"Asset metadata not found: {assets_path}")

        assets = pd.read_csv(assets_path)
        if "asset_id" not in assets.columns:
            raise ValueError(f"Asset metadata {assets_path} is missing the 'asset_id' column.")
        matches = assets[assets["asset_id"] == asset]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one asset row for {asset!r} in {assets_path}; found {len(matches)}."
            )

        self._asset_id = asset
        self._stream = PolarMindStream(matches.iloc[0])
        self._spectrum = load_spectrum(str(data_dir))

    def process_minute(
        self, minute: ReplayMinute
    ) -> Mapping[str, object] | None:
        row = dict(minute.features)
        row["asset_id"] = self._asset_id
        enriched = attach_spectrum(pd.DataFrame([row]), self._spectrum)
        prediction = json.loads(self._stream.update(enriched.iloc[0].to_dict()))
        if prediction["fault_type"] is not None:
            minute = pd.Timestamp(row["window_start_utc"])
            if minute.tzinfo is None:
                minute = minute.tz_localize("UTC")
            else:
                minute = minute.tz_convert("UTC")
            prediction["alert_time_utc"] = (minute + pd.Timedelta(minutes=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        return prediction
