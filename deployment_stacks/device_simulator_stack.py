from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import re

from aws_cdk import (
    ArnFormat,
    Aws,
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
)
from constructs import Construct

_THING_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _parse_timestamp(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 timestamp.")

    timestamp = value.strip()
    if timestamp.endswith("Z"):
        timestamp = f"{timestamp[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be an ISO-8601 timestamp, got {value!r}."
        ) from error

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class PumpConfig:
    asset: str
    start: str
    model_history_start: str | None = None
    speed: float = 60.0
    model: str = "polar-mind"
    cpu: int = 256
    memory_mib: int = 512

    def __post_init__(self) -> None:
        if not isinstance(self.asset, str) or not _THING_NAME_PATTERN.fullmatch(
            self.asset
        ):
            raise ValueError(
                f"Invalid pump asset/Thing name {self.asset!r}; use 1-128 "
                "letters, digits, hyphens, or underscores."
            )

        start = _parse_timestamp(self.start, f"{self.asset}.start")
        if self.model_history_start is not None:
            history_start = _parse_timestamp(
                self.model_history_start,
                f"{self.asset}.model_history_start",
            )
            if history_start > start:
                raise ValueError(
                    f"{self.asset}.model_history_start must be at or before start."
                )

        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError(f"{self.asset}.model must be a non-empty string.")
        if self.model == "none" and self.model_history_start is not None:
            raise ValueError(
                f"{self.asset}.model_history_start requires an enabled model."
            )
        if not isinstance(self.speed, (int, float)) or isinstance(self.speed, bool):
            raise ValueError(f"{self.asset}.speed must be a non-negative number.")
        if not math.isfinite(self.speed) or self.speed < 0:
            raise ValueError(f"{self.asset}.speed must be a non-negative number.")
        if type(self.cpu) is not int or self.cpu <= 0:
            raise ValueError(f"{self.asset}.cpu must be a positive Fargate CPU unit value.")
        if type(self.memory_mib) is not int or self.memory_mib <= 0:
            raise ValueError(f"{self.asset}.memory_mib must be a positive integer.")


class PumpSimulatorStack(Stack):
    """Deploy one resource-limited Fargate service for each configured pump."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        pumps: Sequence[PumpConfig],
        architecture: str = "ARM64",
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not pumps:
            raise ValueError("At least one pump must be configured.")
        normalized_assets = [pump.asset.casefold() for pump in pumps]
        if len(normalized_assets) != len(set(normalized_assets)):
            raise ValueError("Pump asset names must be unique.")

        if not isinstance(architecture, str):
            raise ValueError("architecture must be either 'ARM64' or 'X86_64'.")
        architecture = architecture.upper()
        architectures = {
            "ARM64": (
                ecs.CpuArchitecture.ARM64,
                ecr_assets.Platform.LINUX_ARM64,
            ),
            "X86_64": (
                ecs.CpuArchitecture.X86_64,
                ecr_assets.Platform.LINUX_AMD64,
            ),
        }
        try:
            task_architecture, image_platform = architectures[architecture]
        except KeyError as error:
            raise ValueError(
                "architecture must be either 'ARM64' or 'X86_64'."
            ) from error

        repository_root = Path(__file__).resolve().parents[1]
        image_asset = ecr_assets.DockerImageAsset(
            self,
            "DeviceSimulatorImage",
            directory=str(repository_root),
            file="deployment_stacks/Dockerfile.device_simulator",
            platform=image_platform,
        )

        vpc = ec2.Vpc(
            self,
            "PumpSimulatorVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                )
            ],
        )
        cluster = ecs.Cluster(self, "PumpSimulatorCluster", vpc=vpc)
        security_group = ec2.SecurityGroup(
            self,
            "PumpSimulatorSecurityGroup",
            vpc=vpc,
            description="Outbound-only network access for pump simulator tasks.",
            allow_all_outbound=True,
        )

        CfnOutput(
            self,
            "ClusterName",
            value=cluster.cluster_name,
            description="ECS cluster hosting the pump simulator tasks.",
        )
        CfnOutput(
            self,
            "VpcId",
            value=vpc.vpc_id,
            description="VPC for the outbound-only simulator tasks.",
        )

        for index, pump in enumerate(pumps, start=1):
            self._add_pump_service(
                index=index,
                pump=pump,
                image=ecs.ContainerImage.from_docker_image_asset(image_asset),
                vpc=vpc,
                cluster=cluster,
                security_group=security_group,
                task_architecture=task_architecture,
            )

    def _add_pump_service(
        self,
        *,
        index: int,
        pump: PumpConfig,
        image: ecs.ContainerImage,
        vpc: ec2.IVpc,
        cluster: ecs.ICluster,
        security_group: ec2.ISecurityGroup,
        task_architecture: ecs.CpuArchitecture,
    ) -> None:
        suffix = re.sub(r"[^A-Za-z0-9]", "", pump.asset)
        service_name = f"pump-simulator-{pump.asset.lower()}"
        task_role = iam.Role(
            self,
            f"Pump{index}TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description=f"IoT permissions for simulator {pump.asset}.",
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:CreateThing"],
                resources=[self._iot_arn("thing", pump.asset)],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:DescribeEndpoint"],
                resources=["*"],
            )
        )

        publish_topics = [
            self._iot_arn("topic", f"devices/{pump.asset}/telemetry"),
        ]
        alert_topic: str | None = None
        if pump.model != "none":
            alert_topic = self._iot_arn("topic", f"devices/{pump.asset}/alerts")
            publish_topics.append(alert_topic)
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:Publish"],
                resources=publish_topics,
            )
        )
        if alert_topic is not None:
            task_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["iot:RetainPublish"],
                    resources=[alert_topic],
                )
            )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:Connect"],
                resources=[self._iot_arn("client", "device-simulator-*")],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:Subscribe"],
                resources=[
                    self._iot_arn(
                        "topicfilter",
                        f"devices/{pump.asset}/replay",
                    )
                ],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:Receive"],
                resources=[
                    self._iot_arn("topic", f"devices/{pump.asset}/replay")
                ],
            )
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            f"Pump{index}TaskDefinition",
            family=service_name,
            cpu=pump.cpu,
            memory_limit_mib=pump.memory_mib,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=task_architecture,
            ),
            task_role=task_role,
        )

        command = [
            "--data",
            "/app/data/full",
            "--asset",
            pump.asset,
            "--listen-for-replay",
            "--loop",
        ]
        if pump.model_history_start is not None:
            command.extend(
                ["--model-history-start", pump.model_history_start]
            )
        command.extend(
            [
                "--start",
                pump.start,
                "--speed",
                f"{pump.speed:g}",
                "--model",
                pump.model,
                "--region",
                Aws.REGION,
            ]
        )

        log_group = logs.LogGroup(
            self,
            f"Pump{index}LogGroup",
            log_group_name=f"/aws/ecs/pump-simulator/{pump.asset}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        task_definition.add_container(
            f"Pump{index}Container",
            image=image,
            cpu=pump.cpu,
            memory_limit_mib=pump.memory_mib,
            command=command,
            logging=ecs.LogDrivers.aws_logs(
                log_group=log_group,
                stream_prefix=pump.asset,
            ),
        )

        service = ecs.FargateService(
            self,
            f"Pump{index}Service",
            service_name=service_name,
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC,
            ),
            security_groups=[security_group],
            min_healthy_percent=0,
            max_healthy_percent=100,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )
        service.node.add_dependency(log_group)

        CfnOutput(
            self,
            f"Pump{index}ServiceName",
            value=service.service_name,
            description=f"ECS service for {pump.asset}.",
        )
        CfnOutput(
            self,
            f"Pump{index}TaskDefinitionArn",
            value=task_definition.task_definition_arn,
            description=f"Task definition for {pump.asset}.",
        )
        CfnOutput(
            self,
            f"Pump{index}LogGroupName",
            value=log_group.log_group_name,
            description=f"CloudWatch log group for {pump.asset}.",
        )

    def _iot_arn(self, resource: str, resource_name: str) -> str:
        return self.format_arn(
            service="iot",
            resource=resource,
            resource_name=resource_name,
            arn_format=ArnFormat.SLASH_RESOURCE_NAME,
        )
