from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from threading import Event

from botocore.client import BaseClient

from .predictors.base import PredictorAdapter
from .provisioning import Device
from .replay import DEFAULT_START, iter_replay_minutes

LOGGER = logging.getLogger(__name__)


def _print_prediction(prediction: dict[str, object]) -> None:
    print(f"Prediction: {json.dumps(prediction, separators=(',', ':'))}", flush=True)


def publish_replay_telemetry(
    iot_data_client: BaseClient,
    device: Device,
    *,
    data_dir: str | Path,
    asset: str,
    start: str | None = DEFAULT_START,
    model_history_start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    speed: float = 1.0,
    stop_event: Event | None = None,
    predictor: PredictorAdapter | None = None,
) -> int:
    if not math.isfinite(speed) or speed < 0:
        raise ValueError("speed must be a finite, non-negative number.")

    LOGGER.info("Replaying telemetry as %s to %s", device.thing_name, device.topic)
    if predictor is not None:
        if model_history_start is None:
            LOGGER.info("Warming predictor with up to 24 hours of prior telemetry")
        else:
            LOGGER.info("Loading predictor history from %s", model_history_start)
    previous_timestamp: datetime | None = None
    published = 0
    warmup_minutes = 0
    warmup_complete = predictor is None
    for minute in iter_replay_minutes(
        data_dir,
        asset,
        start,
        end,
        limit,
        warmup_hours=24 if predictor is not None else 0,
        history_start=model_history_start if predictor is not None else None,
    ):
        if stop_event is not None and stop_event.is_set():
            break
        if minute.is_warmup:
            if predictor is not None:
                warmup_minutes += 1
                predictor.process_minute(minute)
                if warmup_minutes % 360 == 0:
                    LOGGER.info("Predictor warm-up processed %d prior minutes", warmup_minutes)
            continue

        if not warmup_complete:
            LOGGER.info("Predictor warm-up complete after %d prior minutes", warmup_minutes)
            warmup_complete = True

        if speed > 0:
            timestamp = minute.timestamp.to_pydatetime()
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
            payload=json.dumps(minute.telemetry, separators=(",", ":")).encode("utf-8"),
            qos=1,
        )
        published += 1
        if predictor is not None:
            prediction = predictor.process_minute(minute)
            if prediction is not None:
                _print_prediction(dict(prediction))
        LOGGER.info("Published replay telemetry minute %d", published)

    LOGGER.info("Replayed %d telemetry minutes", published)
    return published
