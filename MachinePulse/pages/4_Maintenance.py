import streamlit as st

from utils.ui_components import (
    setup_page,
    render_sidebar,
    page_header
)


setup_page("Maintenance", "🔧")

render_sidebar()

page_header(
    "Maintenance Recommendations",
    "AI-assisted inspection and maintenance guidance."
)


pump = st.selectbox(
    "Select Pump",
    ["PUMP-01", "PUMP-02", "PUMP-03", "PUMP-04", "PUMP-05"]
)


recommendations = {

    "PUMP-01": {
        "fault": "No fault detected",
        "priority": "Routine monitoring",
        "actions": [
            "Continue normal monitoring",
            "Review scheduled maintenance plan"
        ]
    },

    "PUMP-02": {
        "fault": "Possible vibration anomaly",
        "priority": "Medium",
        "actions": [
            "Inspect pump mounting",
            "Check for mechanical looseness",
            "Review vibration measurements"
        ]
    },

    "PUMP-03": {
        "fault": "Possible bearing wear",
        "priority": "High",
        "actions": [
            "Inspect bearing housing",
            "Check bearing lubrication",
            "Inspect for unusual noise",
            "Review vibration trends"
        ]
    },

    "PUMP-04": {
        "fault": "No fault detected",
        "priority": "Routine monitoring",
        "actions": [
            "Continue normal monitoring"
        ]
    },

    "PUMP-05": {
        "fault": "Possible pressure sensor anomaly",
        "priority": "Medium",
        "actions": [
            "Inspect pressure transmitter",
            "Check sensor connections",
            "Compare readings with other measurements"
        ]
    }

}


result = recommendations[pump]


st.subheader("Detected Issue")

st.markdown(f"### {result['fault']}")

st.write(f"Inspection Priority: {result['priority']}")


st.divider()

st.subheader("Recommended Inspection Actions")


for i, action in enumerate(result["actions"]):

    st.checkbox(
        action,
        key=f"{pump}_action_{i}"
    )


st.divider()

if st.button("Mark as Reviewed"):

    st.success(
        f"Inspection recommendations for {pump} reviewed."
    )


st.caption(
    "Recommendations are advisory only. "
    "Maintenance decisions remain with qualified personnel."
)