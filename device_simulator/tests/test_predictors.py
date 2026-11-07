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


def test_polar_mind_adapter_batches_history_before_scoring_replay_minutes(
    tmp_path, monkeypatch
) -> None:
    pd.DataFrame(
        [{"asset_id": "PUMP-06", "motor_rated_rpm": 1800.0}]
    ).to_csv(tmp_path / "assets.csv", index=False)
    history_seen = []

    def fake_on_minute(minute, asset, history):
        history_seen.append(history.copy())
        return []

    monkeypatch.setattr(model, "on_minute", fake_on_minute)
    predictor = create_predictor(
        "polar-mind",
        data_dir=tmp_path,
        asset="PUMP-06",
    )
    assert predictor is not None

    history_timestamps = [
        pd.Timestamp("2025-10-08T00:00:00Z"),
        pd.Timestamp("2025-10-08T01:00:00Z"),
        pd.Timestamp("2025-10-09T00:00:00Z"),
    ]
    for timestamp in history_timestamps:
        assert predictor.process_minute(
            ReplayMinute(
                timestamp=timestamp,
                telemetry={},
                features={"window_start_utc": timestamp, "rows": 60},
                is_warmup=True,
            )
        ) is None

    assert history_seen == []

    replay_timestamp = pd.Timestamp("2025-10-09T00:01:00Z")
    result = predictor.process_minute(
        ReplayMinute(
            timestamp=replay_timestamp,
            telemetry={},
            features={"window_start_utc": replay_timestamp, "rows": 60},
            is_warmup=False,
        )
    )

    assert result is not None
    assert result["phase"] == "watching"
    assert len(history_seen) == 1
    assert history_seen[0]["window_start_utc"].tolist() == history_timestamps


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


def test_drift_confidence_uses_the_window_median() -> None:
    row = pd.Series(
        {
            "drift_ch": "i",
            "drift_med": 8.25,
            "z_i": 5.5,
        }
    )

    assert model.signature_confidence("drift", row) == 0.75


def test_mark_drift_retains_the_triggering_channel_after_it_clears() -> None:
    minute_count = 1000
    z_i = [0.0] * 180 + [4.0] * 360 + [6.0] * (minute_count - 540)
    frame = pd.DataFrame(
        {
            "asset_id": ["PUMP-06"] * minute_count,
            "window_start_utc": pd.date_range(
                "2025-10-08T00:00:00Z", periods=minute_count, freq="min"
            ),
            "steady": [True] * minute_count,
            "z_i": z_i,
            "z_pw": [0.0] * minute_count,
            "z_pd": [0.0] * minute_count,
            "z_q": [0.0] * minute_count,
            "z_n": [0.0] * minute_count,
        }
    )

    marked = model.mark_drift(frame)

    assert marked["drift_ch"].eq("i").any()
    assert marked.iloc[-1]["drift_ch"] == ""
    assert marked.iloc[-1]["drift_hold_ch"] == "i"
    assert marked.iloc[-1]["drift_hold_med"] == 6.0


def test_misalignment_evidence_uses_readings_and_spectrum_ratio() -> None:
    evidence = model.evidence_text(
        "mis",
        pd.Series(
            {
                "z_v": 7.0,
                "vibration_rms_mm_s__mean": 8.2,
                "ratio_21": 1.4,
            }
        ),
        pd.Series(dtype=object),
    )

    assert evidence == (
        "vibration_rms_mm_s (8.2 mm/s); 2x/1x vibration (1.40)"
    )
