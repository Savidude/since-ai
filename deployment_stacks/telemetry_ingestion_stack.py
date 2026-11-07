from __future__ import annotations

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_iot as iot
from aws_cdk import aws_kinesisfirehose as firehose
from aws_cdk import aws_s3 as s3
from constructs import Construct


class TelemetryIngestionStack(Stack):
    """Archive AWS IoT telemetry in an encrypted, versioned S3 bucket."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        raw_bucket = s3.Bucket(
            self,
            "RawTelemetryBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        firehose_role = iam.Role(
            self,
            "FirehoseS3Role",
            assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"),
        )
        raw_bucket.grant_write(firehose_role, "raw/*")
        raw_bucket.grant_write(firehose_role, "errors/*")

        delivery_stream = firehose.CfnDeliveryStream(
            self,
            "TelemetryDeliveryStream",
            delivery_stream_type="DirectPut",
            s3_destination_configuration=firehose.CfnDeliveryStream.S3DestinationConfigurationProperty(
                bucket_arn=raw_bucket.bucket_arn,
                role_arn=firehose_role.role_arn,
                buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                    interval_in_seconds=60,
                    size_in_m_bs=5,
                ),
                compression_format="GZIP",
                prefix=(
                    "raw/ingest_year=!{timestamp:yyyy}/"
                    "ingest_month=!{timestamp:MM}/"
                    "ingest_day=!{timestamp:dd}/"
                ),
                error_output_prefix=(
                    "errors/!{firehose:error-output-type}/"
                    "ingest_year=!{timestamp:yyyy}/"
                    "ingest_month=!{timestamp:MM}/"
                    "ingest_day=!{timestamp:dd}/"
                ),
            ),
        )

        iot_rule_role = iam.Role(
            self,
            "IotRuleFirehoseRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com"),
        )
        iot_rule_role.add_to_policy(
            iam.PolicyStatement(
                actions=["firehose:PutRecord"],
                resources=[delivery_stream.attr_arn],
            )
        )

        topic_filter = "devices/+/telemetry"
        rule = iot.CfnTopicRule(
            self,
            "TelemetryIngestionRule",
            rule_name="TelemetryToRawS3",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                description="Send device telemetry to the raw S3 Firehose destination.",
                sql=(
                    "SELECT *, topic(2) AS asset_id "
                    "FROM 'devices/+/telemetry'"
                ),
                aws_iot_sql_version="2016-03-23",
                rule_disabled=False,
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        firehose=iot.CfnTopicRule.FirehoseActionProperty(
                            delivery_stream_name=delivery_stream.ref,
                            role_arn=iot_rule_role.role_arn,
                            separator="\n",
                        )
                    )
                ],
            ),
        )

        CfnOutput(
            self,
            "RawBucketName",
            value=raw_bucket.bucket_name,
            description="S3 bucket receiving raw, compressed telemetry records.",
        )
        CfnOutput(
            self,
            "DeliveryStreamName",
            value=delivery_stream.ref,
            description="Amazon Data Firehose stream targeted by the IoT rule.",
        )
        CfnOutput(
            self,
            "IotRuleName",
            value=rule.ref,
            description="AWS IoT rule matching device telemetry topics.",
        )
        CfnOutput(
            self,
            "TelemetryTopicFilter",
            value=topic_filter,
            description="MQTT topic filter accepted by the ingestion rule.",
        )
