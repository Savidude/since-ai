from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from typing import Any

import boto3
from awscrt import auth, mqtt
from awscrt.exceptions import AwsCrtError
from awsiot import mqtt_connection_builder
from botocore.exceptions import BotoCoreError

LOGGER = logging.getLogger(__name__)
_ASSET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_IDLE_TIMEOUT_SECONDS = 15


def normalize_telemetry_message(
    payload: bytes,
    *,
    asset_id: str,
    topic: str,
) -> dict[str, Any] | None:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        LOGGER.warning("Ignoring non-JSON telemetry on %s: %s", topic, error)
        return None

    if not isinstance(message, dict):
        LOGGER.warning("Ignoring non-object telemetry on %s", topic)
        return None

    if isinstance(message.get("payload"), dict):
        message = message["payload"]

    record = dict(message)
    record["pump_id"] = asset_id
    record["topic"] = topic
    return record


class MqttTelemetrySubscriber:
    def __init__(self, asset_id: str, max_records: int = 1000) -> None:
        if not _ASSET_NAME_PATTERN.fullmatch(asset_id):
            raise ValueError(
                "Asset ID must contain 1-128 letters, digits, hyphens, or underscores."
            )
        if max_records < 1:
            raise ValueError("max_records must be greater than zero.")

        self.asset_id = asset_id
        self.topic = f"devices/{asset_id}/telemetry"
        self._records: deque[dict[str, Any]] = deque(maxlen=max_records)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_access = time.monotonic()
        self._status = "connecting"
        self._error: str | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"mqtt-{asset_id.lower()}",
            daemon=True,
        )
        self._thread.start()

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    def get_records(self) -> list[dict[str, Any]]:
        with self._lock:
            self._last_access = time.monotonic()
            return [record.copy() for record in self._records]

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)

    def _on_message(self, topic: str, payload: bytes, **_kwargs: object) -> None:
        record = normalize_telemetry_message(
            payload,
            asset_id=self.asset_id,
            topic=topic,
        )
        if record is None:
            return

        with self._lock:
            self._records.append(record)

    def _on_connection_interrupted(
        self,
        _connection: object,
        error: object,
        **_kwargs: object,
    ) -> None:
        LOGGER.warning("MQTT connection interrupted for %s: %s", self.asset_id, error)
        self._set_status("reconnecting", str(error))

    def _on_connection_resumed(
        self,
        _connection: object,
        _return_code: object,
        _session_present: object,
        **_kwargs: object,
    ) -> None:
        self._set_status("connected")

    def _run(self) -> None:
        connection = None
        connected = False
        try:
            session = boto3.Session(region_name=os.environ.get("AWS_REGION"))
            region = session.region_name
            if not region:
                raise RuntimeError(
                    "No AWS region configured; set AWS_REGION or configure a default region."
                )

            endpoint = session.client("iot", region_name=region).describe_endpoint(
                endpointType="iot:Data-ATS"
            )["endpointAddress"]
            credentials_provider = _create_credentials_provider(session)
            connection = mqtt_connection_builder.websockets_with_default_aws_signing(
                endpoint=endpoint,
                region=region,
                credentials_provider=credentials_provider,
                client_id=f"device-monitor-{uuid.uuid4().hex[:8]}",
                clean_session=True,
                keep_alive_secs=30,
                on_connection_interrupted=self._on_connection_interrupted,
                on_connection_resumed=self._on_connection_resumed,
            )

            connection.connect().result()
            connected = True
            subscribe_future, _packet_id = connection.subscribe(
                topic=self.topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_message,
            )
            subscribe_future.result()
            self._set_status("connected")
            while not self._stop_event.wait(timeout=1):
                with self._lock:
                    last_access = self._last_access
                if time.monotonic() - last_access >= _IDLE_TIMEOUT_SECONDS:
                    break
        except (AwsCrtError, BotoCoreError, OSError, RuntimeError, ValueError) as error:
            LOGGER.exception("MQTT telemetry subscription failed for %s", self.asset_id)
            self._set_status("error", str(error))
        finally:
            if connected and connection is not None:
                try:
                    connection.disconnect().result()
                except (AwsCrtError, RuntimeError) as error:
                    LOGGER.exception("MQTT disconnect failed for %s", self.asset_id)

            if self.status != "error":
                self._set_status("stopped")

    def _set_status(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self._status = status
            self._error = error


def _create_credentials_provider(
    session: boto3.Session,
) -> auth.AwsCredentialsProvider:
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials found in the boto3 credential chain.")

    def refresh_credentials() -> auth.AwsCredentials:
        frozen_credentials = credentials.get_frozen_credentials()
        expiration_value = getattr(credentials, "_expiry_time", None)
        if expiration_value is not None and not isinstance(expiration_value, datetime):
            raise RuntimeError("AWS credential expiration has an unsupported format.")
        return auth.AwsCredentials(
            frozen_credentials.access_key,
            frozen_credentials.secret_key,
            frozen_credentials.token,
            expiration=expiration_value,
        )

    return auth.AwsCredentialsProvider.new_delegate(refresh_credentials)
