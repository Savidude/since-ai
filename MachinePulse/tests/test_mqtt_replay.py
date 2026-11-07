from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from awscrt import mqtt

from backend import mqtt_replay
from backend.mqtt_replay import (
    MqttReplaySession,
    get_replay_timestamp_utc,
    normalize_alert_message,
)


class CompletedFuture:
    def result(self) -> dict[str, object]:
        return {}


class FakeConnection:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.callbacks: dict[str, Any] = {}
        self.published_payload: str | bytes | None = None
        self.publish_started = threading.Event()
        self.alert_subscription_completed = threading.Event()
        self.published_at: float | None = None
        self.alert_subscribed_at: float | None = None
        self.disconnected = False

    def connect(self) -> CompletedFuture:
        self.events.append("connect")
        return CompletedFuture()

    def subscribe(self, **kwargs: Any) -> tuple[CompletedFuture, int]:
        topic = kwargs["topic"]
        self.events.append(f"subscribe:{topic}")
        self.callbacks[topic] = kwargs["callback"]
        if topic == "devices/PUMP-06/alerts":
            self.alert_subscribed_at = time.monotonic()
            kwargs["callback"](
                topic=topic,
                payload=json.dumps(
                    {
                        "asset_id": "PUMP-06",
                        "minute_utc": "2025-10-09T21:40:00Z",
                        "phase": "watching",
                    }
                ).encode("utf-8"),
                dup=False,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                retain=True,
            )
            self.alert_subscription_completed.set()
        return CompletedFuture(), len(self.events)

    def publish(self, **kwargs: Any) -> tuple[CompletedFuture, int]:
        topic = kwargs["topic"]
        self.events.append(f"publish:{topic}")
        self.published_payload = kwargs["payload"]
        self.published_at = time.monotonic()

        self.callbacks["devices/PUMP-06/telemetry"](
            topic="devices/PUMP-06/telemetry",
            payload=json.dumps(
                {
                    "timestamp_utc": "2025-10-09T21:45:00Z",
                    "motor_current_a": 17.2833,
                }
            ).encode("utf-8"),
            dup=False,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            retain=False,
        )
        self.publish_started.set()
        return CompletedFuture(), len(self.events)

    def disconnect(self) -> CompletedFuture:
        self.disconnected = True
        return CompletedFuture()


class FakeIotClient:
    def describe_endpoint(self, **_kwargs: Any) -> dict[str, str]:
        return {"endpointAddress": "example-ats.iot.us-east-1.amazonaws.com"}


class FakeSession:
    region_name = "us-east-1"

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def client(self, *_args: Any, **_kwargs: Any) -> FakeIotClient:
        return FakeIotClient()


def _wait_for(predicate: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_normalize_alert_payloads() -> None:
    watching = normalize_alert_message(
        json.dumps(
            {
                "asset_id": "PUMP-06",
                "phase": "watching",
                "event_class": None,
                "fault_type": None,
                "confidence": None,
            }
        ).encode("utf-8"),
        asset_id="PUMP-06",
        topic="devices/PUMP-06/alerts",
    )
    fault = normalize_alert_message(
        json.dumps(
            {
                "asset_id": "PUMP-06",
                "phase": "alert",
                "event_class": "MACHINE",
                "fault_type": "BEARING_WEAR",
                "confidence": 0.64,
                "evidence": "Vibration and pressure anomalies.",
                "recommended_action": "Inspect the bearing.",
            }
        ).encode("utf-8"),
        asset_id="PUMP-06",
        topic="devices/PUMP-06/alerts",
    )

    assert watching is not None
    assert watching["phase"] == "watching"
    assert watching["fault_type"] is None
    assert fault is not None
    assert fault["event_class"] == "MACHINE"
    assert fault["fault_type"] == "BEARING_WEAR"
    assert fault["confidence"] == 0.64
    assert fault["recommended_action"] == "Inspect the bearing."


def test_normalize_nested_alert_payload() -> None:
    alert = normalize_alert_message(
        json.dumps(
            {
                "topic": "devices/PUMP-06/alerts",
                "payload": {"phase": "watching"},
            }
        ).encode("utf-8"),
        asset_id="PUMP-06",
        topic="devices/PUMP-06/alerts",
    )

    assert alert == {"phase": "watching", "asset_id": "PUMP-06"}


def test_replay_timestamp_is_ten_minutes_before_fault_or_selection() -> None:
    selected_timestamp = datetime(
        2025,
        10,
        9,
        21,
        50,
        tzinfo=timezone.utc,
    )
    fault_timestamp = datetime(2025, 10, 9, 21, 55)

    assert get_replay_timestamp_utc(
        selected_timestamp,
        fault_timestamp,
    ) == datetime(2025, 10, 9, 21, 45, tzinfo=timezone.utc)
    assert get_replay_timestamp_utc(
        selected_timestamp,
    ) == datetime(2025, 10, 9, 21, 40, tzinfo=timezone.utc)


def test_replay_session_delays_alert_subscription_and_stops_on_fault(
    monkeypatch: Any,
) -> None:
    connection = FakeConnection()
    builder_options: dict[str, Any] = {}
    delay_seconds = 0.1
    monkeypatch.setattr(
        mqtt_replay,
        "_ALERT_SUBSCRIPTION_DELAY_SECONDS",
        delay_seconds,
        raising=False,
    )

    def fake_builder(**kwargs: Any) -> FakeConnection:
        builder_options.update(kwargs)
        return connection

    monkeypatch.setattr(mqtt_replay.boto3, "Session", FakeSession)
    monkeypatch.setattr(
        mqtt_replay,
        "_create_credentials_provider",
        lambda _session: object(),
    )
    monkeypatch.setattr(
        mqtt_replay.mqtt_connection_builder,
        "websockets_with_default_aws_signing",
        fake_builder,
    )

    session = MqttReplaySession(
        "PUMP-06",
        datetime(
            2025,
            10,
            9,
            23,
            45,
            tzinfo=timezone(timedelta(hours=2)),
        ),
    )
    try:
        assert connection.publish_started.wait(timeout=2)
        assert _wait_for(lambda: session.published_at is not None)
        assert connection.alert_subscription_completed.wait(timeout=2)

        assert connection.events == [
            "connect",
            "subscribe:devices/PUMP-06/telemetry",
            "publish:devices/PUMP-06/replay",
            "subscribe:devices/PUMP-06/alerts",
        ]
        assert connection.published_at is not None
        assert connection.alert_subscribed_at is not None
        assert connection.alert_subscribed_at - connection.published_at >= (
            delay_seconds * 0.8
        )
        assert json.loads(str(connection.published_payload)) == {
            "timestamp_utc": "2025-10-09T21:45:00Z"
        }
        assert not session.ready
        assert session.latest_alert is None
        assert session.get_telemetry_records() == [
            {
                "timestamp_utc": "2025-10-09T21:45:00Z",
                "motor_current_a": 17.2833,
                "pump_id": "PUMP-06",
                "topic": "devices/PUMP-06/telemetry",
            }
        ]

        connection.callbacks["devices/PUMP-06/alerts"](
            topic="devices/PUMP-06/alerts",
            payload=json.dumps(
                {
                    "asset_id": "PUMP-06",
                    "minute_utc": "2025-10-09T21:46:00Z",
                    "phase": "watching",
                    "event_class": None,
                    "fault_type": None,
                    "confidence": None,
                }
            ).encode("utf-8"),
            dup=False,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            retain=False,
        )
        assert session.ready
        assert session.latest_alert is not None
        assert session.latest_alert["phase"] == "watching"

        connection.callbacks["devices/PUMP-06/alerts"](
            topic="devices/PUMP-06/alerts",
            payload=json.dumps(
                {
                    "asset_id": "PUMP-06",
                    "phase": "alert",
                    "event_class": "MACHINE",
                    "fault_type": "BEARING_WEAR",
                    "confidence": 0.64,
                    "evidence": "Vibration and pressure anomalies.",
                    "recommended_action": "Inspect the bearing.",
                }
            ).encode("utf-8"),
            dup=False,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            retain=False,
        )
        assert session.status == "fault_detected"
        assert _wait_for(lambda: connection.disconnected)

        connection.callbacks["devices/PUMP-06/telemetry"](
            topic="devices/PUMP-06/telemetry",
            payload=json.dumps(
                {
                    "timestamp_utc": "2025-10-09T21:46:00Z",
                    "motor_current_a": 18.0,
                }
            ).encode("utf-8"),
            dup=False,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            retain=False,
        )
        assert len(session.get_telemetry_records()) == 1
        assert session.latest_alert is not None
        assert session.latest_alert["fault_type"] == "BEARING_WEAR"
        assert builder_options["region"] == "us-east-1"
    finally:
        session.stop()

    assert connection.disconnected


def test_replay_session_rejects_naive_timestamp() -> None:
    try:
        MqttReplaySession(
            "PUMP-06",
            datetime(2025, 10, 9, 21, 45),
        )
    except ValueError as error:
        assert "timezone" in str(error)
    else:
        raise AssertionError("A naive replay timestamp should be rejected.")
