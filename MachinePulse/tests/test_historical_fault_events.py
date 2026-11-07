import json

from utils import demo_data


def test_get_historical_fault_events_uses_alert_time_and_details(
    tmp_path,
    monkeypatch,
):
    alerts = [
        {
            "phase": "alert",
            "fault_type": "BEARING_WEAR",
            "minute_utc": "2025-12-03T03:07:00Z",
            "alert_time_utc": "2025-12-03T03:08:00Z",
            "event_class": "MACHINE",
            "confidence": 0.6,
            "evidence": "Vibration increased.",
            "recommended_action": "Inspect the bearing.",
        },
        {
            "phase": "watching",
            "fault_type": None,
            "minute_utc": "2025-12-03T04:07:00Z",
            "alert_time_utc": "2025-12-03T04:08:00Z",
        },
    ]
    (tmp_path / "pump-alerts-PUMP-02.json").write_text(
        json.dumps(alerts),
        encoding="utf-8",
    )
    monkeypatch.setattr(demo_data, "PUMP_ALERTS_DIR", tmp_path)

    assert demo_data.get_historical_fault_events("PUMP-02") == [
        {
            "fault": "BEARING_WEAR",
            "timestamp": "2025-12-03T03:08:00Z",
            "event_class": "MACHINE",
            "confidence": 0.6,
            "evidence": "Vibration increased.",
            "recommended_action": "Inspect the bearing.",
        }
    ]


def test_get_historical_fault_events_returns_empty_when_file_is_missing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(demo_data, "PUMP_ALERTS_DIR", tmp_path)

    assert demo_data.get_historical_fault_events("PUMP-01") == []


def test_get_historical_fault_events_includes_severity_only_when_present(
    tmp_path,
    monkeypatch,
):
    alerts = [
        {
            "phase": "alert",
            "fault_type": "BEARING_WEAR",
            "minute_utc": "2025-12-03T03:07:00Z",
            "severity": "High",
        },
        {
            "phase": "alert",
            "fault_type": "SENSOR_DRIFT",
            "minute_utc": "2025-12-03T04:07:00Z",
        },
    ]
    (tmp_path / "pump-alerts-PUMP-02.json").write_text(
        json.dumps(alerts),
        encoding="utf-8",
    )
    monkeypatch.setattr(demo_data, "PUMP_ALERTS_DIR", tmp_path)

    events = demo_data.get_historical_fault_events("PUMP-02")

    assert events[0]["severity"] == "High"
    assert "severity" not in events[1]
