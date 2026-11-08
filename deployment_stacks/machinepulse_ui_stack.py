"""AWS infrastructure for the public MachinePulse Streamlit demo."""

from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    ArnFormat,
    CfnOutput,
    Duration,
    Stack,
    aws_certificatemanager as acm,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_route53 as route53,
)
from constructs import Construct


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUMP_IDS = tuple(f"PUMP-{index:02d}" for index in range(1, 13))
MIN_TASK_COUNT = 2
MAX_TASK_COUNT = 4
TASK_CPU = 1024
TASK_MEMORY_MIB = 2048
CONTAINER_PORT = 8501


class MachinePulseUiStack(Stack):
    """Deploy MachinePulse behind a resilient, HTTPS-enabled Fargate service."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        domain_name, hosted_zone, certificate = self._custom_domain()

        image_asset = ecr_assets.DockerImageAsset(
            self,
            "MachinePulseUiImage",
            directory=str(PROJECT_ROOT),
            file="deployment_stacks/Dockerfile.machinepulse",
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        vpc = ec2.Vpc(
            self,
            "MachinePulseVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )
        cluster = ecs.Cluster(self, "MachinePulseCluster", vpc=vpc)

        task_definition = ecs.FargateTaskDefinition(
            self,
            "MachinePulseTaskDefinition",
            cpu=TASK_CPU,
            memory_limit_mib=TASK_MEMORY_MIB,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        container = task_definition.add_container(
            "MachinePulseUiContainer",
            image=ecs.ContainerImage.from_docker_image_asset(image_asset),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="machinepulse-ui",
                log_retention=logs.RetentionDays.ONE_WEEK,
            ),
            environment={
                "AWS_REGION": self.region,
                "AWS_DEFAULT_REGION": self.region,
                "STREAMLIT_SERVER_HEADLESS": "true",
                "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
            },
        )
        container.add_port_mappings(
            ecs.PortMapping(
                container_port=CONTAINER_PORT,
                protocol=ecs.Protocol.TCP,
            )
        )
        self._grant_iot_access(task_definition.task_role)

        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "MachinePulseService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=MIN_TASK_COUNT,
            public_load_balancer=True,
            assign_public_ip=True,
            task_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC,
            ),
            min_healthy_percent=100,
            max_healthy_percent=150,
            listener_port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificate=certificate,
            redirect_http=True,
            domain_name=domain_name,
            domain_zone=hosted_zone,
            health_check_grace_period=Duration.seconds(120),
            idle_timeout=Duration.hours(1),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )
        service.target_group.configure_health_check(
            path="/",
            healthy_http_codes="200-399",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(10),
            healthy_threshold_count=2,
            unhealthy_threshold_count=5,
        )
        service.target_group.enable_cookie_stickiness(Duration.hours(24))

        task_scaling = service.service.auto_scale_task_count(
            min_capacity=MIN_TASK_COUNT,
            max_capacity=MAX_TASK_COUNT,
        )
        task_scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=60,
            scale_in_cooldown=Duration.seconds(180),
            scale_out_cooldown=Duration.seconds(60),
        )
        task_scaling.scale_on_memory_utilization(
            "MemoryScaling",
            target_utilization_percent=70,
            scale_in_cooldown=Duration.seconds(180),
            scale_out_cooldown=Duration.seconds(60),
        )

        CfnOutput(
            self,
            "ApplicationUrl",
            value=f"https://{domain_name}",
            description="Public HTTPS URL for the MachinePulse application.",
        )
        CfnOutput(
            self,
            "LoadBalancerDnsName",
            value=service.load_balancer.load_balancer_dns_name,
        )
        CfnOutput(
            self,
            "EcrRepositoryName",
            value=image_asset.repository.repository_name,
        )
        CfnOutput(
            self,
            "EcsClusterName",
            value=cluster.cluster_name,
        )
        CfnOutput(
            self,
            "EcsServiceName",
            value=service.service.service_name,
        )

    def _custom_domain(
        self,
    ) -> tuple[str, route53.IHostedZone, acm.ICertificate]:
        values = {
            "domainName": self.node.try_get_context("domainName"),
            "hostedZoneId": self.node.try_get_context("hostedZoneId"),
            "hostedZoneName": self.node.try_get_context("hostedZoneName"),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            missing_values = ", ".join(sorted(missing))
            raise ValueError(
                "MachinePulseUiStack requires CDK context values: "
                f"{missing_values}."
            )

        domain_name = str(values["domainName"])
        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=str(values["hostedZoneId"]),
            zone_name=str(values["hostedZoneName"]),
        )
        certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )
        return domain_name, hosted_zone, certificate

    def _grant_iot_access(self, task_role: iam.IRole) -> None:
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:DescribeEndpoint"],
                resources=["*"],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:Connect"],
                resources=[
                    self._iot_arn("client", "device-monitor-*"),
                    self._iot_arn("client", "device-replay-*"),
                ],
            )
        )

        telemetry_topics = [
            f"devices/{pump_id}/telemetry" for pump_id in PUMP_IDS
        ]
        alert_topics = [f"devices/{pump_id}/alerts" for pump_id in PUMP_IDS]
        replay_topics = [f"devices/{pump_id}/replay" for pump_id in PUMP_IDS]
        subscribed_topics = telemetry_topics + alert_topics

        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:Subscribe"],
                resources=[
                    self._iot_arn("topicfilter", topic)
                    for topic in subscribed_topics
                ],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:Receive"],
                resources=[
                    self._iot_arn("topic", topic) for topic in subscribed_topics
                ],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iot:Publish"],
                resources=[
                    self._iot_arn("topic", topic) for topic in replay_topics
                ],
            )
        )

    def _iot_arn(self, resource_type: str, resource_name: str) -> str:
        return Stack.format_arn(
            self,
            service="iot",
            resource=resource_type,
            resource_name=resource_name,
            arn_format=ArnFormat.SLASH_RESOURCE_NAME,
        )
