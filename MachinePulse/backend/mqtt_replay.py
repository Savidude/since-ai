from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from awscrt import mqtt
from awscrt.exceptions import AwsCrtError
from awsiot import mqtt_connection_builder
from botocore.exceptions import BotoCoreError

from .mqtt_telemetry import (
    _create_credentials_provider,
    is_fault_alert,
    normalize_alert_message,
    normalize_telemetry_message,
)

LOGGER = logging.getLogger(__name__)
_ASSET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ALERT_SUBSCRIPTION_DELAY_SECONDS = 10
_IDLE_TIMEOUT_SECONDS = 60


def get_replay_timestamp_utc(
    selected_timestamp_utc: datetime,
    fault_timestamp_utc: datetime | None = None,
) -> datetime:
    anchor = fault_timestamp_utc or selected_timestamp_utc
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    return (
        anchor.astimezone(timezone.utc) - timedelta(minutes=10)
    ).replace(microsecond=0)


class MqttReplaySession:
    def __init__(
        self,
        asset_id: str,
        replay_timestamp_utc: datetime,
        max_records: int = 1000,
    ) -> None:
        if not _ASSET_NAME_PATTERN.fullmatch(asset_id):
            raise ValueError(
                "Asset ID must contain 1-128 letters, digits, hyphens, or underscores."
            )
        if replay_timestamp_utc.tzinfo is None or replay_timestamp_utc.utcoffset() is None:
            raise ValueError("Replay timestamp must include a timezone.")
        if max_records < 1:
            raise ValueError("max_records must be greater than zero.")

        self.asset_id = asset_id
        self.alert_topic = f"devices/{asset_id}/alerts"
        self.telemetry_topic = f"devices/{asset_id}/telemetry"
        self.replay_topic = f"devices/{asset_id}/replay"
        self.request_timestamp_utc = replay_timestamp_utc.astimezone(timezone.utc).replace(
            microsecond=0
        )

        self._telemetry_records: deque[dict[str, Any]] = deque(maxlen=max_records)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_access = time.monotonic()
        self._status = "connecting"
        self._error: str | None = None
        self._alert_error: str | None = None
        self._latest_alert: dict[str, Any] | None = None
        self._ready = False
        self._published_at: float | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"mqtt-replay-{asset_id.lower()}",
            daemon=True,
        )
        self._thread.start()

    @property
    def status(self) -> str:
        with self._lock:
            self._last_access = time.monotonic()
            return self._status

    @property
    def error(self) -> str | None:
        with self._lock:
            self._last_access = time.monotonic()
            return self._error

    @property
    def alert_error(self) -> str | None:
        with self._lock:
            self._last_access = time.monotonic()
            return self._alert_error

    @property
    def latest_alert(self) -> dict[str, Any] | None:
        with self._lock:
            self._last_access = time.monotonic()
            return self._latest_alert.copy() if self._latest_alert is not None else None

    @property
    def ready(self) -> bool:
        with self._lock:
            self._last_access = time.monotonic()
            return self._ready

    @property
    def published_at(self) -> float | None:
        with self._lock:
            self._last_access = time.monotonic()
            return self._published_at

    def get_telemetry_records(self) -> list[dict[str, Any]]:
        with self._lock:
            self._last_access = time.monotonic()
            return [record.copy() for record in self._telemetry_records]

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _on_alert(
        self,
        topic: str,
        payload: bytes,
        retain: bool = False,
        **_kwargs: object,
    ) -> None:
        if retain:
            LOGGER.info("Ignoring retained replay alert on %s", topic)
            with self._lock:
                self._last_access = time.monotonic()
            return

        alert = normalize_alert_message(
            payload,
            asset_id=self.asset_id,
            topic=topic,
        )
        with self._lock:
            if self._stop_event.is_set():
                return

            self._last_access = time.monotonic()
            self._ready = True
            self._status = "ready"
            if alert is None:
                self._alert_error = (
                    "Received an invalid alert message; the current status "
                    "could not be updated."
                )
            else:
                self._latest_alert = alert
                self._alert_error = None
                if is_fault_alert(alert):
                    self._status = "fault_detected"
                    self._stop_event.set()

    def _on_telemetry(self, topic: str, payload: bytes, **_kwargs: object) -> None:
        record = normalize_telemetry_message(
            payload,
            asset_id=self.asset_id,
            topic=topic,
        )
        if record is None:
            return

        with self._lock:
            if self._stop_event.is_set():
                return
            self._last_access = time.monotonic()
            self._telemetry_records.append(record)

    def _on_connection_interrupted(
        self,
        _connection: object,
        error: object,
        **_kwargs: object,
    ) -> None:
        if self._stop_event.is_set():
            return
        LOGGER.warning("MQTT replay connection interrupted for %s: %s", self.asset_id, error)
        self._set_status("reconnecting", str(error))

    def _on_connection_resumed(
        self,
        _connection: object,
        _return_code: object,
        _session_present: object,
        **_kwargs: object,
    ) -> None:
        if self._stop_event.is_set():
            return
        with self._lock:
            self._last_access = time.monotonic()
            self._error = None
            self._status = "ready" if self._ready else "waiting_for_alert"

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
                client_id=f"device-replay-{uuid.uuid4().hex[:8]}",
                clean_session=True,
                keep_alive_secs=30,
                on_connection_interrupted=self._on_connection_interrupted,
                on_connection_resumed=self._on_connection_resumed,
            )

            connection.connect().result()
            connected = True

            telemetry_future, _packet_id = connection.subscribe(
                topic=self.telemetry_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_telemetry,
            )
            telemetry_future.result()

            self._set_status("publishing")
            publish_future, _packet_id = connection.publish(
                topic=self.replay_topic,
                payload=json.dumps(
                    {"timestamp_utc": self.request_timestamp_utc.isoformat().replace(
                        "+00:00", "Z"
                    )}
                ),
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )
            publish_future.result()
            with self._lock:
                self._last_access = time.monotonic()
                self._published_at = time.monotonic()
                if not self._ready:
                    self._status = "waiting_for_alert"

            if self._stop_event.wait(timeout=_ALERT_SUBSCRIPTION_DELAY_SECONDS):
                return

            alerts_future, _packet_id = connection.subscribe(
                topic=self.alert_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_alert,
            )
            alerts_future.result()

            while not self._stop_event.wait(timeout=1):
                with self._lock:
                    last_access = self._last_access
                if time.monotonic() - last_access >= _IDLE_TIMEOUT_SECONDS:
                    break
        except (AwsCrtError, BotoCoreError, OSError, RuntimeError, ValueError) as error:
            LOGGER.exception("MQTT replay session failed for %s", self.asset_id)
            self._set_status("error", str(error))
        finally:
            if connected and connection is not None:
                try:
                    connection.disconnect().result()
                except (AwsCrtError, RuntimeError) as error:
                    LOGGER.exception("MQTT replay disconnect failed for %s", self.asset_id)

            with self._lock:
                failed = self._status == "error"
                fault_detected = self._status == "fault_detected"
            if not failed and not fault_detected:
                self._set_status("stopped")

    def _set_status(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self._last_access = time.monotonic()
            self._status = status
            self._error = error
