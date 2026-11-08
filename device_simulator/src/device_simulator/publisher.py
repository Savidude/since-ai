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
_ALERT_MESSAGE_EXPIRY_SECONDS = 24 * 60 * 60


def _alert_state_signature(prediction: dict[str, object]) -> str:
    state = {
        key: value
        for key, value in prediction.items()
        if key != "minute_utc"
    }
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


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
    loop: bool = False,
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
    published = 0
    last_alert_state: str | None = None
    while stop_event is None or not stop_event.is_set():
        previous_timestamp: datetime | None = None
        replayed_this_pass = 0
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
            replayed_this_pass += 1
            if predictor is not None:
                prediction = predictor.process_minute(minute)
                if prediction is not None:
                    prediction_record = dict(prediction)
                    alert_state = _alert_state_signature(prediction_record)
                    if alert_state != last_alert_state:
                        iot_data_client.publish(
                            topic=f"devices/{device.thing_name}/alerts",
                            payload=json.dumps(
                                prediction_record, separators=(",", ":")
                            ).encode("utf-8"),
                            qos=1,
                            retain=True,
                            messageExpiry=_ALERT_MESSAGE_EXPIRY_SECONDS,
                        )
                        last_alert_state = alert_state
            LOGGER.info("Published replay telemetry minute %d", published)

        if not loop or replayed_this_pass == 0:
            break
        if stop_event is not None and stop_event.is_set():
            break
        if speed > 0:
            delay = 60 / speed
            if stop_event is None:
                time.sleep(delay)
            elif stop_event.wait(delay):
                    break

    LOGGER.info("Replayed %d telemetry minutes", published)
    return published
