#!/usr/bin/env python3
"""Call PolarMind and print the JSON result.

From the participant folder:

    python OurModel/call_polarMind.py --asset PUMP-08 --minute 2025-09-10T10:16:00Z
    python OurModel/call_polarMind.py --asset PUMP-08
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

OUR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(OUR)
sys.path.insert(0, OUR)

import polarMindModel as pm  # noqa: E402

# Passed into PolarMindStream.update. A channel the pump does not have is omitted.
CHANNELS = (
    "motor_speed_rpm",
    "motor_current_a",
    "motor_power_kw",
    "suction_pressure_kpa",
    "discharge_pressure_kpa",
    "flow_m3_h",
    "bearing_temp_c",
    "motor_winding_temp_c",
    "fluid_temp_c",
    "vibration_rms_mm_s",
    "vibration_accel_rms_g",
    "vibration_crest_factor",
    "valve_position_pct",
)
FEATURE_FIELDS = (
    ["window_start_utc", "operating_mode", "running_fraction", "rows"]
    + [f"{name}__{stat}" for name in CHANNELS for stat in ("mean", "std", "count")]
    + ["ratio_21", "snapshot_utc"]
)


def to_features(row) -> dict:
    """The only fields the model receives for one minute."""
    features = {}
    for name in FEATURE_FIELDS:
        if name not in row:
            continue
        value = row[name]
        if pd.isna(value):
            continue
        if hasattr(value, "isoformat"):
            value = pd.Timestamp(value)
            if value.tzinfo is None:
                value = value.tz_localize("UTC")
            value = value.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif hasattr(value, "item"):
            value = value.item()
        features[name] = value
    return features


def _stamp(value) -> pd.Timestamp:
    when = pd.Timestamp(value)
    if when.tzinfo is None:
        when = when.tz_localize("UTC")
    return when


def one_minute(asset, minutes: pd.DataFrame, when: pd.Timestamp) -> str:
    """Feed every earlier minute, then score this one. Same call a device makes."""
    minutes = minutes.sort_values("window_start_utc")
    hit = minutes[minutes["window_start_utc"] == when]
    if hit.empty:
        raise SystemExit(f"no minute {when.strftime('%Y-%m-%dT%H:%M:%SZ')} for {asset['asset_id']}")
    stream = pm.PolarMindStream(asset)
    earlier = minutes[minutes["window_start_utc"] < when]
    stream.history = pd.DataFrame([to_features(item) for _, item in earlier.iterrows()])
    stream.started = minutes["window_start_utc"].min()
    features = to_features(hit.iloc[0])
    return stream.update(features)


def all_alerts(asset, data_dir: str) -> str:
    """Every alert for this pump, one JSON object per alert."""
    assets = pd.DataFrame([asset])
    frame = pm.load_features(data_dir, pd.read_csv(os.path.join(data_dir, "assets.csv")))
    pred = pm.predict(frame[frame["asset_id"] == asset["asset_id"]], assets)
    rows = []
    for _, alert in pred.iterrows():
        fired = _stamp(alert["alert_time_utc"]) - pd.Timedelta(minutes=1)
        rows.append({
            "asset_id": alert["asset_id"],
            "minute_utc": fired.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": "alert",
            "event_class": alert["event_class"],
            "fault_type": alert["fault_type"],
            "confidence": alert["confidence"],
            "evidence": alert["evidence"],
            "recommended_action": alert["recommended_action"],
        })
    return json.dumps(rows, indent=2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Call PolarMind and print JSON.")
    parser.add_argument("--data", default=os.path.join(ROOT, "data", "full"))
    parser.add_argument("--asset", default="PUMP-08")
    parser.add_argument("--minute", default="", help="UTC minute to score, e.g. 2025-09-10T10:16:00Z")
    parser.add_argument("--features", default="", help="JSON file of one minute's features to pass to the model")
    args = parser.parse_args(argv)

    assets = pd.read_csv(os.path.join(args.data, "assets.csv"))
    chosen = assets[assets["asset_id"] == args.asset]
    if chosen.empty:
        raise SystemExit(f"unknown asset {args.asset}")
    asset = chosen.iloc[0]

    if args.minute or args.features:
        feat = pd.read_parquet(os.path.join(args.data, "features_1min.parquet"))
        minutes = feat[feat["asset_id"] == args.asset].copy()
        minutes["window_start_utc"] = pd.to_datetime(minutes["window_start_utc"], utc=True)
        if args.features:
            with open(args.features, encoding="utf-8") as fh:
                supplied = json.load(fh)
            when = _stamp(supplied["window_start_utc"])
            earlier = minutes[minutes["window_start_utc"] < when]
            stream = pm.PolarMindStream(asset)
            stream.history = pd.DataFrame([to_features(item) for _, item in earlier.iterrows()])
            stream.started = minutes["window_start_utc"].min()
            print(stream.update(to_features(supplied)))
        else:
            print(one_minute(asset, minutes, _stamp(args.minute)))
    else:
        print(all_alerts(asset, args.data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
