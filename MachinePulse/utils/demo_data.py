
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st


# ==================================================
# CONFIGURATION
# ==================================================

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Common fields across the pump datasets
TELEMETRY_COLUMNS = [
    "pump_id",
    "topic",
    "timestamp_utc",
    "operating_mode",
    "motor_current_a",
    "suction_pressure_kpa",
    "discharge_pressure_kpa",
    "fluid_temp_c",
    "ambient_temp_c",
    "vibration_rms_mm_s",
    "bearing_temp_c",
    "vibration_accel_rms_g",
    "vibration_crest_factor",
    "motor_power_kw",
    "motor_speed_rpm",
    "motor_winding_temp_c",
    "flow_m3_h",
    "valve_position_pct",
    "quality_flags",
    "sensor_source",
    "sample_interval_s"
]

NUMERIC_COLUMNS = [
    column for column in TELEMETRY_COLUMNS
    if column not in [
        "pump_id",
        "topic",
        "timestamp_utc",
        "operating_mode",
        "sensor_source"
    ]
]


# ==================================================
# DISCOVER PUMP FILES
# ==================================================

def get_pump_files():

    if not DATA_DIR.exists():
        return {}

    pump_files = {}

    for file_path in DATA_DIR.glob(
        "pump-*_history_all.jsonl"
    ):

        match = re.match(
            r"pump-(\d+)_history_all\.jsonl",
            file_path.name,
            re.IGNORECASE
        )

        if match:
            pump_id = f"PUMP-{int(match.group(1)):02d}"
            pump_files[pump_id] = file_path

    return dict(sorted(pump_files.items()))


# ==================================================
# LOAD INDIVIDUAL JSONL FILE
# ==================================================

@st.cache_data(show_spinner=False)
def load_jsonl_file(file_path, modified_time, file_size):

    records = []

    with open(file_path, "r", encoding="utf-8") as file:

        for line in file:

            if not line.strip():
                continue

            try:
                message = json.loads(line)

                # Support nested and flat JSONL formats
                if isinstance(message.get("payload"), dict):

                    record = {
                        **message["payload"],
                        "topic": message.get("topic")
                    }

                else:
                    record = message.copy()

                records.append(record)

            except (json.JSONDecodeError, TypeError):
                continue

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Add optional/common columns if missing
    for column in TELEMETRY_COLUMNS:

        if column not in df.columns:
            df[column] = np.nan

    # Parse timestamps
    df["timestamp_utc"] = pd.to_datetime(
        df["timestamp_utc"],
        utc=True,
        errors="coerce"
    )

    # Convert numerical measurements
    for column in NUMERIC_COLUMNS:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    # Standardize operating mode
    df["operating_mode"] = (
        df["operating_mode"]
        .astype("string")
        .str.upper()
    )

    # Remove invalid timestamps
    df = df.dropna(
        subset=["timestamp_utc"]
    )

    # Sort chronologically
    df = df.sort_values(
        "timestamp_utc"
    ).reset_index(drop=True)

    return df


# ==================================================
# GET TELEMETRY FOR A SELECTED PUMP
# ==================================================

def get_telemetry(pump_id):

    pump_files = get_pump_files()

    if pump_id not in pump_files:
        return pd.DataFrame()

    file_path = pump_files[pump_id]

    # File metadata allows cache refresh when file changes
    stat = file_path.stat()

    df = load_jsonl_file(
        str(file_path),
        stat.st_mtime_ns,
        stat.st_size
    )

    if df.empty:
        return df

    df = df.copy()

    df["pump_id"] = pump_id

    if df["topic"].isna().all():
        df["topic"] = f"devices/{pump_id}/telemetry"

    return df


# ==================================================
# GET ALL AVAILABLE PUMPS
# ==================================================

def get_available_pumps():

    pump_files = get_pump_files()

    available_pumps = []

    for pump_id, file_path in pump_files.items():

        stat = file_path.stat()

        df = load_jsonl_file(
            str(file_path),
            stat.st_mtime_ns,
            stat.st_size
        )

        if not df.empty:
            available_pumps.append(pump_id)

    return available_pumps


# ==================================================
# GET PUMP SUMMARY
# ==================================================

def get_pump_data():

    pump_records = []

    pump_files = get_pump_files()

    for pump_id, file_path in pump_files.items():

        stat = file_path.stat()

        df = load_jsonl_file(
            str(file_path),
            stat.st_mtime_ns,
            stat.st_size
        )

        if df.empty:
            continue

        latest = df.iloc[-1]

        pump_records.append({

            "pump_id": pump_id,

            "tier": "Actual telemetry",

            "status": str(
                latest.get("operating_mode", "Unknown")
            ).title(),

            "description": "Historical JSONL telemetry",

            "records": len(df),

            "first_timestamp": df[
                "timestamp_utc"
            ].iloc[0],

            "last_timestamp": df[
                "timestamp_utc"
            ].iloc[-1]

        })

    return pd.DataFrame(pump_records)
