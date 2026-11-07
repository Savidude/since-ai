from __future__ import annotations

import json

import pandas as pd

from device_simulator.predictors import available_predictors, create_predictor
from device_simulator.predictors.polar_mind import model
from device_simulator.replay import ReplayMinute


def test_predictor_registry_exposes_polar_mind_and_none() -> None:
    assert available_predictors() == ("none", "polar-mind")
    assert create_predictor("none", data_dir="unused", asset="PUMP-06") is None


def test_polar_mind_adapter_loads_local_asset_metadata_and_streams(tmp_path) -> None:
    pd.DataFrame(
        [{"asset_id": "PUMP-06", "motor_rated_rpm": 1800.0}]
    ).to_csv(tmp_path / "assets.csv", index=False)
    predictor = create_predictor(
        "polar-mind",
        data_dir=tmp_path,
        asset="PUMP-06",
    )
    assert predictor is not None

    result = predictor.process_minute(
        ReplayMinute(
            timestamp=pd.Timestamp("2025-10-09T00:00:00Z"),
            telemetry={},
            features={
                "window_start_utc": pd.Timestamp("2025-10-09T00:00:00Z"),
                "rows": 60,
                "operating_mode": "RUNNING",
                "running_fraction": 1.0,
                "motor_speed_rpm__mean": 1800.0,
            },
            is_warmup=False,
        )
    )

    assert result is not None
    assert result["phase"] == "learning_normal"
    assert result["fault_type"] is None


def test_polar_mind_stream_holds_alerts_until_learning_period_completes(
    monkeypatch,
) -> None:
    calls = []

    def fake_on_minute(minute, asset, history):
        calls.append((minute, asset, history))
        return [
            {
                "event_class": "MACHINE",
                "fault_type": "TEST_ALERT",
                "confidence": 0.9,
                "evidence": "test evidence",
                "recommended_action": "test action",
            }
        ]

    monkeypatch.setattr(model, "on_minute", fake_on_minute)
    stream = model.PolarMindStream({"asset_id": "PUMP-06"})

    learning = json.loads(
        stream.update({"window_start_utc": "2025-10-09T00:00:00Z"})
    )
    watching = json.loads(
        stream.update({"window_start_utc": "2025-10-10T00:00:00Z"})
    )

    assert learning["phase"] == "learning_normal"
    assert learning["fault_type"] is None
    assert watching["phase"] == "alert"
    assert watching["fault_type"] == "TEST_ALERT"
    assert len(calls) == 1
    assert len(calls[0][2]) == 1
