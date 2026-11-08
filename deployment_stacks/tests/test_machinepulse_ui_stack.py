import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template
import pytest

from machinepulse_ui_stack import MachinePulseUiStack


_DOMAIN_CONTEXT = {
    "domainName": "demo.savidude.com",
    "hostedZoneId": "Z000000000000",
    "hostedZoneName": "demo.savidude.com",
}


def _stack(tmp_path: Path) -> MachinePulseUiStack:
    app = cdk.App(outdir=str(tmp_path / "cdk.out"))
    for key, value in _DOMAIN_CONTEXT.items():
        app.node.set_context(key, value)
    return MachinePulseUiStack(app, "TestMachinePulseUiStack")


def test_stack_requires_custom_domain_context() -> None:
    app = cdk.App()

    with pytest.raises(ValueError, match="requires CDK context values"):
        MachinePulseUiStack(app, "TestMachinePulseUiStack")


def test_stack_creates_redundant_https_fargate_service(
    tmp_path: Path,
) -> None:
    template = Template.from_stack(_stack(tmp_path))

    template.resource_count_is("AWS::ECS::Cluster", 1)
    template.resource_count_is("AWS::ECS::Service", 1)
    template.resource_count_is("AWS::ECS::TaskDefinition", 1)
    template.resource_count_is("AWS::ElasticLoadBalancingV2::LoadBalancer", 1)
    template.resource_count_is("AWS::ElasticLoadBalancingV2::Listener", 2)

    template.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {
            "Cpu": "1024",
            "Memory": "2048",
            "RuntimePlatform": {
                "CpuArchitecture": "ARM64",
                "OperatingSystemFamily": "LINUX",
            },
            "ContainerDefinitions": Match.array_with(
                [
                    Match.object_like(
                        {
                            "PortMappings": Match.array_with(
                                [
                                    Match.object_like(
                                        {
                                            "ContainerPort": 8501,
                                            "Protocol": "tcp",
                                        }
                                    )
                                ]
                            )
                        }
                    )
                ]
            ),
        },
    )
    template.has_resource_properties(
        "AWS::ECS::Service",
        {
            "DesiredCount": 2,
            "DeploymentConfiguration": {
                "MaximumPercent": 150,
                "MinimumHealthyPercent": 100,
                "DeploymentCircuitBreaker": {
                    "Enable": True,
                    "Rollback": True,
                },
            },
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {
                    "AssignPublicIp": "ENABLED",
                }
            },
        },
    )

    template.has_resource_properties(
        "AWS::ApplicationAutoScaling::ScalableTarget",
        {
            "MinCapacity": 2,
            "MaxCapacity": 4,
            "ScalableDimension": "ecs:service:DesiredCount",
            "ServiceNamespace": "ecs",
        },
    )
    template.resource_count_is("AWS::ApplicationAutoScaling::ScalingPolicy", 2)

    listeners = template.find_resources(
        "AWS::ElasticLoadBalancingV2::Listener"
    )
    listener_properties = [
        resource["Properties"] for resource in listeners.values()
    ]
    https_listener = next(
        properties for properties in listener_properties
        if properties.get("Port") == 443
    )
    assert https_listener["Protocol"] == "HTTPS"
    assert "Certificates" in https_listener

    http_listener = next(
        properties for properties in listener_properties
        if properties.get("Port") == 80
    )
    assert http_listener["DefaultActions"][0]["Type"] == "redirect"

    template.has_resource_properties(
        "AWS::CertificateManager::Certificate",
        {
            "DomainName": "demo.savidude.com",
            "ValidationMethod": "DNS",
        },
    )
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {
            "HealthCheckPath": "/",
            "Matcher": {"HttpCode": "200-399"},
        },
    )


def test_iot_task_role_is_scoped_to_machinepulse_topics(
    tmp_path: Path,
) -> None:
    template = Template.from_stack(_stack(tmp_path))
    statements = []
    for resource in template.find_resources("AWS::IAM::Policy").values():
        policy_statements = resource["Properties"]["PolicyDocument"]["Statement"]
        if not isinstance(policy_statements, list):
            policy_statements = [policy_statements]
        statements.extend(policy_statements)

    iot_permissions = {}
    for statement in statements:
        actions = statement["Action"]
        if isinstance(actions, str):
            actions = [actions]
        for action in actions:
            if action.startswith("iot:"):
                iot_permissions[action] = statement

    assert {
        "iot:DescribeEndpoint",
        "iot:Connect",
        "iot:Subscribe",
        "iot:Receive",
        "iot:Publish",
    } <= iot_permissions.keys()

    assert iot_permissions["iot:DescribeEndpoint"]["Resource"] == "*"
    assert "client/device-monitor-*" in json.dumps(
        iot_permissions["iot:Connect"]["Resource"]
    )
    assert "client/device-replay-*" in json.dumps(
        iot_permissions["iot:Connect"]["Resource"]
    )
    for action in ("iot:Subscribe", "iot:Receive", "iot:Publish"):
        resources = iot_permissions[action]["Resource"]
        assert resources != "*"
        assert "devices/PUMP-" in json.dumps(resources)


def test_dockerfile_copies_machinepulse_runtime_and_supporting_data() -> None:
    dockerfile = (
        Path(__file__).parents[1] / "Dockerfile.machinepulse"
    ).read_text(encoding="utf-8")

    assert "COPY MachinePulse/ ./MachinePulse/" in dockerfile
    assert "COPY challenge_description/src/ ./challenge_description/src/" in dockerfile
    assert "COPY device_simulator/pump_alerts/ ./device_simulator/pump_alerts/" in dockerfile
    assert '"streamlit", "run", "app.py"' in dockerfile
