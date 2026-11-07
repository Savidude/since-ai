from __future__ import annotations

import re
from dataclasses import dataclass

from botocore.client import BaseClient
from botocore.exceptions import ClientError

_THING_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ALREADY_EXISTS = "ResourceAlreadyExistsException"


@dataclass(frozen=True)
class Device:
    thing_name: str
    region: str
    endpoint: str
    topic: str


class AwsIotProvisioner:
    def __init__(self, *, iot_client: BaseClient, region: str) -> None:
        self._iot = iot_client
        self._region = region

    def ensure_device(self, thing_name: str) -> Device:
        if not _THING_NAME_PATTERN.fullmatch(thing_name):
            raise ValueError(
                "Thing name must be 1-128 characters and contain only "
                "letters, digits, hyphens, or underscores."
            )

        try:
            self._iot.create_thing(thingName=thing_name)
        except ClientError as error:
            if error.response["Error"]["Code"] != _ALREADY_EXISTS:
                raise

        endpoint = self._iot.describe_endpoint(
            endpointType="iot:Data-ATS"
        )["endpointAddress"]
        return Device(
            thing_name=thing_name,
            region=self._region,
            endpoint=endpoint,
            topic=f"devices/{thing_name}/telemetry",
        )
