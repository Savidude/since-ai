# Deployment stacks

This CDK v2 Python app contains the pump simulator, telemetry ingestion, and
optional MachinePulse UI stacks:

```text
Pump simulator tasks → AWS IoT Core → IoT Rule → Amazon Data Firehose → S3
MachinePulse viewers → HTTPS ALB → ECS Fargate UI tasks → AWS IoT Core
```

The MachinePulse UI stack is opt-in so existing deployments are unchanged.
Pass `-c deployMachinePulseUi=true` when synthesizing or deploying that stack.

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
Prediction status is published to the pump's `alerts` topic only when it
changes. The status is retained for 24 hours, so new subscribers receive the
latest state without repeated normal-status messages; telemetry and replay
behavior remain unchanged.

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

## Deploy the simulator and telemetry stacks

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
cdk deploy PumpSimulatorStack
```

Replace `ACCOUNT` and `REGION` with the deployment account and region. Deploying
the simulator stack also deploys the ingestion stack first. The stacks output
the simulator service/task/log-group names, cluster and VPC, bucket, Firehose
stream, IoT rule, and topic filter.

Each task has a per-pump IAM role scoped to its Thing, telemetry and alert
topics, and replay topic. The task role can create the Thing if it does not
exist, describe the IoT endpoint, publish telemetry, publish retained alert
updates, and listen for replay commands. The stack grants the IoT rule
permission to write to its Firehose stream and Firehose permission to write
only to the bucket's raw and error prefixes. No static AWS credentials are
placed in the image.

Fargate requires supported CPU/memory combinations. The sample uses the
0.25-vCPU/512-MiB combination independently for each pump; adjust each pump
entry in `pumps.json` to model another supported device budget.

## Deploy the MachinePulse public UI

The UI stack uses the same AWS account and region as the existing pump
simulator. It packages the Streamlit app, historical telemetry, pump alert
files, and what-if model data into its image. Its ECS task role grants access
only to the app's pump telemetry, alert, and replay topics; no static AWS
credentials or OpenAI secret are required.

The service starts with two ARM64 1-vCPU/2-GiB tasks across two Availability
Zones and scales between two and four tasks using CPU and memory utilization. It is
behind an HTTPS ALB with health checks, deployment rollback, WebSocket-friendly
idle timeout, and load-balancer cookie stickiness. Tasks use public subnets
without a NAT gateway; inbound application traffic is accepted through the
load balancer. The UI is public and has no login. Any visitor can use live
telemetry and request a simulator replay.

Before deploying the UI, update the existing simulator stack once to install
the retained alert-state publisher:

```sh
cd deployment_stacks
cdk deploy PumpSimulatorStack
```

Deploy using the existing hosted zone:

```sh
cd deployment_stacks
cdk deploy MachinePulseUiStack \
  -c deployMachinePulseUi=true \
  -c domainName=demo.savidude.com \
  -c hostedZoneId=Z020773314CEKDDGGHA45 \
  -c hostedZoneName=demo.savidude.com \
  --require-approval never
```

The stack requests a DNS-validated ACM certificate, creates the Route 53 alias
record, and outputs the HTTPS URL. The `deployMachinePulseUi` context flag
prevents the existing simulator deployment command from creating the UI stack
unless it is explicitly selected.

### 24-hour cost estimate

This estimate uses AWS list prices for `eu-central-1` (EU Frankfurt), all 12
existing ARM64 simulator tasks, the UI scaled to its maximum of four ARM64
1-vCPU/2-GiB tasks for the full day, 18 public IPv4 addresses (12 simulator,
four UI, and two ALB addresses), and one ALB LCU for 24 hours. It assumes 50
viewers keep live monitoring open for 24 hours, each following one selected
pump. At 60x, the simulators publish one telemetry message per pump per
second; changed alert states are retained and repeated status messages are
suppressed.

| Component | 24-hour estimate |
|---|---:|
| UI Fargate tasks (four, ARM64) | $4.36 |
| Existing simulator Fargate tasks (12, ARM64) | $3.27 |
| Public IPv4 addresses (18) | $2.16 |
| ALB and one LCU | $0.84 |
| AWS IoT Core messages (about 5.36 million) | $6.43 |
| IoT Rules Engine (telemetry rule and action) | $0.37 |
| Data Firehose (about 0.37 GB) | $0.03 |
| IoT connectivity | <$0.01 |
| **Modeled subtotal** | **about $17.48** |

This leaves about $2.52 for CloudWatch logs, storage, DNS, and variable data
transfer. The estimate excludes taxes, discounts/free-tier credits, frequent
replay traffic, and additional ALB capacity. AWS prices and actual usage vary;
the stack does not enforce a hard spending limit. See AWS [Fargate][fargate],
[Application Load Balancer][alb], [public IPv4][ipv4], [IoT Core][iot], and
[Data Firehose][firehose] pricing.

The local validation covered 50 simultaneous homepage requests, not 50
long-lived Streamlit WebSocket sessions through the AWS load balancer. Run an
ALB-backed session/load test before the event, and monitor task health,
utilization, and actual charges during it.

After the demo, remove the UI stack with the same context values:

```sh
cdk destroy MachinePulseUiStack \
  -c deployMachinePulseUi=true \
  -c domainName=demo.savidude.com \
  -c hostedZoneId=Z020773314CEKDDGGHA45 \
  -c hostedZoneName=demo.savidude.com
```

Destroying the UI stack does not stop the existing simulator fleet. If that
fleet is only needed for this demo, stop or destroy it separately after
checking that no other consumers depend on it.
Retained alert state expires after 24 hours.

[fargate]: https://aws.amazon.com/fargate/pricing/
[alb]: https://aws.amazon.com/elasticloadbalancing/pricing/
[ipv4]: https://aws.amazon.com/vpc/pricing/
[iot]: https://aws.amazon.com/iot-core/pricing/
[firehose]: https://aws.amazon.com/firehose/pricing/

## Tests

```sh
python -m pytest
```
