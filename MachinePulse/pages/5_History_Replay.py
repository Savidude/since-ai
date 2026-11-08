
from datetime import timedelta
import time

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from backend import MqttReplaySession, get_replay_timestamp_utc

from utils.demo_data import (
    get_pump_data,
    get_telemetry
)

from utils.telemetry_charts import (
    SENSOR_CHART_OPTIONS,
    render_sensor_charts,
)
from utils.alert_display import render_alert_mini_player, render_alert_status

from utils.replay_progress import REPLAY_STAGES, get_replay_stage_index

from utils.what_if import (
    ACTION_LABELS,
    hypothesis_bounds,
    run_historical_what_if,
    scenario_for_fault,
)

from utils.ui_components import (
    setup_page,
    render_sidebar
)

REPLAY_SESSION_KEY = "history_replay_session"
WHAT_IF_RESULT_KEY = "history_what_if_result"


def _format_what_if_value(
    value,
    *,
    suffix="",
    multiplier=1.0,
    precision=2,
):
    if value is None:
        return "N/A"
    return f"{float(value) * multiplier:.{precision}f}{suffix}"


def _render_what_if_result(result):
    if result.get("accepted"):
        st.success(
            "The proposal passed the sandbox checks. "
            "This is not field approval."
        )
    else:
        st.error("The proposal did not pass the sandbox checks.")

    for reason in result.get("rejection_reasons", []):
        st.error(reason)

    for warning in result.get("warnings", []):
        st.warning(warning)

    baseline = result.get("baseline")
    proposed = result.get("proposed")
    if not isinstance(baseline, dict) or not isinstance(proposed, dict):
        st.info("The sandbox did not produce a baseline/proposed comparison.")
        return

    metric_fields = (
        ("Flow", "flow_m3h", " m3/h", 1.0, 1),
        ("Required flow", "flow_required_m3h", " m3/h", 1.0, 1),
        ("Duty satisfaction", "duty_satisfaction", "%", 100.0, 1),
        ("Input power", "input_kw", " kW", 1.0, 2),
        ("Energy cost per day", "energy_eur_per_day", " EUR/day", 1.0, 2),
        ("NPSH margin ratio", "npsh_margin", "", 1.0, 2),
        ("Vibration RMS", "vib_rms_mm_s", " mm/s", 1.0, 2),
        ("Bearing temperature", "bearing_temp_c", " C", 1.0, 1),
        ("Risk proxy (not probability)", "risk_proxy", "", 1.0, 3),
    )
    rows = [
        {
            "Measure": label,
            "Baseline": _format_what_if_value(
                baseline.get(field),
                suffix=suffix,
                multiplier=multiplier,
                precision=precision,
            ),
            "Proposed": _format_what_if_value(
                proposed.get(field),
                suffix=suffix,
                multiplier=multiplier,
                precision=precision,
            ),
        }
        for label, field, suffix, multiplier, precision in metric_fields
    ]

    intervention = result.get("intervention", {})
    estimated_cost = intervention.get("estimated_cost_eur")
    if estimated_cost is not None:
        rows.append(
            {
                "Measure": "One-time estimated intervention cost",
                "Baseline": "N/A",
                "Proposed": _format_what_if_value(
                    estimated_cost,
                    suffix=" EUR",
                    precision=0,
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
    )

    caveats = result.get("caveats", [])
    if caveats:
        st.markdown("**Model caveats**")
        for caveat in caveats:
            st.caption(caveat)


@st.dialog("Pump simulator replay", width="large")
def _show_replay_waiting_dialog(
    replay_session: MqttReplaySession,
) -> None:
    if (
        replay_session.error
        or replay_session.status == "stopped"
        or replay_session.ready
    ):
        st.rerun()
        return

    replay_published_at = replay_session.published_at
    elapsed_seconds = (
        None
        if replay_published_at is None
        else max(0.0, time.monotonic() - replay_published_at)
    )

    st.markdown(f"### Preparing replay for {replay_session.asset_id}")
    st.caption(
        "The simulator is ready when the application receives its first "
        "alert."
    )

    if elapsed_seconds is None:
        stage_index = None
        st.status(
            "Connecting to the pump simulator...",
            state="running",
            expanded=False,
        )
        st.caption("Preparing the replay request.")
    else:
        stage_index = get_replay_stage_index(elapsed_seconds)
        active_title = REPLAY_STAGES[stage_index]
        st.status(
            f"Step {stage_index + 1} of {len(REPLAY_STAGES)}: {active_title}",
            state="running",
            expanded=False,
        )

        if replay_session.status == "reconnecting":
            st.warning(
                "The simulator connection is reconnecting. Replay progress will "
                "resume automatically when it is restored."
            )

        if elapsed_seconds >= 60:
            st.warning(
                "The simulator is taking longer than expected. "
                "This page will keep waiting for the first alert."
            )

    st.markdown("#### Replay stages")
    for index, title in enumerate(REPLAY_STAGES):
        if stage_index is None or index > stage_index:
            icon = "⏳"
            status_text = "Not started"
        elif index < stage_index:
            icon = "✅"
            status_text = "Complete"
        else:
            icon = "🔄"
            status_text = "In progress"

        st.markdown(f"{icon} **{index + 1}. {title}** — {status_text}")

    if st.button("Cancel replay", key="cancel_history_replay"):
        replay_session.stop()
        st.session_state.pop(REPLAY_SESSION_KEY, None)
        st.rerun()

    st_autorefresh(
        interval=1000,
        key=f"history_replay_waiting_refresh_{replay_session.asset_id}",
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

requested_fault_severity = st.session_state.pop(
    "history_fault_severity",
    None
)


# ==================================================
# PAGE CONFIGURATION
# ==================================================

setup_page("History & Replay", "⏪")
render_sidebar()

st.title("Historical Analysis & Replay")
st.subheader("Investigate previous pump conditions and fault development.")
st.divider()


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

st.subheader("Select Pump")
pump = st.selectbox(
    "Select Pump",
    pump_ids,
    key="history_pump",
    label_visibility="collapsed"
)

replay_session = st.session_state.get(REPLAY_SESSION_KEY)
if replay_session is not None and replay_session.asset_id != pump:
    replay_session.stop()
    st.session_state.pop(REPLAY_SESSION_KEY, None)
    replay_session = None


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
    st.session_state.pop("replay_fault_severity", None)


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

        if requested_fault_severity is not None:
            st.session_state["replay_fault_severity"] = (
                requested_fault_severity
            )
        else:
            st.session_state.pop("replay_fault_severity", None)

        # Warn if the requested event is outside the available data
        if target_display < first_timestamp or target_display > last_timestamp:
            st.warning(
                "The fault timestamp is outside the available telemetry "
                "period. Showing the closest available measurement instead."
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

st.markdown(
    """
    <style>
    [class*="st-key-history_search_button"] button {
        color: #15395f !important;
        background: #e8f1ff !important;
        border-color: #bfd1e5 !important;
    }

    [class*="st-key-history_search_button"] button:hover {
        color: #10213d !important;
        background: #dceaff !important;
        border-color: #a9bfd9 !important;
    }

    [class*="st-key-history_replay_button"] button {
        color: #15395f !important;
        background: #e8f1ff !important;
        border-color: #bfd1e5 !important;
    }

    [class*="st-key-history_replay_button"] button:hover {
        color: #10213d !important;
        background: #dceaff !important;
        border-color: #a9bfd9 !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

col1, col2, col3 = st.columns(
    [2, 2, 1],
    vertical_alignment="bottom"
)

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
    search_clicked = st.button(
        "🔍 Search",
        width="stretch",
        type="secondary",
        key="history_search_button"
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
    st.session_state.pop("replay_fault_severity", None)

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
        st.session_state.pop("replay_fault_severity", None)

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
        st.session_state.pop("replay_fault_severity", None)

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
replay_fault_severity = st.session_state.get(
    "replay_fault_severity"
)

if replay_fault_name and replay_fault_timestamp is not None:
    fault_event_message = (
        f"Fault event: **{replay_fault_name}**  \n"
        f"Event timestamp: "
        f"{replay_fault_timestamp.strftime('%d %B %Y, %H:%M:%S UTC')}"
    )
    if (
        replay_fault_severity is not None
        and str(replay_fault_severity).strip()
    ):
        fault_event_message += f"  \nAlert severity: {replay_fault_severity}"
    st.warning(fault_event_message)


# ==================================================
# OPERATING CONDITIONS
# ==================================================

st.divider()

st.subheader("Operating Conditions")

st.markdown(
    """
    <style>
    [class*="st-key-history_what_if_container"] [data-testid="stExpander"] {
        border: 2px solid #8aaed6 !important;
        border-radius: 12px !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

if replay_fault_name and replay_fault_timestamp is not None:
    what_if_container = st.container(key="history_what_if_container")
    with what_if_container.expander(
        "🧪 Try a what-if: explore a maintenance action",
        expanded=True,
    ):
        st.caption(
            "Evaluate a hypothetical action at the selected historical "
            "operating point. This simulation does not control equipment "
            "or replay the future fault progression."
        )
        st.write(
            f"Pump: **{pump}**  \n"
            f"Selected simulation time: "
            f"**{selected_timestamp_utc.strftime('%d %B %Y, %H:%M:%S UTC')}**"
        )

        scenario = scenario_for_fault(replay_fault_name)
        if scenario is None:
            st.info(
                f"What-if analysis is not available for "
                f"{replay_fault_name}. The simulator only covers selected "
                "mechanical and hydraulic fault hypotheses."
            )
        else:
            hypothesis_key, allowed_actions = scenario
            lower, upper = hypothesis_bounds(hypothesis_key)
            event_key = pd.Timestamp(replay_fault_timestamp).strftime(
                "%Y%m%dT%H%M%S"
            )
            form_key = (
                f"history_what_if_{pump}_{replay_fault_name}_{event_key}"
            )

            with st.form(key=form_key):
                action = st.selectbox(
                    "Action to evaluate",
                    allowed_actions,
                    format_func=lambda item: ACTION_LABELS[item],
                    key=f"{form_key}_action",
                )
                hypothesis_value = st.text_input(
                    f"Assumed {hypothesis_key} model value "
                    f"({lower:g} to {upper:g})",
                    placeholder="Required",
                    help=(
                        "This is a user-supplied model assumption, not a "
                        "measured value. Alert confidence or categorical "
                        "severity is not converted into this number."
                    ),
                    key=f"{form_key}_hypothesis",
                )
                st.caption(
                    "The numeric condition assumption is separate from any "
                    "severity field supplied by the alert."
                )
                submitted = st.form_submit_button(
                    "Run what-if simulation",
                    type="primary",
                )

            scenario_signature = (
                pump,
                replay_fault_name,
                selected_timestamp_utc.isoformat(),
                action,
                hypothesis_value.strip(),
            )
            if submitted:
                try:
                    result = run_historical_what_if(
                        pump,
                        selected_timestamp_utc.isoformat(),
                        replay_fault_name,
                        action,
                        hypothesis_value,
                    )
                except (FileNotFoundError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.session_state[WHAT_IF_RESULT_KEY] = {
                        "signature": scenario_signature,
                        "result": result,
                    }

            stored_result = st.session_state.get(WHAT_IF_RESULT_KEY)
            if (
                stored_result is not None
                and stored_result.get("signature") == scenario_signature
            ):
                _render_what_if_result(stored_result["result"])

if st.button(
    "▶ Replay",
    key="history_replay_button",
    help="Request telemetry from 10 minutes before the selected historical time.",
    width="stretch",
):
    if replay_session is not None:
        replay_session.stop()

    fault_timestamp = (
        pd.Timestamp(replay_fault_timestamp).to_pydatetime()
        if replay_fault_timestamp is not None
        else None
    )
    replay_start_timestamp = get_replay_timestamp_utc(
        selected_timestamp_utc.to_pydatetime(),
        fault_timestamp,
    )
    replay_session = MqttReplaySession(
        pump,
        replay_start_timestamp,
    )
    st.session_state[REPLAY_SESSION_KEY] = replay_session

if replay_session is not None:
    replay_status = replay_session.status

    if replay_session.error:
        st.error("The replay could not be completed. Please try again.")
    elif replay_status == "fault_detected":
        st.success(
            "Fault detected during replay. Review the current alert details below."
        )
    elif replay_status == "stopped":
        st.info("The replay session has stopped. Select Replay to start again.")
    elif replay_session.ready:
        st.success("Pump simulator is ready; receiving alert updates.")
        st_autorefresh(
            interval=1000,
            key=f"history_replay_live_refresh_{pump}",
        )
    else:
        _show_replay_waiting_dialog(replay_session)

    st.caption(
        "Replay start: "
        f"{replay_session.request_timestamp_utc.strftime('%d %b %Y %H:%M:%S UTC')}"
    )

    if replay_session.alert_error:
        st.warning("Live alert updates are temporarily unavailable.")

    current_alert = replay_session.latest_alert
    if current_alert is not None:
        render_alert_mini_player(current_alert, asset_id=pump)
        render_alert_status(current_alert)

# ==================================================
# SENSOR MEASUREMENTS
# ==================================================

st.divider()

st.subheader("Historical Sensor Measurements")

live_replay_data = pd.DataFrame()
if replay_session is not None:
    replay_records = replay_session.get_telemetry_records()
    if replay_records:
        live_replay_data = pd.DataFrame(replay_records)
        if "timestamp_utc" not in live_replay_data.columns:
            st.warning("Replay data is missing timestamps and cannot be displayed.")
            live_replay_data = pd.DataFrame()
        else:
            live_replay_data["timestamp_utc"] = pd.to_datetime(
                live_replay_data["timestamp_utc"],
                utc=True,
                errors="coerce",
            )
            live_replay_data = (
                live_replay_data.dropna(subset=["timestamp_utc"])
                .sort_values("timestamp_utc")
                .reset_index(drop=True)
            )
            if live_replay_data.empty:
                st.warning("Replay data contains no valid timestamps.")

if not live_replay_data.empty:
    st.caption(
        f"Showing {len(live_replay_data):,} replay telemetry measurements."
    )
    render_sensor_charts(
        live_replay_data,
        empty_message="No valid replay sensor measurements available.",
    )
else:
    if replay_session is not None and replay_session.ready:
        st.info(
            "The simulator is ready. Waiting for replay telemetry."
        )

    plotted_charts = 0

    for label, (column, unit) in SENSOR_CHART_OPTIONS.items():

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
