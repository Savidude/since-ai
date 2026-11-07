import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from telemetry_ingestion_stack import TelemetryIngestionStack


def test_stack_creates_hardened_raw_bucket_and_firehose() -> None:
    app = cdk.App()
    stack = TelemetryIngestionStack(app, "TestTelemetryIngestionStack")
    template = Template.from_stack(stack)

    template.resource_count_is("AWS::S3::Bucket", 1)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "VersioningConfiguration": {"Status": "Enabled"},
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "ServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        }
                    }
                ]
            },
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )
    template.resource_count_is("AWS::KinesisFirehose::DeliveryStream", 1)
    template.has_resource_properties(
        "AWS::KinesisFirehose::DeliveryStream",
        {
            "DeliveryStreamType": "DirectPut",
            "S3DestinationConfiguration": Match.object_like(
                {
                    "BufferingHints": {
                        "IntervalInSeconds": 60,
                        "SizeInMBs": 5,
                    },
                    "CompressionFormat": "GZIP",
                    "Prefix": Match.string_like_regexp(r"^raw/ingest_year="),
                }
            ),
        },
    )


def test_iot_rule_adds_asset_id_and_sends_newline_delimited_records() -> None:
    app = cdk.App()
    stack = TelemetryIngestionStack(app, "TestTelemetryIngestionStack")
    template = Template.from_stack(stack)

    template.resource_count_is("AWS::IoT::TopicRule", 1)
    template.has_resource_properties(
        "AWS::IoT::TopicRule",
        {
            "TopicRulePayload": Match.object_like(
                {
                    "AwsIotSqlVersion": "2016-03-23",
                    "Sql": (
                        "SELECT *, topic(2) AS asset_id "
                        "FROM 'devices/+/telemetry'"
                    ),
                    "Actions": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Firehose": Match.object_like(
                                        {"Separator": "\n"}
                                    )
                                }
                            )
                        ]
                    ),
                }
            )
        },
    )
