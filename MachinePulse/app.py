import streamlit as st
import pandas as pd

from utils.ui_components import (
    setup_page,
    render_sidebar,
    render_hero_banner,
    render_status_badge
)

from utils.demo_data import get_pump_data


# ==================================================
# PAGE CONFIGURATION
# ==================================================

setup_page("Overview")
render_sidebar()


# ==================================================
# HERO BANNER
# ==================================================

render_hero_banner()


# ==================================================
# LOAD PUMP DATA
# ==================================================

pumps = get_pump_data()

if pumps.empty:
    st.warning(
        "No pump telemetry files found. "
        "Please check the data folder."
    )
    st.stop()

pumps = pumps.sort_values("pump_id").reset_index(drop=True)

operating_modes = (
    pumps["status"]
    .fillna("UNKNOWN")
    .astype(str)
    .str.upper()
)

running = int((operating_modes == "RUNNING").sum())
stopped = int((operating_modes == "STOPPED").sum())
locked_out = int(
    operating_modes.isin(["LOCKED_OUT", "LOCKED OUT"]).sum()
)
unknown = len(pumps) - running - stopped - locked_out


# ==================================================
# KPI CARDS
# ==================================================

st.subheader("System Overview")

kpi_columns = st.columns(4)

kpis = [
    ("🏭", "Monitored Pumps", len(pumps), "Total monitored equipment"),
    ("📈", "Running", running, "Operating pumps"),
    ("⚠️", "Locked Out", locked_out, "Equipment locked out"),
    ("⏸️", "Stopped", stopped, "Not operating"),
]

for column, (icon, label, value, help_text) in zip(kpi_columns, kpis):
    with column:
        with st.container(border=True):
            st.markdown(
                f"<div style='font-size:25px'>{icon}</div>",
                unsafe_allow_html=True
            )
            st.metric(label, value, help=help_text)


if unknown > 0:
    st.caption(f"Additional pumps with unknown operating status: {unknown}")

st.divider()


# ==================================================
# EQUIPMENT HEALTH
# ==================================================

st.subheader("Equipment Health")
st.markdown(
    '<div class="pg-section-caption">'
    'Select a pump to open its individual monitoring page.'
    '</div>',
    unsafe_allow_html=True
)

# Four compact cards per row, as in the proposed mockup
cards_per_row = 4

for start in range(0, len(pumps), cards_per_row):
    columns = st.columns(cards_per_row)
    batch = pumps.iloc[start:start + cards_per_row]

    for column, (_, pump) in zip(columns, batch.iterrows()):
        pump_id = str(pump["pump_id"])
        status = str(pump.get("status", "UNKNOWN"))
        tier = str(pump.get("tier", "Unknown"))

        with column:
            with st.container(border=True):
                top_left, top_right = st.columns([1.15, 1])

                with top_left:
                    st.markdown(
                        f'<div class="pg-pump-title">{pump_id}</div>',
                        unsafe_allow_html=True
                    )

                with top_right:
                    render_status_badge(status)

                st.markdown(
                    f'<div class="pg-pump-meta">'
                    f'{tier} · Instrumentation available'
                    f'</div>',
                    unsafe_allow_html=True
                )

                if st.button(
                    "View Pump →",
                    key=f"monitor_{pump_id}",
                    width="stretch"
                ):
                    st.session_state["selected_pump"] = pump_id
                    st.switch_page("pages/2_Pump_Monitoring.py")


# ==================================================
# RECENT AI ALERTS AND SYSTEM INFORMATION
# ==================================================

st.divider()

left, right = st.columns([1, 1.2])

with left:
    with st.container(border=True):
        st.subheader("🔔 Recent AI Alerts")
        st.info(
            "No connected AI alerts are available yet. "
            "Fault predictions will appear here once the detection "
            "model is integrated."
        )

with right:
    with st.container(border=True):
        st.subheader("ⓘ System Information")

        mode = st.session_state.get("data_mode", "Simulated Live")

        info1, info2, info3 = st.columns(3)

        info1.markdown("**Data Mode**")
        info1.caption(mode)

        info2.markdown("**Telemetry Source**")
        info2.caption("Historical JSONL files")

        info3.markdown("**Operating Mode**")
        info3.caption("READ ONLY")


st.caption(
    "Operating modes are obtained from the uploaded JSONL telemetry. "
    "AI fault predictions are displayed only when model outputs are connected."
)
