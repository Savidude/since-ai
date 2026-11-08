#!/usr/bin/env python3

import json
from pathlib import Path

import aws_cdk as cdk

from device_simulator_stack import PumpConfig, PumpSimulatorStack
from machinepulse_ui_stack import MachinePulseUiStack
from telemetry_ingestion_stack import TelemetryIngestionStack

app = cdk.App()
telemetry_stack = TelemetryIngestionStack(app, "TelemetryIngestionStack")

configuration_path = Path(__file__).with_name("pumps.json")
configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
if not isinstance(configuration, dict):
    raise ValueError(f"{configuration_path} must contain a JSON object.")

pump_definitions = configuration.get("pumps")
if not isinstance(pump_definitions, list):
    raise ValueError(f"{configuration_path} must define a 'pumps' array.")

pump_configs = [
    PumpConfig(**pump_definition)
    for pump_definition in pump_definitions
    if isinstance(pump_definition, dict)
]
if len(pump_configs) != len(pump_definitions):
    raise ValueError("Every entry in the pumps array must be a JSON object.")

simulator_stack = PumpSimulatorStack(
    app,
    "PumpSimulatorStack",
    pumps=pump_configs,
    architecture=configuration.get("architecture", "ARM64"),
)
simulator_stack.add_stack_dependency(
    telemetry_stack,
    reason="Deploy telemetry ingestion before starting the pump publishers.",
)

if str(app.node.try_get_context("deployMachinePulseUi")).lower() == "true":
    MachinePulseUiStack(app, "MachinePulseUiStack")

app.synth()
