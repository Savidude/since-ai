
import streamlit as st
import pandas as pd

from utils.ui_components import (
    setup_page,
    render_sidebar
)

from utils.demo_data import (
    get_pump_data,
    get_historical_fault_events
)


# ==================================================
# PAGE CONFIGURATION
# ==================================================

setup_page("Fault Diagnosis", "🧠")

render_sidebar()

st.title("AI Fault Diagnosis")
st.subheader("Historical fault records and supporting details.")
st.divider()


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

st.subheader("Select Pump")

selected_pump = st.selectbox(
    "Select Pump",
    pump_ids,
    index=pump_ids.index("PUMP-02") if "PUMP-02" in pump_ids else 0,
    key="fault_diagnosis_pump",
    label_visibility="collapsed"
)


# ==================================================
# HISTORICAL FAULT DIAGNOSIS
# ==================================================

historical_fault_events = get_historical_fault_events(selected_pump)

if not historical_fault_events:
    st.markdown(
        """
        <div style="
            min-height: 45vh;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            text-align: center;
        ">
            <p style="font-size: 1.75rem; margin-bottom: 0.5rem;">
                Faults have not been detected.
            </p>
            <p style="font-size: 1.1rem;">
                Please select another pump.
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )
    st.stop()

st.divider()

st.markdown(
    """
    <style>
    div[data-testid="stVerticalBlock"][class*="st-key-fault-event-"] {
        margin-bottom: 1rem;
        border: 2px solid #52647b !important;
        border-radius: 12px;
        background: #ffffff;
        box-shadow: 0 3px 10px rgba(15, 35, 65, 0.12);
    }

    [class*="st-key-fault-event-"] [data-testid="stExpander"] summary p {
        font-size: 1rem !important;
    }

    [class*="st-key-fault_replay_"] button {
        color: #15395f !important;
        background: #e8f1ff !important;
        border-color: #bfd1e5 !important;
        font-size: 1.1rem !important;
        font-weight: 700 !important;
    }

    [class*="st-key-fault_replay_"] button p {
        font-size: 1.1rem !important;
        font-weight: 700 !important;
    }

    [class*="st-key-fault_replay_"] button [data-testid="stIconMaterial"] {
        font-size: 1.6rem !important;
        line-height: 1 !important;
    }

    [class*="st-key-fault_replay_"] button:hover {
        color: #10213d !important;
        background: #dceaff !important;
        border-color: #a9bfd9 !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.subheader("📋 Historical Fault Events")

for index, event in enumerate(historical_fault_events):

    fault_name = event["fault"]
    fault_timestamp = pd.to_datetime(
        event["timestamp"],
        utc=True
    )

    with st.container(
        border=True,
        key=f"fault-event-{selected_pump}-{index}"
    ):

        event_column, replay_column = st.columns([6, 1])

        with event_column:
            st.markdown(f"**{fault_name}**")

            event_metadata = [
                "🕒 "
                + fault_timestamp.strftime("%d %b %Y, %H:%M:%S UTC")
            ]

            severity = str(event.get("severity") or "").strip()
            if severity:
                severity_icon = {
                    "high": "🔴",
                    "medium": "🟠",
                    "low": "🟡",
                }.get(severity.casefold(), "⚪")
                event_metadata.append(f"{severity_icon} {severity}")

            if event.get("event_class"):
                event_metadata.append(f"Class: {event['event_class']}")

            confidence = event.get("confidence")
            if confidence is not None:
                event_metadata.append(f"Confidence: {confidence:.0%}")

            st.caption(" · ".join(event_metadata))

        with replay_column:
            if st.button(
                "Replay",
                key=f"fault_replay_{selected_pump}_{index}",
                icon=":material/play_arrow:",
                use_container_width=True
            ):

                st.session_state[
                    "history_pump_from_fault"
                ] = selected_pump

                st.session_state[
                    "history_target_timestamp"
                ] = event["timestamp"]

                st.session_state[
                    "selected_fault"
                ] = fault_name

                st.session_state[
                    "history_fault_severity"
                ] = event.get("severity")

                st.switch_page(
                    "pages/5_History_Replay.py"
                )

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
