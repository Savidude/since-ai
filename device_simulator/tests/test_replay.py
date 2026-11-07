import json
from threading import Event
from typing import Any

import pandas as pd
import pytest

from device_simulator import publisher
from device_simulator.cli import _build_parser
from device_simulator.provisioning import Device
from device_simulator.replay import DEFAULT_START, iter_replay_payloads


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
                "missing_value": float("nan"),
            },
            {
                "asset_id": "PUMP-06",
                "day": "2025-10-09",
                "timestamp_utc": "2025-10-09T00:00:00Z",
                "temperature_c": 2.0,
                "sample_count": 1,
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
        "2025-10-09T00:00:01+00:00",
    ]
    assert payloads[1]["temperature_c"] == 1.23457
    assert payloads[0]["sample_count"] == 1
    assert "asset_id" not in payloads[0]
    assert "day" not in payloads[0]
    assert "missing_value" not in payloads[0]


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
    assert delays == [pytest.approx(1.0)]
    assert all(
        message["topic"] == device.topic and message["qos"] == 1
        for message in client.publications
    )
    assert json.loads(client.publications[0]["payload"])["sample_count"] == 1


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
        ]
    )

    assert default_args.start == "2025-10-09T00:00:00Z"
    assert default_args.speed == 1.0
    assert default_args.thing_name == "PUMP-06"
    assert custom_args.start == "2025-10-10T00:00:00Z"


def test_replay_cli_requires_a_dataset() -> None:
    with pytest.raises(SystemExit) as error:
        _build_parser().parse_args(["--thing-name", "PUMP-06"])

    assert error.value.code == 2
