import json
from threading import Event
from typing import Any

import pandas as pd
import pytest

from device_simulator import publisher
from device_simulator.cli import _build_parser
from device_simulator.provisioning import Device
from device_simulator.replay import (
    DEFAULT_START,
    ReplayMinute,
    iter_replay_minutes,
    iter_replay_payloads,
)


class FakeIotDataClient:
    def __init__(self) -> None:
        self.publications: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.publications.append(kwargs)
        return {}


def _write_telemetry_csv(data_dir) -> None:
    pd.DataFrame(
        [
            {
                "asset_id": "PUMP-06",
                "day": "2025-10-09",
                "timestamp_utc": "2025-10-09T00:00:01Z",
                "temperature_c": 1.23456789,
                "sample_count": 2,
                "operating_mode": "RUNNING",
                "quality_flags": 1,
                "missing_value": float("nan"),
            },
            {
                "asset_id": "PUMP-06",
                "day": "2025-10-09",
                "timestamp_utc": "2025-10-09T00:00:00Z",
                "temperature_c": 2.0,
                "sample_count": 1,
                "operating_mode": "RUNNING",
                "quality_flags": 4,
                "missing_value": float("nan"),
            },
            {
                "asset_id": "PUMP-06",
                "day": "2025-10-09",
                "timestamp_utc": "2025-10-09T00:01:00Z",
                "temperature_c": 4.0,
                "sample_count": 5,
                "operating_mode": "RUNNING",
                "quality_flags": 0,
                "missing_value": float("nan"),
            },
            {
                "asset_id": "PUMP-06",
                "day": "2025-10-10",
                "timestamp_utc": "2025-10-10T00:00:00Z",
                "temperature_c": 3.0,
                "sample_count": 3,
                "missing_value": float("nan"),
            },
            {
                "asset_id": "PUMP-05",
                "day": "2025-10-09",
                "timestamp_utc": "2025-10-09T00:00:00Z",
                "temperature_c": 4.0,
                "sample_count": 4,
                "missing_value": float("nan"),
            },
        ]
    ).to_csv(data_dir / "telemetry.csv.gz", index=False, compression="gzip")


def test_replay_payloads_filter_sort_and_normalize_csv_rows(tmp_path) -> None:
    _write_telemetry_csv(tmp_path)

    payloads = list(
        iter_replay_payloads(
            tmp_path,
            "PUMP-06",
            start="2025-10-09T00:00:00Z",
            end="2025-10-10T00:00:00Z",
        )
    )

    assert [payload["timestamp_utc"] for payload in payloads] == [
        "2025-10-09T00:00:00+00:00",
        "2025-10-09T00:01:00+00:00",
    ]
    assert payloads[0]["temperature_c"] == 1.61728
    assert payloads[0]["sample_count"] == 1.5
    assert payloads[0]["quality_flags"] == 5
    assert payloads[0]["operating_mode"] == "RUNNING"
    assert "asset_id" not in payloads[0]
    assert "day" not in payloads[0]
    assert "missing_value" not in payloads[0]


def test_replay_minutes_include_warmup_features_and_limit_only_replay(
    tmp_path,
) -> None:
    _write_telemetry_csv(tmp_path)

    minutes = list(
        iter_replay_minutes(
            tmp_path,
            "PUMP-06",
            start="2025-10-09T00:01:00Z",
            end="2025-10-09T00:02:00Z",
            limit=1,
            warmup_hours=1,
        )
    )

    assert [minute.is_warmup for minute in minutes] == [True, False]
    assert minutes[0].telemetry["timestamp_utc"] == "2025-10-09T00:00:00+00:00"
    assert minutes[0].telemetry["temperature_c"] == 1.61728
    assert minutes[0].features["temperature_c__mean"] == pytest.approx(1.617283945)
    assert minutes[0].features["temperature_c__count"] == 2
    assert minutes[0].features["quality_any"] == 5
    assert minutes[0].features["operating_mode"] == "RUNNING"
    assert minutes[1].telemetry["timestamp_utc"] == "2025-10-09T00:01:00+00:00"


def test_replay_minutes_use_explicit_model_history_start(tmp_path) -> None:
    _write_telemetry_csv(tmp_path)

    minutes = list(
        iter_replay_minutes(
            tmp_path,
            "PUMP-06",
            start="2025-10-09T00:01:00Z",
            end="2025-10-09T00:02:00Z",
            history_start="2025-10-09T00:00:00Z",
        )
    )

    assert [minute.is_warmup for minute in minutes] == [True, False]
    assert [minute.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") for minute in minutes] == [
        "2025-10-09T00:00:00Z",
        "2025-10-09T00:01:00Z",
    ]


def test_replay_minutes_reject_model_history_after_replay_start(tmp_path) -> None:
    _write_telemetry_csv(tmp_path)

    with pytest.raises(ValueError, match="Model history start"):
        list(
            iter_replay_minutes(
                tmp_path,
                "PUMP-06",
                start="2025-10-09T00:01:00Z",
                history_start="2025-10-09T00:02:00Z",
            )
        )


def test_replay_payloads_load_partitioned_parquet_rows(tmp_path) -> None:
    parquet_dir = (
        tmp_path
        / "telemetry"
        / "asset_id=PUMP-06"
        / "day=2025-10-09"
    )
    parquet_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(
                ["2025-10-09T00:00:00Z"], utc=True
            ),
            "temperature_c": [18.5],
        }
    ).to_parquet(parquet_dir / "part-0.parquet", index=False)

    payloads = list(iter_replay_payloads(tmp_path, "PUMP-06"))

    assert payloads == [
        {
            "timestamp_utc": "2025-10-09T00:00:00+00:00",
            "temperature_c": 18.5,
        }
    ]


def test_replay_publisher_uses_device_topic_and_realtime_delays(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_telemetry_csv(tmp_path)
    client = FakeIotDataClient()
    device = Device(
        thing_name="PUMP-06",
        region="us-east-1",
        endpoint="example-ats.iot.us-east-1.amazonaws.com",
        topic="devices/PUMP-06/telemetry",
    )
    delays: list[float] = []
    monkeypatch.setattr(publisher.time, "sleep", delays.append)

    count = publisher.publish_replay_telemetry(
        client,  # type: ignore[arg-type]
        device,
        data_dir=tmp_path,
        asset="PUMP-06",
        start=DEFAULT_START,
        end="2025-10-10T00:00:00Z",
        speed=1.0,
    )

    assert count == 2
    assert delays == [pytest.approx(60.0)]
    assert all(
        message["topic"] == device.topic and message["qos"] == 1
        for message in client.publications
    )
    assert json.loads(client.publications[0]["payload"])["sample_count"] == 1.5


def test_publisher_warms_predictor_and_prints_replay_alert(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_telemetry_csv(tmp_path)
    client = FakeIotDataClient()
    device = Device(
        thing_name="PUMP-06",
        region="us-east-1",
        endpoint="example-ats.iot.us-east-1.amazonaws.com",
        topic="devices/PUMP-06/telemetry",
    )

    class FakePredictor:
        def __init__(self) -> None:
            self.minutes: list[pd.Timestamp] = []

        def process_minute(
            self, replay_minute: ReplayMinute
        ) -> dict[str, object] | None:
            self.minutes.append(replay_minute.timestamp)
            if replay_minute.is_warmup:
                return {"fault_type": "PRE_ROLL_ALERT"}
            if replay_minute.timestamp == pd.Timestamp("2025-10-09T00:01:00Z"):
                return {
                    "fault_type": "TEST_ALERT",
                    "alert_time_utc": "2025-10-09T00:02:00Z",
                }
            return None

    predictor = FakePredictor()
    count = publisher.publish_replay_telemetry(
        client,  # type: ignore[arg-type]
        device,
        data_dir=tmp_path,
        asset="PUMP-06",
        start="2025-10-09T00:01:00Z",
        model_history_start="2025-10-09T00:00:00Z",
        end="2025-10-09T00:02:00Z",
        speed=0,
        predictor=predictor,
    )

    assert count == 1
    assert predictor.minutes == [
        pd.Timestamp("2025-10-09T00:00:00Z"),
        pd.Timestamp("2025-10-09T00:01:00Z"),
    ]
    output = capsys.readouterr().out
    assert "Prediction:" in output
    assert "Warm-up prediction" not in output
    assert len(client.publications) == 1
    assert json.loads(client.publications[0]["payload"])["timestamp_utc"] == (
        "2025-10-09T00:01:00+00:00"
    )


def test_publisher_prints_no_alert_replay_status_but_suppresses_warmup(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_telemetry_csv(tmp_path)
    client = FakeIotDataClient()
    device = Device(
        thing_name="PUMP-06",
        region="us-east-1",
        endpoint="example-ats.iot.us-east-1.amazonaws.com",
        topic="devices/PUMP-06/telemetry",
    )

    class StatusPredictor:
        def process_minute(
            self, replay_minute: ReplayMinute
        ) -> dict[str, object]:
            return {
                "phase": "learning_normal" if replay_minute.is_warmup else "watching",
                "fault_type": None,
            }

    count = publisher.publish_replay_telemetry(
        client,  # type: ignore[arg-type]
        device,
        data_dir=tmp_path,
        asset="PUMP-06",
        start="2025-10-09T00:01:00Z",
        end="2025-10-09T00:02:00Z",
        speed=0,
        predictor=StatusPredictor(),
    )

    output = capsys.readouterr().out
    assert count == 1
    assert output.count("Prediction:") == 1
    assert '"phase":"watching"' in output
    assert "learning_normal" not in output


def test_replay_publisher_stops_when_stop_event_is_set(tmp_path) -> None:
    _write_telemetry_csv(tmp_path)
    stop_event = Event()

    class StopAfterPublishClient(FakeIotDataClient):
        def publish(self, **kwargs: Any) -> dict[str, Any]:
            response = super().publish(**kwargs)
            stop_event.set()
            return response

    client = StopAfterPublishClient()
    device = Device(
        thing_name="PUMP-06",
        region="us-east-1",
        endpoint="example-ats.iot.us-east-1.amazonaws.com",
        topic="devices/PUMP-06/telemetry",
    )

    count = publisher.publish_replay_telemetry(
        client,  # type: ignore[arg-type]
        device,
        data_dir=tmp_path,
        asset="PUMP-06",
        start=DEFAULT_START,
        speed=0,
        stop_event=stop_event,
    )

    assert count == 1
    assert len(client.publications) == 1


def test_replay_cli_start_is_overridable_and_has_requested_default() -> None:
    parser = _build_parser()
    default_args = parser.parse_args(["--asset", "PUMP-06", "--data", "data/full"])
    custom_args = parser.parse_args(
        [
            "--asset",
            "PUMP-06",
            "--data",
            "data/full",
            "--start",
            "2025-10-10T00:00:00Z",
            "--model-history-start",
            "2025-10-08T00:00:00Z",
        ]
    )

    assert default_args.start == "2025-10-09T00:00:00Z"
    assert default_args.speed == 1.0
    assert default_args.model == "polar-mind"
    assert default_args.thing_name == "PUMP-06"
    assert custom_args.start == "2025-10-10T00:00:00Z"
    assert default_args.model_history_start is None
    assert custom_args.model_history_start == "2025-10-08T00:00:00Z"


def test_replay_cli_requires_a_dataset() -> None:
    with pytest.raises(SystemExit) as error:
        _build_parser().parse_args(["--thing-name", "PUMP-06"])

    assert error.value.code == 2
