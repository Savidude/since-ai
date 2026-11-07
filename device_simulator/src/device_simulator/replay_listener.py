from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from datetime import datetime
from queue import Empty, Queue
from threading import Event
from uuid import uuid4

from awscrt import auth, mqtt
from awscrt.exceptions import AwsCrtError
from awsiot import mqtt_connection_builder
from botocore.credentials import Credentials

LOGGER = logging.getLogger(__name__)


def parse_replay_start(payload: bytes) -> str:
    try:
        command = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "Replay payload must be JSON with a 'timestamp_utc' ISO-8601 string."
        ) from error

    if not isinstance(command, dict):
        raise ValueError("Replay payload must be a JSON object.")

    value = command.get("timestamp_utc")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Replay payload must contain a non-empty 'timestamp_utc' string.")

    start = value.strip()
    iso_value = f"{start[:-1]}+00:00" if start.endswith("Z") else start
    try:
        datetime.fromisoformat(iso_value)
    except ValueError as error:
        raise ValueError("'timestamp_utc' must be an ISO-8601 timestamp.") from error
    return start


def _to_aws_credentials(credentials: Credentials) -> auth.AwsCredentials:
    frozen = credentials.get_frozen_credentials()
    if not frozen.access_key or not frozen.secret_key:
        raise ValueError("Boto3 returned incomplete AWS credentials.")
    # Botocore has no public expiry accessor; pass its refresh time to AWS CRT.
    expiration = getattr(credentials, "_expiry_time", None)
    return auth.AwsCredentials(
        access_key_id=frozen.access_key,
        secret_access_key=frozen.secret_key,
        session_token=frozen.token,
        expiration=expiration,
    )


def iter_replay_commands(
    *,
    endpoint: str,
    region: str,
    thing_name: str,
    credentials: Credentials,
    stop_event: Event | None = None,
    on_ready: Callable[[], None] | None = None,
) -> Iterator[bytes]:
    topic = f"devices/{thing_name}/replay"
    messages: Queue[bytes | AwsCrtError] = Queue()

    def on_message(
        topic: str,
        payload: bytes,
        dup: bool,
        qos: mqtt.QoS,
        retain: bool,
        **kwargs: object,
    ) -> None:
        messages.put(payload)

    def on_subscription_complete(future: Future[object]) -> None:
        try:
            future.result()
        except AwsCrtError as error:
            messages.put(error)

    def subscribe(connection: mqtt.Connection) -> Future[object]:
        future, _ = connection.subscribe(
            topic=topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_message,
        )
        return future

    def on_connection_interrupted(
        connection: mqtt.Connection,
        error: AwsCrtError,
        **kwargs: object,
    ) -> None:
        LOGGER.warning("MQTT connection interrupted: %s", error)

    def on_connection_resumed(
        connection: mqtt.Connection,
        return_code: mqtt.ConnectReturnCode,
        session_present: bool,
        **kwargs: object,
    ) -> None:
        if session_present:
            return
        LOGGER.info("Re-subscribing to %s after reconnect", topic)
        try:
            future = subscribe(connection)
        except AwsCrtError as error:
            messages.put(error)
            return
        future.add_done_callback(on_subscription_complete)

    credentials_provider = auth.AwsCredentialsProvider.new_delegate(
        lambda: _to_aws_credentials(credentials)
    )
    connection = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=endpoint,
        region=region,
        credentials_provider=credentials_provider,
        client_id=f"device-simulator-{uuid4().hex}",
        clean_session=True,
        keep_alive_secs=30,
        on_connection_interrupted=on_connection_interrupted,
        on_connection_resumed=on_connection_resumed,
    )

    connected = False
    try:
        connection.connect().result()
        connected = True
        subscribe(connection).result()
        LOGGER.info("Listening for replay commands on %s", topic)
        if on_ready is not None:
            on_ready()
        while stop_event is None or not stop_event.is_set():
            try:
                message = messages.get(
                    timeout=0.1 if stop_event is not None else None
                )
            except Empty:
                continue
            if isinstance(message, AwsCrtError):
                raise message
            yield message
    finally:
        if connected:
            connection.disconnect().result()
