from __future__ import annotations

import argparse
from contextlib import nullcontext
import logging
import re
import uuid
from pathlib import Path
from typing import Sequence

import boto3
from awscrt.exceptions import AwsCrtError
from botocore.exceptions import BotoCoreError

from .monitor import MonitorError, create_credentials_provider, monitor_topic

LOGGER = logging.getLogger("device_monitor")
_THING_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Subscribe to an AWS IoT Core device's telemetry and alerts topics "
            "and print messages."
        )
    )
    parser.add_argument(
        "--thing-name",
        "--asset",
        dest="thing_name",
        default="demo-device",
        help="Device Thing name (or asset ID; default: demo-device).",
    )
    parser.add_argument(
        "--region",
        help="AWS region (defaults to the configured boto3 region).",
    )
    parser.add_argument(
        "--profile",
        help="AWS profile name (defaults to the standard boto3 credential chain).",
    )
    parser.add_argument(
        "--topic",
        choices=("both", "telemetry", "alerts"),
        default="both",
        help="Topic to subscribe to (default: both).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--capture",
        type=Path,
        metavar="PATH",
        help="Append received messages as JSON Lines to this file.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not _THING_NAME_PATTERN.fullmatch(args.thing_name):
        parser.error(
            "--thing-name must contain 1-128 letters, digits, hyphens, or underscores."
        )

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    region = session.region_name
    if not region:
        parser.error("No AWS region configured; pass --region or configure a default region.")

    try:
        iot_client = session.client("iot", region_name=region)
        endpoint = iot_client.describe_endpoint(
            endpointType="iot:Data-ATS"
        )["endpointAddress"]
        credentials_provider = create_credentials_provider(session)
        telemetry_topic = f"devices/{args.thing_name}/telemetry"
        alerts_topic = f"devices/{args.thing_name}/alerts"
        topics = {
            "both": (telemetry_topic, alerts_topic),
            "telemetry": (telemetry_topic,),
            "alerts": (alerts_topic,),
        }[args.topic]
        client_id = f"device-monitor-{uuid.uuid4().hex[:8]}"
        capture_context = (
            args.capture.open("a", encoding="utf-8")
            if args.capture is not None
            else nullcontext()
        )
        with capture_context as capture_file:
            monitor_topic(
                endpoint=endpoint,
                region=region,
                topic=topics[0],
                client_id=client_id,
                credentials_provider=credentials_provider,
                capture_file=capture_file,
                additional_topics=topics[1:],
            )
    except KeyboardInterrupt:
        LOGGER.info("Stopped.")
        return 130
    except (AwsCrtError, BotoCoreError, MonitorError, OSError, ValueError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0
