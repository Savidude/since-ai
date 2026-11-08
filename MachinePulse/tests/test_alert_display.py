from streamlit.testing.v1 import AppTest


def test_current_alert_accordion_shows_latest_fault_state() -> None:
    app = AppTest.from_string(
        """
from utils.alert_display import render_current_alert_accordion

render_current_alert_accordion({
    "asset_id": "PUMP-06",
    "minute_utc": "2025-10-09T21:50:00Z",
    "phase": "alert",
    "event_class": "MACHINE",
    "fault_type": "BEARING_WEAR",
    "confidence": 0.64,
    "evidence": "Vibration and pressure anomalies.",
    "recommended_action": "Inspect the bearing.",
})
"""
    ).run()

    assert not app.exception
    assert len(app.expander) == 1
    assert app.expander[0].label == (
        "Current system status — Fault detected: BEARING_WEAR"
    )
    assert not any("Current fault status" in item.value for item in app.error)
    assert any("#b91c1c" in item.value for item in app.markdown)


def test_current_alert_accordion_shows_waiting_state_without_history() -> None:
    app = AppTest.from_string(
        """
from utils.alert_display import render_current_alert_accordion

render_current_alert_accordion(None)
"""
    ).run()

    assert not app.exception
    assert len(app.expander) == 1
    assert app.expander[0].label == (
        "Current system status — Waiting for first update"
    )
    assert any(
        "Waiting for the first status update from this pump." in item.value
        for item in app.info
    )


def test_current_alert_accordion_colors_healthy_status_green() -> None:
    app = AppTest.from_string(
        """
from utils.alert_display import render_current_alert_accordion

render_current_alert_accordion({"phase": "watching"})
"""
    ).run()

    assert not app.exception
    assert any("#15803d" in item.value for item in app.markdown)
    assert not any(
        "System status OK — no active fault identified." in item.value
        for item in app.success
    )
    assert not any(
        "The simulator is watching the pump." in item.value
        for item in app.markdown
    )


def test_watching_alert_displays_system_ok_details() -> None:
    app = AppTest.from_string(
        """
from utils.alert_display import render_alert_mini_player, render_alert_status

alert = {
    "asset_id": "PUMP-06",
    "minute_utc": "2025-10-09T21:46:00Z",
    "phase": "watching",
    "event_class": None,
    "fault_type": None,
    "confidence": None,
    "evidence": "",
    "recommended_action": "",
}
render_alert_mini_player(alert, asset_id="PUMP-06")
render_alert_status(alert)
"""
    ).run()

    assert not app.exception
    assert any("SYSTEM STATUS OK" in item.value for item in app.success)
    assert any("System status OK" in item.value for item in app.success)
    assert {
        metric.label: metric.value for metric in app.metric
    } == {
        "Event Class": "None",
        "Fault Type": "None",
        "Confidence": "N/A",
    }


def test_missing_alert_does_not_claim_system_is_ok() -> None:
    app = AppTest.from_string(
        """
from utils.alert_display import render_alert_mini_player

render_alert_mini_player(None, asset_id="PUMP-06")
"""
    ).run()

    assert not app.exception
    assert any(
        "Waiting for the first status message" in item.value for item in app.info
    )
    assert not any("SYSTEM STATUS OK" in item.value for item in app.success)
