from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st
from plotly.graph_objects import Figure

SENSOR_CHART_OPTIONS: dict[str, tuple[str, str]] = {
    "Motor Current": ("motor_current_a", "A"),
    "Motor Power": ("motor_power_kw", "kW"),
    "Motor Speed": ("motor_speed_rpm", "RPM"),
    "Motor Winding Temperature": ("motor_winding_temp_c", "°C"),
    "Suction Pressure": ("suction_pressure_kpa", "kPa"),
    "Discharge Pressure": ("discharge_pressure_kpa", "kPa"),
    "Fluid Temperature": ("fluid_temp_c", "°C"),
    "Ambient Temperature": ("ambient_temp_c", "°C"),
    "Flow Rate": ("flow_m3_h", "m³/h"),
    "Valve Position": ("valve_position_pct", "%"),
    "Vibration RMS": ("vibration_rms_mm_s", "mm/s"),
    "Bearing Temperature": ("bearing_temp_c", "°C"),
    "Vibration Acceleration RMS": ("vibration_accel_rms_g", "g"),
    "Vibration Crest Factor": ("vibration_crest_factor", ""),
}


def build_sensor_figures(
    data: pd.DataFrame,
    sensor_options: dict[str, tuple[str, str]] = SENSOR_CHART_OPTIONS,
) -> list[tuple[str, Figure]]:
    figures: list[tuple[str, Figure]] = []
    for label, (column, unit) in sensor_options.items():
        if column not in data.columns:
            continue

        valid_data = data.dropna(subset=[column, "timestamp_utc"])
        if valid_data.empty:
            continue

        y_label = f"{label} ({unit})" if unit else label
        figure = px.line(
            valid_data,
            x="timestamp_utc",
            y=column,
            template="plotly_white",
            labels={"timestamp_utc": "Timestamp (UTC)", column: y_label},
        )
        figure.update_layout(
            height=260,
            margin=dict(l=10, r=10, t=15, b=10),
            xaxis_title="Timestamp (UTC)",
            yaxis_title=y_label,
            showlegend=False,
        )
        figures.append((label, figure))

    return figures


def render_sensor_charts(
    data: pd.DataFrame,
    *,
    empty_message: str,
    sensor_options: dict[str, tuple[str, str]] = SENSOR_CHART_OPTIONS,
) -> int:
    figures = build_sensor_figures(data, sensor_options)
    for label, figure in figures:
        with st.container(border=True):
            st.markdown(f"#### {label}")
            st.plotly_chart(figure, width="stretch")

    if not figures:
        st.warning(empty_message)

    return len(figures)
