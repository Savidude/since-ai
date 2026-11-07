from __future__ import annotations

import json
import threading
from typing import Any

from awscrt import mqtt

from backend import mqtt_telemetry
from backend.mqtt_telemetry import (
    MqttTelemetrySubscriber,
    normalize_telemetry_message,
)


class CompletedFuture:
    def result(self) -> dict[str, object]:
        return {}


class FakeConnection:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.subscribed = threading.Event()
        self.disconnected = False
        self.subscriptions: list[dict[str, Any]] = []

    def connect(self) -> CompletedFuture:
        return CompletedFuture()

    def subscribe(self, **kwargs: Any) -> tuple[CompletedFuture, int]:
        self.subscriptions.append(kwargs)
        kwargs["callback"](
            topic=kwargs["topic"],
            payload=self.payload,
            dup=False,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            retain=False,
        )
        self.subscribed.set()
        return CompletedFuture(), 1

    def disconnect(self) -> CompletedFuture:
        self.disconnected = True
        return CompletedFuture()


def test_normalize_flat_mqtt_payload() -> None:
    payload = json.dumps(
        {
            "timestamp_utc": "2025-10-09T14:45:00+00:00",
            "motor_current_a": 17.2833,
            "operating_mode": "RUNNING",
        }
    ).encode("utf-8")

    record = normalize_telemetry_message(
        payload,
        asset_id="PUMP-06",
        topic="devices/PUMP-06/telemetry",
    )

    assert record == {
        "timestamp_utc": "2025-10-09T14:45:00+00:00",
        "motor_current_a": 17.2833,
        "operating_mode": "RUNNING",
        "pump_id": "PUMP-06",
        "topic": "devices/PUMP-06/telemetry",
    }


def test_normalize_captured_payload_wrapper() -> None:
    payload = json.dumps(
        {
            "topic": "devices/PUMP-06/telemetry",
            "payload": {"motor_current_a": 17.2833},
        }
    ).encode("utf-8")

    record = normalize_telemetry_message(
        payload,
        asset_id="PUMP-06",
        topic="devices/PUMP-06/telemetry",
    )

    assert record == {
        "motor_current_a": 17.2833,
        "pump_id": "PUMP-06",
        "topic": "devices/PUMP-06/telemetry",
    }


def test_subscriber_records_messages_from_asset_telemetry_topic(
    monkeypatch: Any,
) -> None:
    payload = json.dumps(
        {
            "timestamp_utc": "2025-10-09T14:45:00+00:00",
            "motor_current_a": 17.2833,
        }
    ).encode("utf-8")
    connection = FakeConnection(payload)
    builder_options: dict[str, Any] = {}

    class FakeIotClient:
        def describe_endpoint(self, **_kwargs: Any) -> dict[str, str]:
            return {"endpointAddress": "example-ats.iot.us-east-1.amazonaws.com"}

    class FakeSession:
        region_name = "us-east-1"

        def __init__(self, **kwargs: Any) -> None:
            pass

        def client(self, *_args: Any, **_kwargs: Any) -> FakeIotClient:
            return FakeIotClient()

    def fake_builder(**kwargs: Any) -> FakeConnection:
        builder_options.update(kwargs)
        return connection

    monkeypatch.setattr(mqtt_telemetry.boto3, "Session", FakeSession)
    monkeypatch.setattr(
        mqtt_telemetry,
        "_create_credentials_provider",
        lambda session: object(),
    )
    monkeypatch.setattr(
        mqtt_telemetry.mqtt_connection_builder,
        "websockets_with_default_aws_signing",
        fake_builder,
    )

    subscriber = MqttTelemetrySubscriber("PUMP-06")
    try:
        assert connection.subscribed.wait(timeout=2)
        assert subscriber.get_records() == [
            {
                "timestamp_utc": "2025-10-09T14:45:00+00:00",
                "motor_current_a": 17.2833,
                "pump_id": "PUMP-06",
                "topic": "devices/PUMP-06/telemetry",
            }
        ]
        assert connection.subscriptions[0]["topic"] == "devices/PUMP-06/telemetry"
        assert connection.subscriptions[0]["qos"] == mqtt.QoS.AT_LEAST_ONCE
        assert builder_options["region"] == "us-east-1"
        assert builder_options["client_id"].startswith("device-monitor-")
    finally:
        subscriber.stop()

    assert connection.disconnected
