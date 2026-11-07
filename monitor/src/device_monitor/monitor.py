from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TextIO

import boto3
from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder

LOGGER = logging.getLogger(__name__)


class MonitorError(RuntimeError):
    """Raised when the monitor cannot obtain AWS credentials."""


def create_credentials_provider(
    session: boto3.Session,
) -> auth.AwsCredentialsProvider:
    credentials = session.get_credentials()
    if credentials is None:
        raise MonitorError("No AWS credentials found in the boto3 credential chain.")

    def refresh_credentials() -> auth.AwsCredentials:
        frozen_credentials = credentials.get_frozen_credentials()
        expiration_value = getattr(credentials, "_expiry_time", None)
        if expiration_value is not None and not isinstance(expiration_value, datetime):
            raise MonitorError("AWS credential expiration has an unsupported format.")
        return auth.AwsCredentials(
            frozen_credentials.access_key,
            frozen_credentials.secret_key,
            frozen_credentials.token,
            expiration=expiration_value,
        )

    return auth.AwsCredentialsProvider.new_delegate(refresh_credentials)


def monitor_topic(
    *,
    endpoint: str,
    region: str,
    topic: str,
    client_id: str,
    credentials_provider: auth.AwsCredentialsProvider,
    stop_event: threading.Event | None = None,
    capture_file: TextIO | None = None,
    additional_topics: Sequence[str] = (),
) -> None:
    connection = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=endpoint,
        region=region,
        credentials_provider=credentials_provider,
        client_id=client_id,
        clean_session=True,
        keep_alive_secs=30,
        on_connection_interrupted=_on_connection_interrupted,
    )

    connected = False
    try:
        connection.connect().result()
        connected = True
        callback = _message_callback(capture_file)
        topics = (topic, *additional_topics)
        for subscribed_topic in topics:
            subscribe_future, _packet_id = connection.subscribe(
                topic=subscribed_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=callback,
            )
            subscribe_future.result()
        LOGGER.info(
            "Listening on %s; press Ctrl+C to stop.", ", ".join(topics)
        )
        event = stop_event if stop_event is not None else threading.Event()
        event.wait()
    finally:
        if connected:
            connection.disconnect().result()


def _message_callback(capture_file: TextIO | None) -> Callable[..., None]:
    capture_lock = threading.Lock()

    def receive(topic: str, payload: bytes, **kwargs: object) -> None:
        message = payload.decode("utf-8", errors="replace")
        print(f"{topic}: {message}", flush=True)
        if capture_file is None:
            return

        try:
            decoded_payload = json.loads(message)
        except json.JSONDecodeError as error:
            LOGGER.warning("Non-JSON payload received on %s; capturing it as text: %s", topic, error)
            decoded_payload = message

        record = json.dumps(
            {"topic": topic, "payload": decoded_payload},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with capture_lock:
            capture_file.write(record + "\n")
            capture_file.flush()

    return receive


def _on_connection_interrupted(connection: object, error: object, **kwargs: object) -> None:
    LOGGER.warning("MQTT connection interrupted: %s", error)
