from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from threading import Event

from botocore.client import BaseClient

from .provisioning import Device
from .replay import DEFAULT_START, iter_replay_payloads

LOGGER = logging.getLogger(__name__)


def publish_replay_telemetry(
    iot_data_client: BaseClient,
    device: Device,
    *,
    data_dir: str | Path,
    asset: str,
    start: str | None = DEFAULT_START,
    end: str | None = None,
    limit: int | None = None,
    speed: float = 1.0,
    stop_event: Event | None = None,
) -> int:
    if not math.isfinite(speed) or speed < 0:
        raise ValueError("speed must be a finite, non-negative number.")

    LOGGER.info("Replaying telemetry as %s to %s", device.thing_name, device.topic)
    previous_timestamp: datetime | None = None
    published = 0
    for payload in iter_replay_payloads(data_dir, asset, start, end, limit):
        if stop_event is not None and stop_event.is_set():
            break
        if speed > 0:
            timestamp_text = str(payload["timestamp_utc"])
            if timestamp_text.endswith("Z"):
                timestamp_text = f"{timestamp_text[:-1]}+00:00"
            timestamp = datetime.fromisoformat(timestamp_text)
            if previous_timestamp is not None:
                delay = (timestamp - previous_timestamp).total_seconds() / speed
                delay = max(0.0, delay)
                if stop_event is None:
                    time.sleep(delay)
                elif stop_event.wait(delay):
                    break
            previous_timestamp = timestamp

        iot_data_client.publish(
            topic=device.topic,
            payload=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            qos=1,
        )
        published += 1
        LOGGER.info("Published replay telemetry message %d", published)

    LOGGER.info("Replayed %d telemetry messages", published)
    return published
