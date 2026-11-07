#!/usr/bin/env python3

import aws_cdk as cdk

from telemetry_ingestion_stack import TelemetryIngestionStack

app = cdk.App()
TelemetryIngestionStack(app, "TelemetryIngestionStack")
app.synth()
