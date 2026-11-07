
import streamlit as st
import pandas as pd

from utils.ui_components import (
    setup_page,
    render_sidebar,
    page_header
)

from utils.demo_data import (
    get_pump_data,
    get_telemetry,
    get_historical_fault_events
)


# ==================================================
# PAGE CONFIGURATION
# ==================================================

setup_page("Fault Diagnosis", "🧠")

render_sidebar()

page_header(
    "AI Fault Diagnosis",
    "Predicted faults, supporting evidence and uncertainty."
)


# ==================================================
# LOAD AVAILABLE PUMPS
# ==================================================

pumps = get_pump_data()

if pumps.empty:
    st.warning("No pump data available.")
    st.stop()


# ==================================================
# PUMP SELECTION
# ==================================================

pump_ids = pumps["pump_id"].tolist()

selected_pump = st.selectbox(
    "Select Pump",
    pump_ids,
    key="fault_diagnosis_pump"
)


# ==================================================
# ILLUSTRATIVE DIAGNOSIS RESULTS
# ==================================================

demo_diagnoses = {

    "PUMP-01": {
        "fault": "No fault detected",
        "class": "Normal",
        "confidence": 0.96,
        "evidence": [
            "Vibration within normal range",
            "Temperature stable",
            "Pressure measurements consistent"
        ]
    },

    "PUMP-02": {
        "fault": "Possible vibration anomaly",
        "class": "Machine",
        "confidence": 0.82,
        "evidence": [
            "Increasing vibration amplitude",
            "Deviation from historical baseline"
        ]
    },

    "PUMP-03": {
        "fault": "Possible bearing wear",
        "class": "Machine",
        "confidence": 0.92,
        "evidence": [
            "Increasing vibration",
            "Rising temperature",
            "Abnormal trend in recent measurements"
        ]
    },

    "PUMP-04": {
        "fault": "No fault detected",
        "class": "Normal",
        "confidence": 0.95,
        "evidence": [
            "Stable sensor measurements",
            "No significant anomaly"
        ]
    },

    "PUMP-05": {
        "fault": "Possible pressure sensor anomaly",
        "class": "Instrument",
        "confidence": 0.78,
        "evidence": [
            "Pressure deviation",
            "Other measurements relatively stable"
        ]
    }

}


# ==================================================
# PUMP-06 HISTORICAL FAULT EVENTS
# ==================================================

PUMP_06_FAULTS = [

    {
        "fault": "SUCTION_STRAINER_CLOGGING",
        "timestamp": "2025-10-09T07:47:00Z",
        "severity": "High"
    },

    {
        "fault": "SENSOR_DRIFT",
        "timestamp": "2025-10-09T09:13:00Z",
        "severity": "Medium"
    },

    {
        "fault": "CAVITATION",
        "timestamp": "2025-10-09T21:45:00Z",
        "severity": "High"
    },

    {
        "fault": "BEARING_WEAR",
        "timestamp": "2025-10-09T21:51:00Z",
        "severity": "High"
    },

    {
        "fault": "SENSOR_CALIBRATION_SHIFT",
        "timestamp": "2025-10-10T00:21:00Z",
        "severity": "Medium"
    },

    {
        "fault": "MISALIGNMENT",
        "timestamp": "2025-10-10T09:04:00Z",
        "severity": "High"
    }

]


# ==================================================
# HISTORICAL FAULT DIAGNOSIS
# ==================================================

historical_fault_events = (
    PUMP_06_FAULTS
    if selected_pump == "PUMP-06"
    else get_historical_fault_events(selected_pump)
)

if selected_pump == "PUMP-06":
    st.divider()

    st.subheader("📋 Historical Fault Events")

    st.caption(
        "The following fault events were identified in "
        f"the historical data of {selected_pump}. "
        "Select an event to investigate its telemetry."
    )

    # Latest equipment information
    data = get_telemetry(selected_pump)

    if not data.empty:

        latest = data.iloc[-1]

        st.markdown("### Latest Equipment Information")

        col1, col2 = st.columns(2)

        with col1:

            st.metric(
                "Operating Mode",
                str(
                    latest.get(
                        "operating_mode",
                        "Unknown"
                    )
                ).upper()
            )

        with col2:

            motor_current = latest.get(
                "motor_current_a"
            )

            st.metric(
                "Motor Current",
                (
                    f"{motor_current:.2f} A"
                    if pd.notna(motor_current)
                    else "N/A"
                )
            )

        st.caption(
            f"Latest recorded timestamp: "
            f"{latest['timestamp_utc']}"
        )

    st.divider()

elif historical_fault_events:

    st.divider()

    st.subheader("📋 Historical Fault Events")

    st.caption(
        "The following fault events were identified in "
        f"the historical data of {selected_pump}. "
        "Select an event to investigate its telemetry."
    )


# Display historical fault events
if historical_fault_events:

    for index, event in enumerate(historical_fault_events):

        fault_name = event["fault"]

        fault_timestamp = pd.to_datetime(
            event["timestamp"],
            utc=True
        )

        with st.container(border=True):

            col1, col2 = st.columns([4, 1])

            with col1:

                st.markdown(
                    f"### 🔴 {fault_name}"
                )

                st.caption(
                    "🕒 "
                    + fault_timestamp.strftime(
                        "%d %b %Y, %H:%M:%S UTC"
                    )
                )

                severity = event.get("severity")

                if severity == "High":

                    st.error(
                        f"Severity: {severity}"
                    )

                elif severity:

                    st.warning(
                        f"Severity: {severity}"
                    )

                else:

                    alert_details = []

                    if event.get("event_class"):
                        alert_details.append(
                            f"Class: {event['event_class']}"
                        )

                    confidence = event.get("confidence")

                    if confidence is not None:
                        alert_details.append(
                            f"Confidence: {confidence:.0%}"
                        )

                    if alert_details:
                        st.caption(" | ".join(alert_details))

                evidence = event.get("evidence")
                recommended_action = event.get("recommended_action")

                if evidence or recommended_action:

                    with st.expander("Alert details"):

                        if evidence:
                            st.markdown("**Evidence**")
                            st.write(evidence)

                        if recommended_action:
                            st.markdown("**Recommended action**")
                            st.write(recommended_action)

            with col2:

                st.write("")

                if st.button(
                    "📈 Replay",
                    key=f"fault_replay_{selected_pump}_{index}",
                    use_container_width=True
                ):

                    # Pass selected pump to History Replay
                    st.session_state[
                        "history_pump_from_fault"
                    ] = selected_pump

                    # Pass fault timestamp
                    st.session_state[
                        "history_target_timestamp"
                    ] = event["timestamp"]

                    # Pass fault name
                    st.session_state[
                        "selected_fault"
                    ] = fault_name

                    # Navigate to History Replay
                    st.switch_page(
                        "pages/5_History_Replay.py"
                    )


# ==================================================
# ILLUSTRATIVE DIAGNOSES
# ==================================================

if selected_pump in demo_diagnoses:

    result = demo_diagnoses[selected_pump]

    st.divider()

    st.subheader("Prediction Result")

    st.markdown(
        f"### {result['fault']}"
    )

    st.write(
        f"Classification: {result['class']}"
    )

    st.progress(
        result["confidence"]
    )

    st.write(
        f"Illustrative confidence: "
        f"{result['confidence']:.0%}"
    )

    st.divider()

    st.subheader("Supporting Evidence")

    for item in result["evidence"]:

        st.success(item)

    st.info(
        "These are illustrative frontend predictions. "
        "Actual model outputs will be integrated later."
    )


# ==================================================
# OTHER PUMPS: DIAGNOSIS NOT YET AVAILABLE
# ==================================================

elif selected_pump != "PUMP-06":

    st.divider()

    st.info(
        f"No current model diagnosis is configured "
        f"for {selected_pump}."
    )

    st.warning(
        "No current fault prediction has been generated for this pump."
    )
