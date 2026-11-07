from __future__ import annotations

import io
import json
import threading
from typing import Any

from awscrt import auth, mqtt

from device_monitor import monitor
from device_monitor.cli import _build_parser


class CompletedFuture:
    def result(self) -> dict[str, object]:
        return {}


class FakeConnection:
    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self.subscription: dict[str, Any] | None = None

    def connect(self) -> CompletedFuture:
        self.connected = True
        return CompletedFuture()

    def subscribe(self, **kwargs: Any) -> tuple[CompletedFuture, int]:
        self.subscription = kwargs
        callback = kwargs["callback"]
        callback(
            topic=kwargs["topic"],
            payload=b'{"device_id":"demo-device"}',
            dup=False,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            retain=False,
        )
        return CompletedFuture(), 1

    def disconnect(self) -> CompletedFuture:
        self.disconnected = True
        return CompletedFuture()


def test_monitor_subscribes_prints_messages_and_disconnects(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    connection = FakeConnection()
    builder_options: dict[str, Any] = {}

    def fake_builder(**kwargs: Any) -> FakeConnection:
        builder_options.update(kwargs)
        return connection

    monkeypatch.setattr(
        monitor.mqtt_connection_builder,
        "websockets_with_default_aws_signing",
        fake_builder,
    )
    stop_event = threading.Event()
    stop_event.set()

    monitor.monitor_topic(
        endpoint="example-ats.iot.eu-central-1.amazonaws.com",
        region="eu-central-1",
        topic="devices/demo-device/telemetry",
        client_id="device-monitor-test",
        credentials_provider=auth.AwsCredentialsProvider.new_static("test", "test"),
        stop_event=stop_event,
    )

    output = capsys.readouterr().out
    assert "devices/demo-device/telemetry" in output
    assert '"device_id":"demo-device"' in output
    assert builder_options["region"] == "eu-central-1"
    assert builder_options["client_id"] == "device-monitor-test"
    assert connection.connected
    assert connection.disconnected
    assert connection.subscription is not None
    assert connection.subscription["qos"] == mqtt.QoS.AT_LEAST_ONCE


def test_monitor_captures_jsonl_messages(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    connection = FakeConnection()

    def fake_builder(**kwargs: Any) -> FakeConnection:
        return connection

    monkeypatch.setattr(
        monitor.mqtt_connection_builder,
        "websockets_with_default_aws_signing",
        fake_builder,
    )
    capture_file = io.StringIO()
    stop_event = threading.Event()
    stop_event.set()

    monitor.monitor_topic(
        endpoint="example-ats.iot.eu-central-1.amazonaws.com",
        region="eu-central-1",
        topic="devices/PUMP-06/telemetry",
        client_id="device-monitor-test",
        credentials_provider=auth.AwsCredentialsProvider.new_static("test", "test"),
        stop_event=stop_event,
        capture_file=capture_file,
    )

    record = json.loads(capture_file.getvalue())
    assert record == {
        "topic": "devices/PUMP-06/telemetry",
        "payload": {"device_id": "demo-device"},
    }
    assert "devices/PUMP-06/telemetry" in capsys.readouterr().out
    assert connection.disconnected


def test_monitor_cli_accepts_asset_and_capture_path() -> None:
    args = _build_parser().parse_args(
        ["--asset", "PUMP-06", "--capture", "pump-06.jsonl"]
    )

    assert args.thing_name == "PUMP-06"
    assert str(args.capture) == "pump-06.jsonl"
