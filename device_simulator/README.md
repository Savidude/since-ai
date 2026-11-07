# AWS IoT Core sample device

This Python command-line app creates an AWS IoT Core Thing if it does not
already exist, then replays minute-aggregated telemetry from a supplied dataset
to an AWS IoT topic. It can also run a selectable prediction adapter; the
included PolarMind adapter publishes prediction results to
`devices/<thing-name>/alerts`.

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
- `iot:Publish` for `devices/<thing-name>/telemetry` and, when predictions are
  enabled, `devices/<thing-name>/alerts`
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
device-simulator --data data/full --asset PUMP-06 --limit 10 --speed 0
```

`--data` is required; the simulator only publishes stored telemetry and does
not generate random sensor values. The default topic is
`devices/<thing-name>/telemetry`. The default predictor is `polar-mind`;
`--model none` disables predictions. Add another adapter to the predictor
registry to make it selectable with `--model`.

## Scan for alerts offline

To find when the selected predictor raises alerts without connecting to AWS or
publishing telemetry, run the separate offline scanner:

```sh
device-simulator-scan-alerts \
  --data data/full \
  --asset PUMP-06 \
  --start 2025-10-08T13:50:00Z \
  --end 2025-10-09T00:00:00Z \
  --output pump-alerts.json
```

The scanner processes the replay as fast as the local predictor can run, feeds
it up to 24 hours of warm-up data, and writes only detected alerts to the JSON
file. No AWS credentials or permissions are used. `--limit` can cap the number
of replay minutes scanned; warm-up minutes do not count toward the limit. To
use the console command after changing an existing installation, reinstall
the package with `python -m pip install -e .`.

To scan all assets in the telemetry dataset concurrently and write their
alerts to one combined JSON file, use `--all-assets`:

```sh
device-simulator-scan-alerts \
  --data data/full \
  --all-assets \
  --output all-pump-alerts.json
```

The scanner discovers assets from the telemetry partitions or `telemetry.csv.gz`.
By default, it uses up to one worker per logical CPU core, capped at the number
of assets. Set `--workers` to choose a different maximum. In this mode,
`--limit` applies separately to each asset, and each alert includes its
`asset_id`.

By default, predictor history begins up to 24 hours before `--start`. Set
`--model-history-start` to use an earlier history window. Alerts are still
written only for minutes at or after `--start`.

## Minute aggregation and predictions

Telemetry samples are grouped by their UTC minute. Each numeric telemetry
column is sent as that minute's arithmetic mean. Categorical values use the
most common value, and `quality_flags` are combined with bitwise OR. The
message timestamp is the start of its minute. The predictor receives the
corresponding per-minute means plus the per-channel standard deviations and
counts it needs internally.

When a predictor is enabled, the simulator first feeds it up to 24 hours of
telemetry before the replay start as warm-up; those warm-up minutes are not
published and do not count toward `--limit`. If less than 24 hours of prior
data is available, the model continues learning as replay proceeds. The
included PolarMind adapter buffers warm-up history rather than scoring each
historical minute, then starts predicting when replay begins. Warm-up start,
progress, and completion are logged. Each non-null replay-minute prediction is
published as JSON to `devices/<thing-name>/alerts`, including no-alert results
with `fault_type: null`.

Use `--model-history-start` to feed the predictor more than the default 24 hours
before the first published minute. This is useful for reproducing an offline
scan: use the same model-history start as the scan, set `--start` to the first
minute you want to publish, and the intervening minutes will update the
predictor without being sent to AWS. For example:

```sh
device-simulator \
  --data data/full \
  --asset PUMP-06 \
  --model-history-start 2025-10-07T13:50:00Z \
  --start 2025-10-09T21:45:00Z \
  --speed 60
```

If the offline scan used its default history window, pass the scan's `--start`
minus 24 hours as `--model-history-start`. The replay still uses the model's
original baseline and all subsequent context, but publishes only from the new
`--start`.

`--speed 1` publishes one minute of telemetry per minute of wall-clock time;
`--speed 0` publishes as fast as possible. `--limit` caps published minute
messages, not raw samples.

## Replay stored telemetry

`--asset` is an alias for `--thing-name` and is used both to select the data and
as the AWS IoT Thing name. From a working directory where `data/full` exists,
run:

```sh
device-simulator --data data/full --asset PUMP-06 --model polar-mind
```

Replay starts at `2025-10-09T00:00:00Z` by default. Override it with `--start`;
`--end` is an exclusive upper bound. Replayed minute means are published to
`devices/<thing-name>/telemetry`. The dataset path is relative to the current
working directory. For a quick PUMP-06 demonstration before its October 8
fault, start at `2025-10-08T13:50:00Z` and use `--speed 60`; this leaves the
preceding 24 hours available as a healthy warm-up window.

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
timestamp shown above) as soon as it subscribes. Each valid request starts
another replay from the supplied timestamp (inclusive). Before each replay, the
selected predictor is reset and warmed from `--model-history-start` when set,
or with up to 24 hours of prior telemetry otherwise.
If a request arrives while another replay is running, the active replay is
stopped before the new one starts. Requests are handled in arrival order, so a
newer request supersedes any replay started by an older request that is still
in progress.
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
