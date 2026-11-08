# MachinePulse

MachinePulse is a pump-monitoring demonstration built around synthetic
centrifugal-pump telemetry. It brings together a Streamlit dashboard, an AWS
IoT Core simulator and message monitor, a synthetic-data benchmark and
what-if-analysis package, and AWS CDK deployment stacks.

The project is intended for demonstrations, experimentation, and the RETROFIT
AI 2026 challenge. Its data are simulated, and its models and what-if results
are not validated for operational use. The software is read-only with respect
to equipment: it has no control path to a real pump, PLC, drive, valve, or
safety system.

## Choose where to start

| I want to… | Start here |
|---|---|
| Explore the synthetic dataset, benchmark, baselines, or Python API | [`challenge_description/README_FIRST.md`](challenge_description/README_FIRST.md) |
| Start the local dashboard | [Run the dashboard](#run-the-dashboard) |
| Publish simulated pump telemetry to AWS IoT Core | [`device_simulator/README.md`](device_simulator/README.md) |
| Inspect AWS IoT telemetry and alerts in a terminal | [`monitor/README.md`](monitor/README.md) |
| Deploy the simulator fleet, data-ingestion pipeline, or dashboard | [`deployment_stacks/README.md`](deployment_stacks/README.md) |

## Project layout

| Directory | Purpose |
|---|---|
| [`MachinePulse/`](MachinePulse/) | Streamlit dashboard, local JSON Lines history, MQTT telemetry/replay clients, and dashboard tests. |
| [`device_simulator/`](device_simulator/) | `device-simulator` and `device-simulator-scan-alerts` command-line applications. Replays stored data and can publish predictor alerts through AWS IoT Core. |
| [`monitor/`](monitor/) | `device-monitor`, an AWS IoT Core MQTT subscriber for telemetry and alert topics. |
| [`challenge_description/`](challenge_description/) | `retrofit_sim` package, challenge documentation, synthetic data, baseline implementations, examples, and benchmark tests. |
| [`deployment_stacks/`](deployment_stacks/) | AWS CDK v2 app, Dockerfiles, pump fleet configuration, and infrastructure tests. |
| [`scripts/`](scripts/) | Supporting scripts for data retrieval and example model workflows. |

The Python components are separate packages; there is no root-level Python
environment or root requirements file. Install only the components you need.

## Architecture

```text
Synthetic pump dataset
  ├─> MachinePulse dashboard
  │     ├─ local JSONL history for offline browsing
  │     └─ AWS IoT Core MQTT for live telemetry, alerts, and replay
  └─> device-simulator
        └─ AWS IoT Core
             ├─ telemetry and alert topics ─> MachinePulse / device-monitor
             ├─ replay topic <────────────── MachinePulse replay requests
             └─ telemetry IoT Rule ─> Data Firehose ─> S3

retrofit_sim
  ├─ generates and describes the synthetic benchmark data
  ├─ scores event-based model predictions and provides reference baselines
  └─ runs a read-only, simulated what-if analysis
```

The dashboard, simulator, monitor, and benchmark can each be used locally.
AWS is needed only for IoT-connected operation or infrastructure deployment.

### IoT topic conventions

For a Thing or asset named `PUMP-06`, the applications use:

| Topic | Direction | Purpose |
|---|---|---|
| `devices/PUMP-06/telemetry` | Simulator → subscribers | Minute-aggregated telemetry. |
| `devices/PUMP-06/alerts` | Simulator → subscribers | Current predictor status; the simulator publishes changed states as retained messages. |
| `devices/PUMP-06/replay` | Dashboard/operator → simulator | JSON replay request containing a `timestamp_utc` value. |

The simulator publishes with the AWS IoT Data Plane HTTPS API and uses MQTT
over WebSockets for replay commands. The monitor and dashboard use MQTT over
WebSockets. These applications use the AWS credential chain/IAM identity;
they do not require device certificates. See the simulator and monitor guides
for their precise IAM permissions.

## Requirements

- Python 3.10 or later for the Python packages.
- AWS credentials and a configured region for IoT-connected workflows.
- Node.js, Docker with ARM64 build support, and the AWS CDK CLI for deployment.
- Access to a Route 53 hosted zone and a domain name to deploy the optional
  public dashboard stack with HTTPS.

Each package declares its Python dependencies in its own project metadata or
requirements file. The dashboard requirements install the local
`challenge_description` package as well.

## Run the dashboard

The dashboard has offline historical data checked into `MachinePulse/data/`.
Live monitoring and replay additionally require AWS credentials, a region,
and IAM permission to use the relevant IoT topics.

```sh
cd MachinePulse
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

Open the local URL printed by Streamlit (normally `http://localhost:8501`).
The sidebar links to:

- **Overview** — browse the equipment hierarchy and select a pump.
- **Pump Monitoring** — inspect historical telemetry or toggle live MQTT
  telemetry and current alert status.
- **Fault Diagnosis** — inspect the bundled historical alert records.
- **History & Replay** — request a simulator replay around a selected time,
  inspect telemetry and alerts, and run supported historical what-if scenarios.

The overview's online/offline KPI values are currently display constants, not
a live connectivity check. Historical charts use local JSONL files; they do
not, by themselves, indicate that an AWS device is online.

The what-if tool loads the full synthetic benchmark data from the adjacent
`challenge_description/data/full` directory by default. To use a different
compatible data directory, set `RETROFIT_SIM_DATA_DIR` to a directory
containing `assets.csv` and `operating_context.csv`.

## Explore the benchmark and synthetic data

The benchmark package is documented in
[`challenge_description/README_FIRST.md`](challenge_description/README_FIRST.md).
Its quick start runs entirely offline:

```sh
cd challenge_description
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python examples/quickstart.py
python baselines/run_baselines.py --data data/full --out my_results
python -m pytest -q
```

The package provides the `retrofit_sim` module and CLI:

```sh
python -m retrofit_sim --help
python -m retrofit_sim what_if --help
python -m retrofit_sim replay --help
python -m retrofit_sim score --help
python -m retrofit_sim make_dataset --help
```

The shipped data include starter and full tiers. The full tier contains
telemetry partitions, minute features, vibration snapshots, operating context,
asset metadata, alarms, maintenance records, and train/validation event labels.
Test assets are unlabeled. The data are generated by a documented reduced-order
simulator, not collected from operating equipment.

### Hackathon prediction deliverable

Include [`predictions_test.csv`](predictions_test.csv) with the final project.
It contains the model's alerts for test assets PUMP-10, PUMP-11, and PUMP-12 in
the format defined by
[`BENCHMARK_RULES.md`](challenge_description/BENCHMARK_RULES.md). Test labels
are held by the organizers, so use the validation split to evaluate and tune
locally; do not hand-label or tune against the test assets. For example, score
a validation prediction file from `challenge_description/` with:

```sh
python -m retrofit_sim score --predictions my_validation.csv --events data/full/events_validation.csv --assets data/full/assets.csv --benign data/full/benign_windows_train_validation.csv --split validation
```

For interpretation and challenge rules, consult:

- [`START_HERE.md`](challenge_description/START_HERE.md) and
  [`GLOSSARY.md`](challenge_description/GLOSSARY.md) for a plain-language
  introduction.
- [`CHALLENGE_BRIEF.md`](challenge_description/CHALLENGE_BRIEF.md) and
  [`BENCHMARK_RULES.md`](challenge_description/BENCHMARK_RULES.md) for the
  task, evaluation, and leakage constraints.
- [`DATA_DICTIONARY.md`](challenge_description/DATA_DICTIONARY.md) and
  [`TECHNICAL_MODEL.md`](challenge_description/TECHNICAL_MODEL.md) for data
  schemas and simulator assumptions.
- [`LICENSE_AND_PROVENANCE.md`](challenge_description/LICENSE_AND_PROVENANCE.md)
  for licensing and provenance.

## Run the AWS IoT simulator

Install the simulator package from the repository root:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e "device_simulator[test]"
```

Then replay a short section of stored data to an AWS IoT Thing (the region must
be configured in AWS or supplied with `--region`):

```sh
device-simulator \
  --data challenge_description/data/full \
  --asset PUMP-06 \
  --limit 10 \
  --speed 0
```

The simulator reads the supplied dataset; it does not generate random sensor
readings. By default it uses the `polar-mind` predictor. Use `--model none` to
disable predictions. `--speed 0` publishes as fast as possible; `--speed 1`
replays one minute of data per minute of wall-clock time.

For local, offline alert scanning (no AWS credentials required):

```sh
device-simulator-scan-alerts \
  --data challenge_description/data/full \
  --asset PUMP-06 \
  --start 2025-10-08T13:50:00Z \
  --end 2025-10-09T00:00:00Z \
  --output pump-alerts.json
```

For options, warm-up behavior, topic details, and required IAM permissions,
read [`device_simulator/README.md`](device_simulator/README.md).

## Monitor AWS IoT messages

Install and start the terminal monitor in a separate environment:

```sh
cd monitor
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
device-monitor --thing-name PUMP-06 --topic both
```

It subscribes to the asset's telemetry and alerts topics until interrupted.
Use `--capture messages.jsonl` to append received topic/payload pairs to a
JSON Lines file. Credentials and permissions are described in the
[`monitor/README.md`](monitor/README.md).

## Deploy to AWS

The CDK application defines three stacks:

1. **TelemetryIngestionStack** — routes telemetry messages through an IoT Rule
   and Amazon Data Firehose to a private S3 bucket.
2. **PumpSimulatorStack** — builds the simulator image and runs one Fargate
   service per pump listed in `deployment_stacks/pumps.json`.
3. **MachinePulseUiStack** — an optional Streamlit UI behind an HTTPS
   Application Load Balancer.

Start with the complete deployment guide in
[`deployment_stacks/README.md`](deployment_stacks/README.md). It documents
permissions, configuration, bootstrap/deploy commands, DNS and certificate
requirements, cost assumptions, and teardown. In particular, the optional
deployed UI is public and has no login; review that guide and secure the
deployment appropriately before exposing it.

The pump fleet is configured in
[`deployment_stacks/pumps.json`](deployment_stacks/pumps.json). The CDK app
loads this file, deploys telemetry ingestion before the simulator fleet, and
includes the UI stack only when `deployMachinePulseUi=true` is set in CDK
context.

## Tests

Install the corresponding test dependencies, then run each suite from the
repository root:

```sh
(cd MachinePulse && python -m pip install pytest && python -m pytest)
(cd device_simulator && python -m pip install -e ".[test]" && python -m pytest)
(cd monitor && python -m pip install -e ".[test]" && python -m pytest)
(cd challenge_description && python -m pip install -e ".[dev]" && python -m pytest -q)
(cd deployment_stacks && python -m pip install -r requirements-dev.txt && python -m pytest)
```

These are separate suites; there is no root-level test runner. The challenge
tests cover dataset/schema behavior, physics, labels, missing data, leakage,
baselines, sandbox, and scoring. The AWS application tests primarily exercise
local behavior and synthesized infrastructure; successful tests do not
validate an AWS account's permissions, networking, billing, or deployed
service health.

## Safety and limitations

- All included pump telemetry and events are synthetic.
- The prediction adapter and benchmark baselines are demonstration methods,
  not certified fault diagnosis.
- What-if results are simulated comparisons, not field approvals or guaranteed
  savings.
- Recommendations require human review. Do not treat any result as permission
  to modify or control real equipment.
- A deployed MachinePulse UI stack is public and unauthenticated by default;
  follow the deployment guide before using it outside a controlled demo.
