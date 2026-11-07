from __future__ import annotations

import io
import json
import threading
from typing import Any

import pytest
from awscrt import auth, mqtt

from device_monitor import cli, monitor
from device_monitor.cli import _build_parser


class CompletedFuture:
    def result(self) -> dict[str, object]:
        return {}


class FakeConnection:
    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self.subscriptions: list[dict[str, Any]] = []

    def connect(self) -> CompletedFuture:
        self.connected = True
        return CompletedFuture()

    def subscribe(self, **kwargs: Any) -> tuple[CompletedFuture, int]:
        self.subscriptions.append(kwargs)
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


def test_monitor_subscribes_to_telemetry_and_alerts_and_disconnects(
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
        additional_topics=("devices/demo-device/alerts",),
        client_id="device-monitor-test",
        credentials_provider=auth.AwsCredentialsProvider.new_static("test", "test"),
        stop_event=stop_event,
    )

    output = capsys.readouterr().out
    assert "devices/demo-device/telemetry" in output
    assert "devices/demo-device/alerts" in output
    assert '"device_id":"demo-device"' in output
    assert builder_options["region"] == "eu-central-1"
    assert builder_options["client_id"] == "device-monitor-test"
    assert connection.connected
    assert connection.disconnected
    assert [item["topic"] for item in connection.subscriptions] == [
        "devices/demo-device/telemetry",
        "devices/demo-device/alerts",
    ]
    assert all(
        item["qos"] == mqtt.QoS.AT_LEAST_ONCE
        for item in connection.subscriptions
    )


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
        additional_topics=("devices/PUMP-06/alerts",),
        client_id="device-monitor-test",
        credentials_provider=auth.AwsCredentialsProvider.new_static("test", "test"),
        stop_event=stop_event,
        capture_file=capture_file,
    )

    records = [
        json.loads(line) for line in capture_file.getvalue().splitlines()
    ]
    assert records == [
        {
            "topic": "devices/PUMP-06/telemetry",
            "payload": {"device_id": "demo-device"},
        },
        {
            "topic": "devices/PUMP-06/alerts",
            "payload": {"device_id": "demo-device"},
        },
    ]
    output = capsys.readouterr().out
    assert "devices/PUMP-06/telemetry" in output
    assert "devices/PUMP-06/alerts" in output
    assert connection.disconnected


def test_monitor_cli_accepts_asset_and_capture_path() -> None:
    args = _build_parser().parse_args(
        ["--asset", "PUMP-06", "--capture", "pump-06.jsonl"]
    )

    assert args.thing_name == "PUMP-06"
    assert str(args.capture) == "pump-06.jsonl"


@pytest.mark.parametrize(
    ("selection", "expected_topics"),
    [
        (None, ("devices/PUMP-06/telemetry", "devices/PUMP-06/alerts")),
        ("telemetry", ("devices/PUMP-06/telemetry",)),
        ("alerts", ("devices/PUMP-06/alerts",)),
    ],
)
def test_monitor_cli_subscribes_to_selected_topics(
    monkeypatch: Any,
    selection: str | None,
    expected_topics: tuple[str, ...],
) -> None:
    captured: dict[str, Any] = {}

    class FakeIotClient:
        def describe_endpoint(self, **kwargs: Any) -> dict[str, str]:
            return {"endpointAddress": "example-ats.iot.eu-central-1.amazonaws.com"}

    class FakeSession:
        region_name = "eu-central-1"

        def __init__(
            self, *, profile_name: str | None, region_name: str | None
        ) -> None:
            pass

        def client(self, *args: Any, **kwargs: Any) -> FakeIotClient:
            return FakeIotClient()

    monkeypatch.setattr(cli.boto3, "Session", FakeSession)
    monkeypatch.setattr(
        cli,
        "create_credentials_provider",
        lambda session: auth.AwsCredentialsProvider.new_static("test", "test"),
    )
    monkeypatch.setattr(cli, "monitor_topic", lambda **kwargs: captured.update(kwargs))

    argv = ["--thing-name", "PUMP-06", "--region", "eu-central-1"]
    if selection is not None:
        argv.extend(["--topic", selection])

    assert cli.main(argv) == 0
    assert captured["topic"] == expected_topics[0]
    assert captured["additional_topics"] == expected_topics[1:]
