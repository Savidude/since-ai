# Telemetry ingestion stack

This CDK v2 Python app deploys the raw telemetry path:

```text
AWS IoT Core → IoT Rule → Amazon Data Firehose → S3
```

The IoT rule matches `devices/+/telemetry`, copies the payload, and adds
`asset_id` from the second topic segment. Firehose writes newline-delimited
JSON records to a generated S3 bucket, compresses them with GZIP, and buffers
for up to 60 seconds or 5 MiB. S3 prefixes use Firehose arrival time; the
original `timestamp_utc` remains in each payload, so historical replays are
preserved even though they arrive now.

The bucket blocks public access, enforces TLS, uses S3-managed encryption and
versioning, and is retained when the stack is removed.

## Deploy

Use Python 3.10 or later, Node.js, the AWS CDK CLI, and AWS credentials
configured for the target account and region.

```sh
cd deployment_stacks
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cdk bootstrap aws://ACCOUNT/REGION
cdk synth
cdk deploy
```

Replace `ACCOUNT` and `REGION` with the deployment account and region. The
stack outputs the bucket, Firehose stream, IoT rule, and topic filter.

The AWS identity used by the device simulator must separately have permission
to publish to `devices/<thing-name>/telemetry`. The stack grants the IoT rule
permission to write to its Firehose stream and Firehose permission to write
only to the bucket's raw and error prefixes.

## Tests

```sh
python -m pytest
```
