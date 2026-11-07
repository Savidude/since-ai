import pytest
from botocore.exceptions import ClientError

from device_simulator.provisioning import AwsIotProvisioner


class FakeIotClient:
    def __init__(self, thing_exists: bool = False) -> None:
        self.thing_exists = thing_exists
        self.calls: list[str] = []

    def create_thing(self, **kwargs: str) -> dict[str, str]:
        assert kwargs == {"thingName": "sensor-1"}
        self.calls.append("create_thing")
        if self.thing_exists:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ResourceAlreadyExistsException",
                        "Message": "Thing already exists",
                    }
                },
                "CreateThing",
            )
        return {"thingName": "sensor-1"}

    def describe_endpoint(self, **kwargs: str) -> dict[str, str]:
        assert kwargs == {"endpointType": "iot:Data-ATS"}
        self.calls.append("describe_endpoint")
        return {"endpointAddress": "example-ats.iot.us-east-1.amazonaws.com"}


def test_provisioner_creates_thing_and_returns_topic() -> None:
    client = FakeIotClient()
    provisioner = AwsIotProvisioner(
        iot_client=client,  # type: ignore[arg-type]
        region="us-east-1",
    )

    device = provisioner.ensure_device("sensor-1")

    assert device.thing_name == "sensor-1"
    assert device.endpoint == "example-ats.iot.us-east-1.amazonaws.com"
    assert device.topic == "devices/sensor-1/telemetry"
    assert client.calls == ["create_thing", "describe_endpoint"]


def test_provisioner_reuses_existing_thing() -> None:
    client = FakeIotClient(thing_exists=True)
    provisioner = AwsIotProvisioner(
        iot_client=client,  # type: ignore[arg-type]
        region="us-east-1",
    )

    device = provisioner.ensure_device("sensor-1")

    assert device.thing_name == "sensor-1"
    assert client.calls == ["create_thing", "describe_endpoint"]


def test_provisioner_rejects_invalid_thing_name() -> None:
    client = FakeIotClient()
    provisioner = AwsIotProvisioner(
        iot_client=client,  # type: ignore[arg-type]
        region="us-east-1",
    )

    with pytest.raises(ValueError, match="Thing name"):
        provisioner.ensure_device("../sensor")

    assert client.calls == []
