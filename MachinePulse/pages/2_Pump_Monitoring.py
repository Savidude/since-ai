import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from backend import MqttTelemetrySubscriber

from utils.demo_data import get_pump_data, get_telemetry
from utils.alert_display import render_current_alert_accordion
from utils.telemetry_charts import SENSOR_CHART_OPTIONS, render_sensor_charts

from utils.ui_components import setup_page, render_sidebar, page_header


# ==================================================
# PAGE CONFIGURATION
# ==================================================

setup_page("Pump Monitoring", "📈")

render_sidebar()

page_header("Pump Monitoring", "Explore pump telemetry and operating conditions.")


# ==================================================
# LOAD AVAILABLE PUMPS
# ==================================================

pumps = get_pump_data()

if pumps.empty:
    st.warning("No pump data available.")
    st.stop()

pump_ids = pumps["pump_id"].tolist()


# ==================================================
# PUMP SELECTION
# ==================================================

# Receive pump selection from Overview page
requested_pump = st.session_state.pop("selected_pump", None)

# Set the selected pump before creating the widget
if requested_pump in pump_ids:
    st.session_state["monitoring_pump"] = requested_pump

if (
    "monitoring_pump" not in st.session_state
    or st.session_state["monitoring_pump"] not in pump_ids
):
    st.session_state["monitoring_pump"] = pump_ids[0]


selected_pump = st.selectbox("Select Equipment", pump_ids, key="monitoring_pump")

live_mode = st.toggle("Live telemetry", value=True, key="live_mqtt_telemetry")


# ==================================================
# LOAD SELECTED PUMP DATA
# ==================================================

pump_info = pumps[pumps["pump_id"] == selected_pump].iloc[0]

subscriber_key = "pump_mqtt_subscriber"
subscriber = st.session_state.get(subscriber_key)

if subscriber is not None and (
    subscriber.asset_id != selected_pump or subscriber.status == "stopped"
):
    subscriber.stop()
    del st.session_state[subscriber_key]
    subscriber = None

if live_mode:
    if subscriber is None:
        subscriber = MqttTelemetrySubscriber(selected_pump)
        st.session_state[subscriber_key] = subscriber

    if subscriber.status not in {"error", "stopped"}:
        st_autorefresh(interval=2000, key=f"mqtt_refresh_{selected_pump}")

    data = pd.DataFrame(subscriber.get_records())
else:
    if subscriber is not None:
        subscriber.stop()
        del st.session_state[subscriber_key]

    data = get_telemetry(selected_pump)

data = data.drop(columns=["topic"], errors="ignore")

if live_mode:
    current_alert = subscriber.latest_alert
    render_current_alert_accordion(
        current_alert,
        unavailable=bool(subscriber.alert_error or subscriber.error),
    )

if data.empty:
    if live_mode and subscriber.error:
        st.error(
            "Live pump data is currently unavailable. Please try again later."
        )
    elif live_mode:
        st.info("Waiting for live pump measurements.")
    else:
        st.warning(f"No telemetry data available for {selected_pump}.")
    st.stop()


# ==================================================
# PREPARE TIMESTAMPS
# ==================================================

data = data.copy()

data["timestamp_utc"] = pd.to_datetime(data["timestamp_utc"], utc=True, errors="coerce")

data = (
    data.dropna(subset=["timestamp_utc"])
    .sort_values("timestamp_utc")
    .reset_index(drop=True)
)

if data.empty:
    st.warning("No valid timestamped measurements.")
    st.stop()

latest = data.iloc[-1]


# ==================================================
# EQUIPMENT OVERVIEW
# ==================================================

st.divider()

st.subheader("Equipment Overview")

c1, c2, c3 = st.columns(3)

c1.metric("Pump ID", selected_pump)

c2.metric("Instrumentation", pump_info["tier"])

operating_status = str(latest.get("operating_mode", "Unknown")).upper()

c3.metric("Operating Mode", operating_status)


# ==================================================
# SENSOR MEASUREMENTS
# ==================================================

st.divider()

st.subheader("Sensor Measurements")

# ==================================================
# DISPLAY AVAILABLE SENSOR CHARTS
# ==================================================

render_sensor_charts(
    data,
    empty_message="No valid sensor measurements available for this pump.",
)


# ==================================================
# LATEST SENSOR READINGS
# ==================================================

st.divider()

st.subheader("Latest Sensor Readings")

latest_readings = []

for label, (column, unit) in SENSOR_CHART_OPTIONS.items():

    if column not in data.columns:
        continue

    value = latest.get(column)

    if pd.notna(value):

        latest_readings.append(
            {"Measurement": label, "Latest Value": (f"{value:.3f} {unit}".strip())}
        )


if latest_readings:

    st.dataframe(pd.DataFrame(latest_readings), width="stretch", hide_index=True)

else:
    st.info("No valid sensor readings available.")


# ==================================================
# RAW TELEMETRY
# ==================================================

st.divider()

with st.expander("View Raw Telemetry Records"):

    st.dataframe(data, width="stretch", hide_index=True)
