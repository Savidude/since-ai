
import streamlit as st
import plotly.express as px
import pandas as pd

from datetime import timedelta

from utils.demo_data import (
    get_pump_data,
    get_telemetry
)

from utils.ui_components import (
    setup_page,
    render_sidebar,
    page_header
)


# ==================================================
# RECEIVE NAVIGATION DATA FROM FAULT DIAGNOSIS
# ==================================================

requested_pump = st.session_state.pop(
    "history_pump_from_fault",
    None
)

requested_target_timestamp = st.session_state.pop(
    "history_target_timestamp",
    None
)

requested_fault_name = st.session_state.pop(
    "selected_fault",
    None
)


# ==================================================
# PAGE CONFIGURATION
# ==================================================

setup_page("History & Replay", "⏪")
render_sidebar()

page_header(
    "Historical Analysis & Replay",
    "Investigate previous pump conditions and fault development."
)


# ==================================================
# LOAD AVAILABLE PUMPS
# ==================================================

pumps = get_pump_data()

if pumps.empty:
    st.warning("No pump data available.")
    st.stop()

pump_ids = pumps["pump_id"].astype(str).tolist()

# Set the pump before creating its selectbox
if requested_pump in pump_ids:
    st.session_state["history_pump"] = requested_pump

if (
    "history_pump" not in st.session_state
    or st.session_state["history_pump"] not in pump_ids
):
    st.session_state["history_pump"] = pump_ids[0]

pump = st.selectbox(
    "Select Pump",
    pump_ids,
    key="history_pump"
)


# ==================================================
# LOAD TELEMETRY
# ==================================================

data = get_telemetry(pump)

if data.empty:
    st.warning(f"No historical data available for {pump}.")
    st.stop()


# ==================================================
# PREPARE TIMESTAMPS
# ==================================================

data["timestamp_utc"] = pd.to_datetime(
    data["timestamp_utc"],
    utc=True,
    errors="coerce"
)

data = (
    data.dropna(subset=["timestamp_utc"])
    .sort_values("timestamp_utc")
    .reset_index(drop=True)
)

if data.empty:
    st.warning("No valid timestamped records.")
    st.stop()

# Use timezone-naive UTC timestamps for Streamlit widgets
data["timestamp_display"] = (
    data["timestamp_utc"].dt.tz_convert(None)
)

first_timestamp = data["timestamp_display"].iloc[0]
last_timestamp = data["timestamp_display"].iloc[-1]


# ==================================================
# INITIALIZE SESSION STATE
# ==================================================

pump_changed = (
    st.session_state.get("previous_history_pump") != pump
)

if pump_changed:

    st.session_state["previous_history_pump"] = pump

    st.session_state["history_slider_time"] = last_timestamp

    st.session_state["search_date"] = last_timestamp.date()

    st.session_state["search_time"] = (
        last_timestamp.time().replace(
            second=0,
            microsecond=0
        )
    )

    # Clear fault information from the previously selected pump
    st.session_state.pop("replay_fault_name", None)
    st.session_state.pop("replay_fault_timestamp", None)


# Initialize if not already available
if "history_slider_time" not in st.session_state:
    st.session_state["history_slider_time"] = last_timestamp

if "search_date" not in st.session_state:
    st.session_state["search_date"] = last_timestamp.date()

if "search_time" not in st.session_state:
    st.session_state["search_time"] = (
        last_timestamp.time().replace(
            second=0,
            microsecond=0
        )
    )


# Keep slider value within available period
st.session_state["history_slider_time"] = min(
    max(
        pd.Timestamp(st.session_state["history_slider_time"]),
        first_timestamp
    ),
    last_timestamp
)


# ==================================================
# APPLY FAULT EVENT TIMESTAMP
# ==================================================

if requested_target_timestamp is not None:

    target_dt = pd.to_datetime(
        requested_target_timestamp,
        utc=True,
        errors="coerce"
    )

    if pd.notna(target_dt):

        # Convert to timezone-naive UTC for Streamlit widgets
        target_display = target_dt.tz_convert(None)

        # Find the closest available telemetry record
        differences = (
            data["timestamp_display"] - target_display
        ).abs()

        closest_index = differences.argmin()

        nearest_timestamp = data.loc[
            closest_index,
            "timestamp_display"
        ]

        # Synchronize all three date/time controls
        # IMPORTANT: This must happen before creating the widgets.
        st.session_state["history_slider_time"] = nearest_timestamp

        st.session_state["search_date"] = (
            nearest_timestamp.date()
        )

        st.session_state["search_time"] = (
            nearest_timestamp.time().replace(
                second=0,
                microsecond=0
            )
        )

        # Store fault information for display and chart markers
        st.session_state["replay_fault_name"] = (
            requested_fault_name or "Fault event"
        )

        st.session_state["replay_fault_timestamp"] = target_display

        # Warn if the requested event is outside the available data
        if target_display < first_timestamp or target_display > last_timestamp:
            st.warning(
                "The fault timestamp is outside the available telemetry "
                "period. Showing the closest available measurement instead."
            )


# ==================================================
# PLAYBACK SPEED
# ==================================================

st.subheader("Telemetry Playback")

speed = st.select_slider(
    "Playback Speed",
    options=[1, 2, 5, 10],
    value=1,
    format_func=lambda x: f"{x}x"
)


# ==================================================
# CALENDAR AND TIME SEARCH
# ==================================================

st.divider()

st.subheader("Find Historical Date & Time")

st.caption(
    "Select a date and time to find the closest available "
    "historical measurement. All times are UTC."
)

col1, col2, col3 = st.columns([2, 2, 1])

with col1:

    selected_date = st.date_input(
        "Select Date",
        min_value=first_timestamp.date(),
        max_value=last_timestamp.date(),
        key="search_date"
    )

with col2:

    selected_time = st.time_input(
        "Select Time (UTC)",
        key="search_time",
        step=60
    )

with col3:

    st.write("")
    st.write("")

    search_clicked = st.button(
        "🔍 Search",
        width="stretch"
    )


if search_clicked:

    requested_timestamp = pd.Timestamp(
        f"{selected_date} {selected_time}"
    )

    differences = (
        data["timestamp_display"] - requested_timestamp
    ).abs()

    closest_index = differences.argmin()

    closest_timestamp = data.loc[
        closest_index,
        "timestamp_display"
    ]

    # Update timeline to the closest record
    st.session_state["history_slider_time"] = closest_timestamp

    # Manual search clears the previously selected fault marker
    st.session_state.pop("replay_fault_name", None)
    st.session_state.pop("replay_fault_timestamp", None)

    st.rerun()


# ==================================================
# DATE-TIME TIMELINE SLIDER
# ==================================================

st.divider()

st.subheader("Historical Timeline")

st.caption(
    f"Available period: "
    f"{first_timestamp.strftime('%d %b %Y %H:%M')} — "
    f"{last_timestamp.strftime('%d %b %Y %H:%M')} UTC"
)

col1, col2 = st.columns(2)

with col1:

    if st.button(
        "⏮ Reset",
        width="stretch"
    ):

        st.session_state["history_slider_time"] = first_timestamp

        st.session_state["search_date"] = first_timestamp.date()

        st.session_state["search_time"] = (
            first_timestamp.time().replace(
                second=0,
                microsecond=0
            )
        )

        st.session_state.pop("replay_fault_name", None)
        st.session_state.pop("replay_fault_timestamp", None)

        st.rerun()


with col2:

    if st.button(
        "⏭ Show Latest",
        width="stretch"
    ):

        st.session_state["history_slider_time"] = last_timestamp

        st.session_state["search_date"] = last_timestamp.date()

        st.session_state["search_time"] = (
            last_timestamp.time().replace(
                second=0,
                microsecond=0
            )
        )

        st.session_state.pop("replay_fault_name", None)
        st.session_state.pop("replay_fault_timestamp", None)

        st.rerun()


# Convert values safely to Python datetime objects
slider_min = pd.Timestamp(
    first_timestamp
).to_pydatetime()

slider_max = pd.Timestamp(
    last_timestamp
).to_pydatetime()


# The session-state value is already initialized above.
# Do not pass an additional value argument when using this key.
selected_timestamp = st.slider(
    "Timeline Position (UTC)",
    min_value=slider_min,
    max_value=slider_max,
    step=timedelta(seconds=60),
    format="YYYY-MM-DD HH:mm",
    key="history_slider_time"
)


# ==================================================
# FIND CLOSEST ACTUAL TELEMETRY RECORD
# ==================================================

requested_timestamp = pd.Timestamp(selected_timestamp)

differences = (
    data["timestamp_display"] - requested_timestamp
).abs()

closest_index = differences.argmin()

position = closest_index + 1


# ==================================================
# SELECT HISTORICAL RECORDS
# ==================================================

replay_data = data.iloc[:position].copy()

selected = data.iloc[closest_index]

selected_timestamp_utc = selected["timestamp_utc"]


st.caption(
    f"Showing {position:,} of {len(data):,} measurements"
)


# Display selected historical time
st.info(
    "Selected historical time: "
    f"{selected_timestamp_utc.strftime('%d %B %Y, %H:%M:%S UTC')}"
)


# Display selected fault information, if available
replay_fault_name = st.session_state.get(
    "replay_fault_name"
)

replay_fault_timestamp = st.session_state.get(
    "replay_fault_timestamp"
)

if replay_fault_name and replay_fault_timestamp is not None:

    st.warning(
        f"Fault event: **{replay_fault_name}**  \n"
        f"Event timestamp: "
        f"{replay_fault_timestamp.strftime('%d %B %Y, %H:%M:%S UTC')}"
    )


# ==================================================
# OPERATING CONDITIONS
# ==================================================

st.divider()

st.subheader("Operating Conditions")

c1, c2, c3 = st.columns(3)

c1.metric(
    "Operating Mode",
    str(selected.get("operating_mode", "Unknown"))
)

current = selected.get("motor_current_a")

c2.metric(
    "Motor Current",
    f"{current:.2f} A"
    if pd.notna(current) else "N/A"
)

temperature = selected.get("fluid_temp_c")

c3.metric(
    "Fluid Temperature",
    f"{temperature:.1f} °C"
    if pd.notna(temperature) else "N/A"
)


# ==================================================
# SENSOR MEASUREMENTS
# ==================================================

st.divider()

st.subheader("Historical Sensor Measurements")

measurements = {
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
    "Vibration Crest Factor": ("vibration_crest_factor", "")
}


plotted_charts = 0

for label, (column, unit) in measurements.items():

    if column not in replay_data.columns:
        continue

    valid_data = replay_data.dropna(
        subset=[column, "timestamp_utc"]
    )

    if valid_data.empty:
        continue

    with st.container(border=True):

        st.markdown(f"#### {label}")

        y_label = (
            f"{label} ({unit})"
            if unit else label
        )

        fig = px.line(
            valid_data,
            x="timestamp_utc",
            y=column,
            template="plotly_white",
            labels={
                "timestamp_utc": "Timestamp (UTC)",
                column: y_label
            }
        )

        # Mark the selected historical measurement
        selected_value = selected.get(column)

        if pd.notna(selected_value):

            fig.add_scatter(
                x=[selected_timestamp_utc],
                y=[selected_value],
                mode="markers",
                marker=dict(size=10),
                name="Selected record"
            )

        # Mark the fault event on the charts
        if replay_fault_timestamp is not None:

            fig.add_vline(
                x=replay_fault_timestamp,
                line_dash="dash",
                line_width=2,
                annotation_text="Fault event",
                annotation_position="top"
            )

        fig.update_layout(
            height=300,
            margin=dict(
                l=10,
                r=10,
                t=15,
                b=10
            ),
            xaxis_title="Timestamp (UTC)",
            yaxis_title=y_label,
            showlegend=False
        )

        st.plotly_chart(
            fig,
            width="stretch"
        )

        plotted_charts += 1


if plotted_charts == 0:

    st.warning(
        "No valid measurements available for this period."
    )


# ==================================================
# RAW TELEMETRY
# ==================================================

st.divider()

with st.expander("View Historical Telemetry Records"):

    st.dataframe(
        replay_data.drop(
            columns=["timestamp_display"],
            errors="ignore"
        ),
        width="stretch",
        hide_index=True
    )
