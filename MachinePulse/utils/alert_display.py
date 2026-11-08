from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from backend.mqtt_telemetry import is_fault_alert

_ALERT_MINI_PLAYER_STYLE = """
<style>
.st-key-alert_status_mini_player {
    position: fixed !important;
    right: 1.25rem;
    bottom: 1.25rem;
    z-index: 9999;
    width: min(22rem, calc(100vw - 2rem));
    max-height: 16rem;
    overflow-y: auto;
    padding: 0.75rem 1rem;
    border: 1px solid var(--pg-border);
    border-radius: 14px;
    background: #ffffff;
    box-shadow: 0 8px 28px rgba(15, 35, 65, 0.2);
}

.st-key-alert_status_mini_player [data-testid="stAlert"] {
    margin: 0.35rem 0;
}

@media (max-width: 640px) {
    .st-key-alert_status_mini_player {
        right: 0.75rem;
        bottom: 0.75rem;
        left: 0.75rem;
        width: auto;
    }
}
</style>
"""


def _format_confidence(confidence: Any, *, healthy: bool = False) -> str:
    if confidence is None:
        return "N/A" if healthy else "—"

    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        return str(confidence)

    if pd.isna(confidence_value):
        return "N/A" if healthy else "—"

    return f"{confidence_value:.2f} ({confidence_value:.0%})"


def _last_updated(alert: dict[str, Any]) -> str | None:
    for field in ("alert_time_utc", "minute_utc", "timestamp_utc"):
        value = alert.get(field)
        if value:
            return str(value)
    return None


def current_alert_state_label(
    alert: dict[str, Any] | None,
    *,
    unavailable: bool = False,
) -> str:
    if alert is None:
        return "Unavailable" if unavailable else "Waiting for first update"

    phase = str(alert.get("phase") or "").lower()
    if is_fault_alert(alert):
        fault_type = alert.get("fault_type") or phase.upper() or "ALERT"
        return f"Fault detected: {fault_type}"
    if phase == "watching":
        return "System OK"
    return "Status update received"


def render_alert_status(
    alert: dict[str, Any],
    *,
    show_status_summary: bool = True,
) -> None:
    phase = str(alert.get("phase") or "").lower()

    if show_status_summary:
        if is_fault_alert(alert):
            st.error(f"Current fault status: {phase.upper() or 'ALERT'}")
        elif phase == "watching":
            st.success("System status OK — no active fault identified.")
        else:
            st.success("Current fault status: No fault identified")

    updated_at = _last_updated(alert)
    if updated_at:
        st.caption(f"Latest status update: {updated_at}")

    alert_columns = st.columns(3)
    alert_columns[0].metric(
        "Event Class",
        str(alert.get("event_class") or ("None" if phase == "watching" else "—")),
    )
    alert_columns[1].metric(
        "Fault Type",
        str(alert.get("fault_type") or ("None" if phase == "watching" else "—")),
    )
    alert_columns[2].metric(
        "Confidence",
        _format_confidence(alert.get("confidence"), healthy=phase == "watching"),
    )

    st.markdown("**Evidence**")
    evidence = alert.get("evidence")
    st.write(
        str(evidence)
        if evidence
        else "No fault evidence reported."
        if phase == "watching"
        else "—"
    )

    st.markdown("**Recommended action**")
    recommended_action = alert.get("recommended_action")
    st.write(
        str(recommended_action)
        if recommended_action
        else "No corrective action required; continue normal monitoring."
        if phase == "watching"
        else "—"
    )


def render_current_alert_accordion(
    alert: dict[str, Any] | None,
    *,
    unavailable: bool = False,
) -> None:
    state_label = current_alert_state_label(alert, unavailable=unavailable)
    heading_color = None
    if alert is not None:
        phase = str(alert.get("phase") or "").lower()
        if is_fault_alert(alert):
            heading_color = "#b91c1c"
        elif phase == "watching":
            heading_color = "#15803d"

    with st.container(key="current_system_status_accordion"):
        if heading_color:
            st.markdown(
                f"""
                <style>
                .st-key-current_system_status_accordion
                [data-testid="stExpander"] summary,
                .st-key-current_system_status_accordion
                [data-testid="stExpander"] summary p {{
                    color: #e5e7eb !important;
                    font-size: 1.15rem !important;
                    font-weight: 700 !important;
                }}
                .st-key-current_system_status_accordion
                [data-testid="stExpander"] summary {{
                    background-color: {heading_color} !important;
                    border-radius: 0.5rem;
                }}
                .st-key-current_system_status_accordion
                [data-testid="stExpander"] summary svg {{
                    fill: #ffffff !important;
                }}
                </style>
                """,
                unsafe_allow_html=True,
            )

        with st.expander(
            f"Current system status — {state_label}",
            expanded=alert is not None and is_fault_alert(alert),
        ):
            if alert is not None:
                if unavailable:
                    st.warning(
                        "Live status updates are temporarily unavailable. "
                        "Showing the most recent status received."
                    )
                render_alert_status(alert, show_status_summary=False)
            elif unavailable:
                st.warning("The current system status is temporarily unavailable.")
            else:
                st.info("Waiting for the first status update from this pump.")


def render_alert_mini_player(
    alert: dict[str, Any] | None,
    *,
    asset_id: str,
    unavailable_message: str | None = None,
) -> None:
    st.markdown(_ALERT_MINI_PLAYER_STYLE, unsafe_allow_html=True)
    with st.container(key="alert_status_mini_player", border=True):
        st.caption(f"{asset_id} · LIVE ALERT STATUS")

        if alert is None:
            if unavailable_message:
                st.error("Alert status unavailable")
                st.write(unavailable_message)
            else:
                st.info("Waiting for the first status message")
                st.write(
                    "No alert has arrived yet, so the pump's health is not "
                    "confirmed."
                )
            return

        phase = str(alert.get("phase") or "").lower()
        if is_fault_alert(alert):
            st.error("FAULT DETECTED")
            fault_type = alert.get("fault_type") or phase.upper() or "ALERT"
            st.markdown(f"**{fault_type}**")
            st.caption(
                f"Confidence: {_format_confidence(alert.get('confidence'))}"
            )
        elif phase == "watching":
            st.success("SYSTEM STATUS OK")
            st.write("No active fault detected. Monitoring continues.")
        else:
            st.info("No active fault reported in the latest status")
            st.caption(f"Simulator phase: {phase.upper() or 'UNKNOWN'}")

        updated_at = _last_updated(alert)
        if updated_at:
            st.caption(f"Updated: {updated_at}")
