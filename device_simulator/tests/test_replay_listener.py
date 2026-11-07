from __future__ import annotations

from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread
from typing import Callable

import pytest
from awscrt import mqtt
from botocore.credentials import Credentials, RefreshableCredentials

from device_simulator import cli, replay_listener
from device_simulator.provisioning import Device
from device_simulator.replay import DEFAULT_START


class CompletedFuture:
    def result(self) -> None:
        return None

    def add_done_callback(self, callback: Callable[[CompletedFuture], None]) -> None:
        callback(self)


class FakeConnection:
    def __init__(self) -> None:
        self.message_callback: Callable[..., None] | None = None
        self.subscribed = Event()
        self.subscriptions: list[tuple[str, mqtt.QoS]] = []
        self.disconnected = False

    def connect(self) -> CompletedFuture:
        return CompletedFuture()

    def subscribe(self, *, topic: str, qos: mqtt.QoS, callback: Callable[..., None]):
        self.message_callback = callback
        self.subscriptions.append((topic, qos))
        self.subscribed.set()
        return CompletedFuture(), len(self.subscriptions)

    def disconnect(self) -> CompletedFuture:
        self.disconnected = True
        return CompletedFuture()


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b"{}",
        b'{"timestamp_utc": 123}',
        b'{"timestamp_utc": "not-a-timestamp"}',
    ],
)
def test_parse_replay_start_rejects_invalid_payloads(payload: bytes) -> None:
    with pytest.raises(ValueError):
        replay_listener.parse_replay_start(payload)


def test_parse_replay_start_accepts_utc_timestamp() -> None:
    assert replay_listener.parse_replay_start(
        b'{"timestamp_utc":"2025-10-10T00:00:00Z"}'
    ) == "2025-10-10T00:00:00Z"


def test_boto3_credentials_keep_session_token_and_expiration() -> None:
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    metadata = {
        "access_key": "test-access-key",
        "secret_key": "test-secret-key",
        "token": "test-session-token",
        "expiry_time": expiry.isoformat(),
    }
    credentials = RefreshableCredentials.create_from_metadata(
        metadata=metadata,
        refresh_using=lambda: metadata,
        method="test",
    )

    aws_credentials = replay_listener._to_aws_credentials(credentials)

    assert aws_credentials.access_key_id == "test-access-key"
    assert aws_credentials.secret_access_key == "test-secret-key"
    assert aws_credentials.session_token == "test-session-token"
    assert aws_credentials.expiration is not None


def test_replay_listener_subscribes_and_delivers_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    builder_arguments: dict[str, object] = {}

    def build_connection(**kwargs: object) -> FakeConnection:
        builder_arguments.update(kwargs)
        return connection

    monkeypatch.setattr(
        replay_listener.auth.AwsCredentialsProvider,
        "new_delegate",
        lambda callback: object(),
    )
    monkeypatch.setattr(
        replay_listener.mqtt_connection_builder,
        "websockets_with_default_aws_signing",
        build_connection,
    )

    stop_event = Event()
    ready = Event()
    commands = replay_listener.iter_replay_commands(
        endpoint="example-ats.iot.us-east-1.amazonaws.com",
        region="us-east-1",
        thing_name="sensor-1",
        credentials=Credentials("test-access-key", "test-secret-key"),
        stop_event=stop_event,
        on_ready=ready.set,
    )
    received: Queue[bytes | Exception] = Queue()

    def read_command() -> None:
        try:
            received.put(next(commands))
        except Exception as error:
            received.put(error)

    worker = Thread(target=read_command, daemon=True)
    worker.start()
    assert connection.subscribed.wait(timeout=5)
    assert ready.wait(timeout=1)

    payload = b'{"timestamp_utc":"2025-10-10T00:00:00Z"}'
    assert connection.message_callback is not None
    connection.message_callback(
        "devices/sensor-1/replay",
        payload,
        False,
        mqtt.QoS.AT_LEAST_ONCE,
        False,
    )
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert received.get_nowait() == payload
    assert connection.subscriptions == [
        ("devices/sensor-1/replay", mqtt.QoS.AT_LEAST_ONCE)
    ]
    assert builder_arguments["endpoint"] == "example-ats.iot.us-east-1.amazonaws.com"
    assert builder_arguments["region"] == "us-east-1"

    stop_event.set()
    with pytest.raises(StopIteration):
        next(commands)
    assert connection.disconnected


def test_cli_requires_data_even_without_replay_listener() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["--thing-name", "sensor-1"])

    assert error.value.code == 2


def test_listener_command_timestamp_becomes_replay_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __init__(
            self,
            *,
            region_name: str | None,
            credentials: Credentials,
        ) -> None:
            self.region_name = region_name
            self.credentials = credentials

        def client(self, service_name: str, **kwargs: object) -> str:
            return service_name

        def get_credentials(self) -> Credentials:
            return self.credentials

    class FakeProvisioner:
        def __init__(self, *, iot_client: object, region: str) -> None:
            self.region = region

        def ensure_device(self, thing_name: str) -> Device:
            return Device(
                thing_name=thing_name,
                region=self.region,
                endpoint="example-ats.iot.us-east-1.amazonaws.com",
                topic=f"devices/{thing_name}/telemetry",
            )

    payload = b'{"timestamp_utc":"2025-10-10T00:00:00Z"}'
    replay_calls: list[dict[str, object]] = []
    initial_replay_stopped = Event()
    requested_replay_started = Event()

    boto_credentials = Credentials("test-access-key", "test-secret-key")
    session = FakeSession(region_name="us-east-1", credentials=boto_credentials)

    def replay_commands(**kwargs: object):
        assert kwargs["endpoint"] == "example-ats.iot.us-east-1.amazonaws.com"
        assert kwargs["region"] == "us-east-1"
        assert kwargs["thing_name"] == "PUMP-06"
        assert kwargs["credentials"] is boto_credentials
        assert isinstance(kwargs["stop_event"], Event)
        on_ready = kwargs["on_ready"]
        assert callable(on_ready)
        on_ready()
        yield payload

    def publish_replay(
        iot_data_client: object,
        device: Device,
        **kwargs: object,
    ) -> int:
        replay_calls.append({"iot_data_client": iot_data_client, "device": device, **kwargs})
        stop_event = kwargs.get("stop_event")
        assert isinstance(stop_event, Event)
        if kwargs["start"] == DEFAULT_START:
            assert stop_event.wait(timeout=2)
            initial_replay_stopped.set()
        else:
            assert initial_replay_stopped.wait(timeout=2)
            requested_replay_started.set()
            stop_event.wait(timeout=2)
        return 1

    monkeypatch.setattr(cli.boto3, "Session", lambda **kwargs: session)
    monkeypatch.setattr(cli, "AwsIotProvisioner", FakeProvisioner)
    monkeypatch.setattr(cli, "iter_replay_commands", replay_commands)
    monkeypatch.setattr(cli, "publish_replay_telemetry", publish_replay)

    status = cli.main(
        [
            "--thing-name",
            "PUMP-06",
            "--region",
            "us-east-1",
            "--data",
            "data/full",
            "--listen-for-replay",
            "--model",
            "none",
            "--end",
            "2025-10-11T00:00:00Z",
            "--limit",
            "7",
            "--speed",
            "0",
        ]
    )
    assert status == 0
    assert status == 0
    initial_replay = next(call for call in replay_calls if call["start"] == DEFAULT_START)
    requested_replay = next(
        call for call in replay_calls if call["start"] == "2025-10-10T00:00:00Z"
    )
    assert initial_replay_stopped.is_set()
    assert requested_replay_started.is_set()
    assert initial_replay["start"] == DEFAULT_START
    assert initial_replay["end"] == "2025-10-11T00:00:00Z"
    assert initial_replay["limit"] == 7
    assert initial_replay["speed"] == 0.0
    assert requested_replay["start"] == "2025-10-10T00:00:00Z"
    assert requested_replay["end"] == "2025-10-11T00:00:00Z"
    assert requested_replay["limit"] == 7
    assert requested_replay["speed"] == 0.0
