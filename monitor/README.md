# AWS IoT Core message monitor

This component subscribes to the test device's telemetry and alerts topics over
MQTT WebSockets and prints each message to the terminal. It uses the configured
AWS credentials to sign the WebSocket connection; it does not need a device
certificate or private key.

## Install and run

Use Python 3.10 or later:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
device-monitor --thing-name demo-device
```

By default, the monitor gets the AWS region and credentials from the boto3
configuration. Pass `--region` or `--profile` to select them explicitly. Start
the monitor before the simulator in another terminal. It listens to
`devices/<thing-name>/telemetry` and `devices/<thing-name>/alerts` until stopped
with Ctrl+C. Use `--topic telemetry` or `--topic alerts` to listen to only one;
the default is `--topic both`.
Use `--asset` as an alias for `--thing-name`.

To save received messages as JSON Lines while continuing to print them, pass a
capture path. The file is appended to; each line contains the MQTT topic and the
decoded JSON payload:

```sh
device-monitor --asset PUMP-06 --capture pump-06.jsonl
```

For example, to monitor only alerts:

```sh
device-monitor --asset PUMP-06 --topic alerts
```

Non-JSON MQTT payloads are captured as text and reported in the monitor log.

## AWS permissions

The configured AWS identity needs:

- `iot:DescribeEndpoint`
- `iot:Connect` on `arn:aws:iot:<region>:<account>:client/device-monitor-*`
- `iot:Subscribe` on
  `arn:aws:iot:<region>:<account>:topicfilter/devices/<thing-name>/telemetry`
  and `arn:aws:iot:<region>:<account>:topicfilter/devices/<thing-name>/alerts`
- `iot:Receive` on
  `arn:aws:iot:<region>:<account>:topic/devices/<thing-name>/telemetry` and
  `arn:aws:iot:<region>:<account>:topic/devices/<thing-name>/alerts`

No certificate, Thing attachment, or IoT policy is created for the monitor.

## Tests

```sh
python -m pytest
```
