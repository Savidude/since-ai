import streamlit as st
import pandas as pd

from utils.ui_components import (
    setup_page,
    render_sidebar,
    render_hero_banner,
    render_status_badge
)

from utils.demo_data import get_pump_data

PUMP_COUNT = 12


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


# ==================================================
# KPI CARDS
# ==================================================

st.subheader("System Overview")

kpi_columns = st.columns(3)

kpis = [
    ("Monitored Pumps", PUMP_COUNT, "Total monitored equipment"),
    ("Online", PUMP_COUNT, "Connected to the system"),
    ("Offline", 0, "Not connected to the system"),
]

for column, (label, value, help_text) in zip(kpi_columns, kpis):
    with column:
        st.metric(label, value, help=help_text)

st.divider()


# ==================================================
# EQUIPMENT HEALTH
# ==================================================

st.subheader("Equipment Health")
st.markdown(
    '<div class="pg-section-caption">'
    'Browse the machine hierarchy and select a pump to open its '
    'individual monitoring page.'
    '</div>',
    unsafe_allow_html=True
)

equipment_hierarchy = [
    (
        "Machine 01 · Raw Water Intake",
        [
            (
                "Intake Assembly",
                [
                    ("Suction Manifold", ("PUMP-01", "PUMP-02")),
                    ("Transfer Skid", ("PUMP-03",)),
                ],
            ),
            (
                "Screen Wash Unit",
                [
                    ("Wash Circuit", ("PUMP-04",)),
                ],
            ),
        ],
    ),
    (
        "Machine 02 · Treatment & Filtration",
        [
            (
                "Filtration Train A",
                [
                    ("Feed Stage", ("PUMP-05", "PUMP-06")),
                    ("Backwash Stage", ("PUMP-07",)),
                ],
            ),
            (
                "Chemical Dosing Unit",
                [
                    ("Dosing Module", ("PUMP-08",)),
                ],
            ),
        ],
    ),
    (
        "Machine 03 · Distribution & Utilities",
        [
            (
                "Distribution Train",
                [
                    ("Header Pressurization", ("PUMP-09", "PUMP-10")),
                    ("Recirculation Loop", ("PUMP-11",)),
                ],
            ),
            (
                "Cooling Water Circuit",
                [
                    ("Cooling Module", ("PUMP-12",)),
                ],
            ),
        ],
    ),
]

pumps_by_id = {
    str(pump["pump_id"]): pump
    for _, pump in pumps.iterrows()
}

cards_per_row = 4

for machine_index, (machine_name, components) in enumerate(
    equipment_hierarchy,
    start=1
):
    with st.container(border=True, key=f"machine-group-{machine_index}"):
        st.markdown(
            '<div class="pg-machine-heading">'
            f'<span class="pg-machine-kicker">MACHINE {machine_index:02d}</span>'
            f'<span class="pg-machine-title">{machine_name.split(" · ", 1)[1]}</span>'
            '</div>',
            unsafe_allow_html=True
        )

        for component_name, subcomponents in components:
            st.markdown(
                '<div class="pg-tree-component">'
                f'<span class="pg-tree-branch">├─</span>'
                f'<span>{component_name}</span>'
                '</div>',
                unsafe_allow_html=True
            )

            for subcomponent_name, pump_ids in subcomponents:
                with st.expander(
                    f"└─ {subcomponent_name}",
                    expanded=True
                ):
                    available_pumps = [
                        pumps_by_id[pump_id]
                        for pump_id in pump_ids
                        if pump_id in pumps_by_id
                    ]
                    for start in range(
                        0,
                        len(available_pumps),
                        cards_per_row
                    ):
                        batch = available_pumps[
                            start:start + cards_per_row
                        ]
                        columns = st.columns(cards_per_row)

                        for column, pump in zip(columns, batch):
                            pump_id = str(pump["pump_id"])
                            tier = str(pump.get("tier", "Unknown"))

                            with column:
                                with st.container(
                                    key=f"pump-card-{pump_id}"
                                ):
                                    top_left, top_right = st.columns(
                                        [1.15, 1]
                                    )

                                    with top_left:
                                        st.markdown(
                                            '<div class="pg-pump-title">'
                                            f'{pump_id}</div>',
                                            unsafe_allow_html=True
                                        )

                                    with top_right:
                                        render_status_badge("ONLINE")

                                    st.markdown(
                                        '<div class="pg-pump-meta">'
                                        f'{tier} · Instrumentation available'
                                        '</div>',
                                        unsafe_allow_html=True
                                    )

                                    if st.button(
                                        "View Pump →",
                                        key=f"monitor_{pump_id}",
                                        width="stretch"
                                    ):
                                        st.session_state[
                                            "selected_pump"
                                        ] = pump_id
                                        st.switch_page(
                                            "pages/2_Pump_Monitoring.py"
                                        )


st.caption(
    "Illustrative equipment hierarchy. Pump availability is hardcoded "
    "for this demo."
)
