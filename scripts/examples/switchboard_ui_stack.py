"""AWS infrastructure for the public Switchboard AI Streamlit demo."""

from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    CfnParameter,
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
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SwitchboardUiStack(Stack):
    """Deploy the Streamlit UI on one public Fargate task behind an ALB.

    The task is placed in public subnets to avoid a NAT gateway for this
    one-time demo. Its security group only accepts application traffic from
    the load balancer; the public IP is used for outbound OpenAI and Bedrock
    calls.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        openai_secret_arn = CfnParameter(
            self,
            "OpenAiSecretArn",
            type="String",
            no_echo=True,
            description=(
                "ARN of a Secrets Manager secret containing the plain-text "
                "OpenAI API key."
            ),
        )
        openai_secret = secretsmanager.Secret.from_secret_complete_arn(
            self,
            "OpenAiSecret",
            openai_secret_arn.value_as_string,
        )

        image_asset = ecr_assets.DockerImageAsset(
            self,
            "SwitchboardUiImage",
            directory=str(PROJECT_ROOT),
            file="deployment_stacks/Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        vpc = ec2.Vpc(
            self,
            "SwitchboardVpc",
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

        cluster = ecs.Cluster(
            self,
            "SwitchboardCluster",
            vpc=vpc,
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "SwitchboardTaskDefinition",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        container = task_definition.add_container(
            "SwitchboardUiContainer",
            image=ecs.ContainerImage.from_docker_image_asset(image_asset),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="switchboard-ui",
                log_retention=logs.RetentionDays.ONE_WEEK,
            ),
            environment={
                "AWS_REGION": self.region,
                "AWS_DEFAULT_REGION": self.region,
                "STREAMLIT_SERVER_HEADLESS": "true",
            },
        )
        container.add_port_mappings(
            ecs.PortMapping(
                container_port=8501,
                protocol=ecs.Protocol.TCP,
            )
        )
        container.add_secret(
            "OPENAI_API_KEY",
            ecs.Secret.from_secrets_manager(openai_secret),
        )

        # The ECS agent reads Secrets Manager values using the execution role.
        openai_secret.grant_read(task_definition.execution_role)

        # ChatBedrockConverse uses the task role. Resource "*" keeps this
        # stack deployable for the configured regional model; narrow it to a
        # specific model ARN for a longer-lived deployment.
        task_definition.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )

        certificate, domain_name, hosted_zone = self._custom_domain()
        use_https = certificate is not None
        route53_domain_name = domain_name if hosted_zone is not None else None

        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "SwitchboardService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            public_load_balancer=True,
            assign_public_ip=True,
            task_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC,
            ),
            min_healthy_percent=100,
            max_healthy_percent=200,
            listener_port=443 if use_https else 80,
            protocol=(
                elbv2.ApplicationProtocol.HTTPS
                if use_https
                else elbv2.ApplicationProtocol.HTTP
            ),
            certificate=certificate,
            redirect_http=use_https,
            domain_name=route53_domain_name,
            domain_zone=hosted_zone,
            health_check_grace_period=Duration.seconds(120),
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
        # Streamlit keeps a WebSocket open. A longer ALB idle timeout avoids
        # disconnecting an otherwise active demo session.
        service.load_balancer.set_attribute(
            "idle_timeout.timeout_seconds",
            "3600",
        )

        load_balancer_url = (
            f"https://{domain_name}"
            if domain_name and use_https
            else f"http://{service.load_balancer.load_balancer_dns_name}"
        )

        CfnOutput(
            self,
            "ApplicationUrl",
            value=load_balancer_url,
            description="Public URL for the Switchboard AI Streamlit application.",
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

    def _custom_domain(
        self,
    ) -> tuple[
        acm.ICertificate | None,
        str | None,
        route53.IHostedZone | None,
    ]:
        """Return optional DNS/TLS resources configured through CDK context.

        When ``certificateArn`` is supplied without Route 53 zone values, DNS
        is managed externally. In that mode the certificate is attached to the
        ALB, but the ECS pattern does not attempt to create a Route 53 record.
        """

        domain_name = self.node.try_get_context("domainName")
        hosted_zone_id = self.node.try_get_context("hostedZoneId")
        hosted_zone_name = self.node.try_get_context("hostedZoneName")
        certificate_arn = self.node.try_get_context("certificateArn")

        if certificate_arn and not hosted_zone_id and not hosted_zone_name:
            if not domain_name:
                raise ValueError(
                    "domainName is required when certificateArn is used "
                    "with external DNS."
                )
            certificate = acm.Certificate.from_certificate_arn(
                self,
                "Certificate",
                str(certificate_arn),
            )
            return certificate, str(domain_name), None

        values = {
            "domainName": domain_name,
            "hostedZoneId": hosted_zone_id,
            "hostedZoneName": hosted_zone_name,
            "certificateArn": certificate_arn,
        }
        supplied = {key for key, value in values.items() if value}
        if not supplied:
            return None, None, None

        required = {"domainName", "hostedZoneId", "hostedZoneName"}
        missing = required - supplied
        if missing:
            missing_values = ", ".join(sorted(missing))
            raise ValueError(
                "Custom-domain deployment requires CDK context values: "
                f"{missing_values}."
            )

        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=str(hosted_zone_id),
            zone_name=str(hosted_zone_name),
        )

        if certificate_arn:
            certificate: acm.ICertificate = acm.Certificate.from_certificate_arn(
                self,
                "Certificate",
                str(certificate_arn),
            )
        else:
            certificate = acm.Certificate(
                self,
                "Certificate",
                domain_name=str(domain_name),
                validation=acm.CertificateValidation.from_dns(hosted_zone),
            )

        return certificate, str(domain_name), hosted_zone
