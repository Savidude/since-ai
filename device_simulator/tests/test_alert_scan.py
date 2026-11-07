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


def test_all_assets_scan_writes_combined_alerts_in_parallel(
    tmp_path, monkeypatch
) -> None:
    class FakePredictor:
        def __init__(self, asset: str) -> None:
            self.asset = asset

        def process_minute(self, minute: ReplayMinute) -> dict[str, object]:
            return {
                "fault_type": f"{self.asset}_FAULT",
                "phase": "alert",
            }

    class FakeProcessPoolExecutor:
        worker_counts: list[int] = []

        def __init__(self, *, max_workers: int) -> None:
            self.worker_counts.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def map(self, function, assets):
            return [function(asset) for asset in assets]

    timestamp = pd.Timestamp("2025-10-08T13:50:00Z")

    def fake_iter_replay_minutes(
        data_dir, asset, start, end, limit, *, warmup_hours, history_start
    ):
        yield ReplayMinute(
            timestamp=timestamp,
            telemetry={},
            features={},
            is_warmup=False,
        )

    monkeypatch.setattr(
        alert_scan, "list_telemetry_assets", lambda data_dir: ("PUMP-05", "PUMP-06")
    )
    monkeypatch.setattr(
        alert_scan,
        "ProcessPoolExecutor",
        FakeProcessPoolExecutor,
    )
    monkeypatch.setattr(alert_scan, "iter_replay_minutes", fake_iter_replay_minutes)
    monkeypatch.setattr(
        alert_scan,
        "create_predictor",
        lambda name, *, data_dir, asset: FakePredictor(asset),
    )
    output_path = tmp_path / "alerts.json"

    result = alert_scan.main(
        [
            "--data",
            str(tmp_path),
            "--all-assets",
            "--workers",
            "2",
            "--start",
            "2025-10-08T13:50:00Z",
            "--output",
            str(output_path),
        ]
    )

    alerts = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert FakeProcessPoolExecutor.worker_counts == [2]
    assert alerts == [
        {
            "fault_type": "PUMP-05_FAULT",
            "phase": "alert",
            "asset_id": "PUMP-05",
            "minute_utc": "2025-10-08T13:50:00Z",
            "alert_time_utc": "2025-10-08T13:51:00Z",
        },
        {
            "fault_type": "PUMP-06_FAULT",
            "phase": "alert",
            "asset_id": "PUMP-06",
            "minute_utc": "2025-10-08T13:50:00Z",
            "alert_time_utc": "2025-10-08T13:51:00Z",
        },
    ]
