# Pump simulator and telemetry ingestion stacks

This CDK v2 Python app deploys two stacks:

```text
One ECS Fargate service per pump → AWS IoT Core → IoT Rule → Amazon Data Firehose → S3
```

The `PumpSimulatorStack` builds one ARM64 simulator image containing the
`device_simulator` application and `challenge_description/data/full`, then
creates one independent Fargate service and task definition for every entry in
`pumps.json`. Each task has its own CPU, memory, replay window, predictor state,
IAM task role, and CloudWatch log group. The manifest includes all 12 pumps,
each with 0.25 vCPU and 512 MiB. `PUMP-06` retains its configured replay window;
for the other pumps, `model_history_start` is the asset's `window_start_utc` from
[`assets.csv`](../challenge_description/data/full/assets.csv), and `start` is
24 hours later to provide one day of predictor warm-up.

The tasks run `device-simulator --listen-for-replay`. They perform the configured
initial replay, then remain subscribed to `devices/<thing-name>/replay`.
Restarting a task starts its initial replay again. The Fargate services use
public subnets with no inbound security-group rules so they can reach AWS IoT
Core and CloudWatch without a NAT gateway.

The `TelemetryIngestionStack` deploys the raw telemetry path:

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

Use Python 3.10 or later, Node.js, Docker with ARM64 build support, the AWS CDK
CLI, and AWS credentials configured for the target account and region.

The Docker build context is the repository root. The root `.dockerignore`
limits it to the simulator source, the full telemetry dataset, and the
simulator Dockerfile. The Dockerfile copies the sibling dataset directly into
the image; no separate data staging command is needed. Updating the dataset
requires rebuilding and deploying the image.

Edit `pumps.json` to configure the pumps to deploy. Each pump entry accepts:

- `asset`: the telemetry asset ID and AWS IoT Thing name.
- `start`: inclusive first replay timestamp.
- `model_history_start`: optional predictor warm-up start; if omitted, the
  simulator uses up to 24 hours before `start`.
- `speed`: replay speed factor.
- `model`: predictor name, `polar-mind` by default; `none` disables predictions.
- `cpu` and `memory_mib`: independent Fargate task limits for this pump.

The history timestamp must be at or before the replay start. The `--asset`
option selects data for that pump and publishes under its own IoT topics.

```sh
cd deployment_stacks
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cdk bootstrap aws://ACCOUNT/REGION
cdk synth PumpSimulatorStack
cdk deploy
```

Replace `ACCOUNT` and `REGION` with the deployment account and region. Deploying
the simulator stack also deploys the ingestion stack first. The stacks output
the simulator service/task/log-group names, cluster and VPC, bucket, Firehose
stream, IoT rule, and topic filter.

Each task has a per-pump IAM role scoped to its Thing, telemetry and alert
topics, and replay topic. The task role can create the Thing if it does not
exist, describe the IoT endpoint, publish telemetry/alerts, and listen for
replay commands. The stack grants the IoT rule permission to write to its
Firehose stream and Firehose permission to write only to the bucket's raw and
error prefixes. No static AWS credentials are placed in the image.

Fargate requires supported CPU/memory combinations. The sample uses the
0.25-vCPU/512-MiB combination independently for each pump; adjust each pump
entry in `pumps.json` to model another supported device budget.

## Tests

```sh
python -m pytest
```
