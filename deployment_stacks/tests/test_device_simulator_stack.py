from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template
import pytest

from device_simulator_stack import PumpConfig, PumpSimulatorStack


def _stack(tmp_path: Path) -> PumpSimulatorStack:
    app = cdk.App(outdir=str(tmp_path / "cdk.out"))
    return PumpSimulatorStack(
        app,
        "TestPumpSimulatorStack",
        architecture="ARM64",
        pumps=[
            PumpConfig(
                asset="PUMP-06",
                start="2025-10-09T21:45:00Z",
                model_history_start="2025-10-07T13:50:00Z",
                speed=60,
                cpu=256,
                memory_mib=512,
            ),
            PumpConfig(
                asset="PUMP-07",
                start="2025-05-13T00:00:00Z",
                model_history_start=None,
                speed=30,
                cpu=512,
                memory_mib=1024,
            ),
        ],
    )


def test_stack_creates_independent_arm_fargate_service_per_pump(
    tmp_path: Path,
) -> None:
    template = Template.from_stack(_stack(tmp_path))

    template.resource_count_is("AWS::ECS::Cluster", 1)
    template.resource_count_is("AWS::ECS::Service", 2)
    template.resource_count_is("AWS::ECS::TaskDefinition", 2)
    task_definitions = template.find_resources("AWS::ECS::TaskDefinition")
    task_properties = [resource["Properties"] for resource in task_definitions.values()]
    assert {
        (properties["Cpu"], properties["Memory"])
        for properties in task_properties
    } == {("256", "512"), ("512", "1024")}
    assert all(
        properties["RuntimePlatform"]
        == {
            "CpuArchitecture": "ARM64",
            "OperatingSystemFamily": "LINUX",
        }
        for properties in task_properties
    )

    services = template.find_resources("AWS::ECS::Service")
    for resource in services.values():
        properties = resource["Properties"]
        assert properties["DesiredCount"] == 1
        assert properties["LaunchType"] == "FARGATE"
        assert properties["NetworkConfiguration"]["AwsvpcConfiguration"][
            "AssignPublicIp"
        ] == "ENABLED"


def test_each_task_has_its_pump_specific_command_and_role(
    tmp_path: Path,
) -> None:
    template = Template.from_stack(_stack(tmp_path))
    task_definitions = template.find_resources("AWS::ECS::TaskDefinition")
    commands = [
        resource["Properties"]["ContainerDefinitions"][0]["Command"]
        for resource in task_definitions.values()
    ]

    pump_06 = next(command for command in commands if "PUMP-06" in command)
    pump_07 = next(command for command in commands if "PUMP-07" in command)
    assert pump_06[pump_06.index("--model-history-start") + 1] == (
        "2025-10-07T13:50:00Z"
    )
    assert pump_06[pump_06.index("--start") + 1] == "2025-10-09T21:45:00Z"
    assert "--model-history-start" not in pump_07
    assert pump_07[pump_07.index("--start") + 1] == "2025-05-13T00:00:00Z"
    assert all("--listen-for-replay" in command for command in commands)
    assert all(command[command.index("--data") + 1] == "/app/data/full" for command in commands)
    assert all(command[command.index("--region") + 1] == {"Ref": "AWS::Region"} for command in commands)

    template.resource_count_is("AWS::IAM::Role", 4)


def test_pump_config_rejects_history_after_replay_start() -> None:
    with pytest.raises(ValueError, match="must be at or before start"):
        PumpConfig(
            asset="PUMP-06",
            start="2025-10-09T21:45:00Z",
            model_history_start="2025-10-10T00:00:00Z",
        )


def test_dockerfile_uses_main_simulator_and_embeds_full_dataset() -> None:
    dockerfile = (
        Path(__file__).parents[1] / "Dockerfile.device_simulator"
    ).read_text(encoding="utf-8")

    assert "COPY challenge_description/data/full ./data/full" in dockerfile
    assert 'ENTRYPOINT ["device-simulator"]' in dockerfile
    assert "device-simulator-scan-alerts" not in dockerfile
