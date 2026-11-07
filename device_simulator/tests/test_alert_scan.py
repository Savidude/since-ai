from __future__ import annotations

import json

import pandas as pd

from device_simulator import alert_scan
from device_simulator.replay import ReplayMinute


def test_offline_alert_scan_writes_alerts_without_publishing(
    tmp_path, monkeypatch
) -> None:
    warmup_minute = ReplayMinute(
        timestamp=pd.Timestamp("2025-10-08T13:49:00Z"),
        telemetry={},
        features={},
        is_warmup=True,
    )
    quiet_minute = ReplayMinute(
        timestamp=pd.Timestamp("2025-10-08T13:50:00Z"),
        telemetry={},
        features={},
        is_warmup=False,
    )
    alert_minute = ReplayMinute(
        timestamp=pd.Timestamp("2025-10-08T13:51:00Z"),
        telemetry={},
        features={},
        is_warmup=False,
    )
    iterator_arguments = {}

    def fake_iter_replay_minutes(
        data_dir, asset, start, end, limit, *, warmup_hours, history_start
    ):
        iterator_arguments.update(
            data_dir=data_dir,
            asset=asset,
            start=start,
            end=end,
            limit=limit,
            warmup_hours=warmup_hours,
            history_start=history_start,
        )
        yield warmup_minute
        yield quiet_minute
        yield alert_minute

    class FakePredictor:
        def __init__(self) -> None:
            self.processed: list[ReplayMinute] = []

        def process_minute(self, minute: ReplayMinute) -> dict[str, object]:
            self.processed.append(minute)
            if minute is warmup_minute:
                return {
                    "asset_id": "PUMP-06",
                    "phase": "alert",
                    "fault_type": "PRE_ROLL_ALERT",
                }
            if minute is alert_minute:
                return {
                    "asset_id": "PUMP-06",
                    "phase": "alert",
                    "fault_type": "TEST_ALERT",
                }
            return {"asset_id": "PUMP-06", "phase": "watching", "fault_type": None}

    predictor = FakePredictor()
    monkeypatch.setattr(
        alert_scan, "iter_replay_minutes", fake_iter_replay_minutes
    )
    monkeypatch.setattr(
        alert_scan,
        "create_predictor",
        lambda name, *, data_dir, asset: predictor,
    )
    output_path = tmp_path / "alerts.json"

    result = alert_scan.main(
        [
            "--data",
            str(tmp_path),
            "--asset",
            "PUMP-06",
            "--start",
            "2025-10-08T13:50:00Z",
            "--model-history-start",
            "2025-10-07T13:50:00Z",
            "--limit",
            "2",
            "--output",
            str(output_path),
        ]
    )

    alerts = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert iterator_arguments == {
        "data_dir": str(tmp_path),
        "asset": "PUMP-06",
        "start": "2025-10-08T13:50:00Z",
        "end": None,
        "limit": 2,
        "warmup_hours": 24,
        "history_start": "2025-10-07T13:50:00Z",
    }
    assert predictor.processed == [warmup_minute, quiet_minute, alert_minute]
    assert alerts == [
        {
            "asset_id": "PUMP-06",
            "phase": "alert",
            "fault_type": "TEST_ALERT",
            "minute_utc": "2025-10-08T13:51:00Z",
            "alert_time_utc": "2025-10-08T13:52:00Z",
        }
    ]
