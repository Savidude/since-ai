# AWS IoT Core sample device

This Python command-line app creates an AWS IoT Core Thing if it does not
already exist, then replays telemetry from a supplied dataset to an AWS IoT topic.

The sample uses your normal AWS credentials with the IoT Data Plane HTTPS API
for publishing and MQTT over WebSockets when listening for replay commands. It
does not create or use a device certificate or private key. The Thing is a
registry entry; publishing and command subscription are authorized as the AWS
IAM identity available through the standard AWS credential chain. The MQTT
listener uses credentials from the same boto3 session as the HTTPS clients.

## Requirements

- Python 3.10 or later
- AWS credentials available through the standard boto3 credential chain (for
  example, an IAM role or `AWS_PROFILE`)
- An AWS region configured in your profile or provided with `--region`

The AWS identity needs:

- `iot:CreateThing` to create the Thing on the first run
- `iot:DescribeEndpoint` to find the account's IoT Data Plane endpoint
- `iot:Publish` for `devices/<thing-name>/telemetry`
- When using `--listen-for-replay`: `iot:Connect` for the simulator MQTT client,
  `iot:Subscribe` for the replay topic filter, and `iot:Receive` for the replay
  topic

The identity publishing replay commands also needs `iot:Publish` for
`devices/<thing-name>/replay`.

Scope these permissions to the intended Thing, topic, and topic filter where
supported. No AWS credentials are stored by the simulator.

## Install and run

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
device-simulator --data data/full --asset demo-device --limit 10 --speed 0
```

`--data` is required; the simulator only publishes stored telemetry and does
not generate random sensor values. The default topic is
`devices/<thing-name>/telemetry`.

## Replay stored telemetry

`--asset` is an alias for `--thing-name` and is used both to select the data and
as the AWS IoT Thing name. From a working directory where `data/full` exists,
run:

```sh
device-simulator --data data/full --asset PUMP-06
```

Replay starts at `2025-10-09T00:00:00Z` by default. Override it with `--start`;
`--end` is an exclusive upper bound, `--limit` caps the number of messages, and
`--speed` controls timing (`1` follows the recorded timestamps, `0` runs as
fast as possible). Replayed payloads are published to the normal
`devices/<thing-name>/telemetry` AWS IoT topic. The dataset path is relative to
the current working directory.

## Replay data while listening for commands

To replay the dataset from `--start` while listening for replay requests, run:

```sh
device-simulator --thing-name PUMP-06 --data data/full --listen-for-replay
```

The simulator subscribes to `devices/PUMP-06/replay`. Send a JSON object with a
`timestamp_utc` field, for example:

```json
{"timestamp_utc":"2025-10-10T00:00:00Z"}
```

The simulator starts replaying the dataset from `--start` (the default start
timestamp shown above) as soon as it subscribes. It publishes the stored sensor
values, not generated random readings. When that initial replay finishes, the
simulator continues listening. Each valid request starts another replay from
the supplied timestamp (inclusive). If a request arrives while another replay
is running, the active replay is stopped before the new one starts. Requests
are handled in arrival order, so a newer request supersedes any replay started
by an older request that is still in progress.
`--end`, `--limit`, and `--speed` apply to the initial replay and each requested
replay. A request timestamp overrides `--start` for that replay. Invalid
messages are logged and ignored. `--listen-for-replay` requires `--data`;
all simulator runs require a dataset.

Stopping the process does not delete the Thing. Certificates or policies
created by an earlier version of this simulator are not changed or deleted.
Previously saved local credential files are also left untouched and are no
longer used.

## Tests

```sh
python -m pytest
```
