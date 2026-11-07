from __future__ import annotations

import argparse
import logging
import math
from threading import Event, Thread
from typing import Sequence

from awscrt.exceptions import AwsCrtError
import boto3
from botocore.exceptions import BotoCoreError

from .predictors import available_predictors, create_predictor
from .provisioning import AwsIotProvisioner
from .publisher import publish_replay_telemetry
from .replay import DEFAULT_START
from .replay_listener import iter_replay_commands, parse_replay_start

LOGGER = logging.getLogger("device_simulator")


def _nonnegative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative number") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite, non-negative number")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create an AWS IoT Core Thing if needed, replay stored telemetry, and optionally "
            "listen for replay commands."
        )
    )
    parser.add_argument(
        "--thing-name",
        "--asset",
        dest="thing_name",
        required=True,
        help="AWS IoT Thing name; use --asset as an alias when replaying an asset.",
    )
    parser.add_argument(
        "--region",
        help="AWS region (defaults to the configured boto3 region).",
    )
    parser.add_argument(
        "--data",
        required=True,
        help="Telemetry dataset directory to replay (for example, data/full).",
    )
    parser.add_argument(
        "--listen-for-replay",
        action="store_true",
        help=(
            "Replay the dataset while listening for requests on devices/<thing-name>/replay."
        ),
    )
    parser.add_argument(
        "--start",
        default=DEFAULT_START,
        help=(
            f"Replay start timestamp, inclusive (default: {DEFAULT_START}; "
            "used for the initial replay while listening; command timestamps override it)."
        ),
    )
    parser.add_argument(
        "--end",
        help="Optional replay end timestamp, exclusive.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of published minute messages.",
    )
    parser.add_argument(
        "--speed",
        type=_nonnegative_float,
        default=1.0,
        help="Replay speed factor (0 = as fast as possible, 1 = one minute per minute).",
    )
    parser.add_argument(
        "--model",
        choices=available_predictors(),
        default="polar-mind",
        help="Prediction adapter to run (default: polar-mind; use none to disable).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    session = boto3.Session(region_name=args.region)
    region = session.region_name
    if not region:
        parser.error(
            "No AWS region configured; pass --region or configure a default region."
        )

    try:
        provisioner = AwsIotProvisioner(
            iot_client=session.client("iot", region_name=region),
            region=region,
        )
        device = provisioner.ensure_device(args.thing_name)
        iot_data_client = session.client(
            "iot-data",
            region_name=region,
            endpoint_url=f"https://{device.endpoint}",
        )
        LOGGER.info(
            "Device ready: thing=%s region=%s topic=%s",
            device.thing_name,
            device.region,
            device.topic,
        )
        if args.listen_for_replay:
            credentials = session.get_credentials()
            if credentials is None:
                raise ValueError(
                    "No AWS credentials available for the replay listener."
                )
            listener_stop_event = Event()
            replay_errors: list[Exception] = []
            initial_replay_client = session.client(
                "iot-data",
                region_name=region,
                endpoint_url=f"https://{device.endpoint}",
            )
            replay_stop_event: Event | None = None
            replay_thread: Thread | None = None

            def stop_active_replay() -> None:
                nonlocal replay_stop_event, replay_thread
                if replay_stop_event is not None:
                    replay_stop_event.set()
                if replay_thread is not None:
                    replay_thread.join()
                replay_stop_event = None
                replay_thread = None

            def start_replay(start: str) -> None:
                nonlocal replay_stop_event, replay_thread
                stop_active_replay()
                if replay_errors:
                    return

                replay_stop_event = Event()
                current_stop_event = replay_stop_event

                def publish_replay() -> None:
                    try:
                        predictor = create_predictor(
                            args.model,
                            data_dir=args.data,
                            asset=args.thing_name,
                        )
                        publish_replay_telemetry(
                            initial_replay_client,
                            device,
                            data_dir=args.data,
                            asset=args.thing_name,
                            start=start,
                            end=args.end,
                            limit=args.limit,
                            speed=args.speed,
                            stop_event=current_stop_event,
                            predictor=predictor,
                        )
                    except (BotoCoreError, OSError, ValueError) as error:
                        replay_errors.append(error)
                        listener_stop_event.set()

                replay_thread = Thread(
                    target=publish_replay,
                    name="device-simulator-replay",
                )
                replay_thread.start()

            try:
                for payload in iter_replay_commands(
                    endpoint=device.endpoint,
                    region=device.region,
                    thing_name=device.thing_name,
                    credentials=credentials,
                    stop_event=listener_stop_event,
                    on_ready=lambda: start_replay(args.start),
                ):
                    try:
                        start = parse_replay_start(payload)
                    except ValueError as error:
                        LOGGER.error("Ignoring invalid replay command: %s", error)
                        continue
                    LOGGER.info("Received replay request starting at %s", start)
                    start_replay(start)
            finally:
                listener_stop_event.set()
                stop_active_replay()
            if replay_errors:
                raise replay_errors[0]
        else:
            publish_replay_telemetry(
                iot_data_client,
                device,
                data_dir=args.data,
                asset=args.thing_name,
                start=args.start,
                end=args.end,
                limit=args.limit,
                speed=args.speed,
                predictor=create_predictor(
                    args.model,
                    data_dir=args.data,
                    asset=args.thing_name,
                ),
            )
    except KeyboardInterrupt:
        LOGGER.info("Stopped.")
        return 130
    except (AwsCrtError, BotoCoreError, OSError, ValueError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0
