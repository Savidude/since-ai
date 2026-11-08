from __future__ import annotations

import pandas as pd

from utils.telemetry_charts import SENSOR_CHART_OPTIONS, build_sensor_figures


def test_build_sensor_figures_uses_shared_sensor_style() -> None:
    data = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(
                ["2025-10-09T21:45:00Z", "2025-10-09T21:46:00Z"],
                utc=True,
            ),
            "motor_current_a": [17.2, 17.5],
            "fluid_temp_c": [42.0, 42.5],
        }
    )

    figures = build_sensor_figures(data)

    assert [label for label, _figure in figures] == [
        "Motor Current",
        "Fluid Temperature",
    ]
    motor_current_figure = figures[0][1]
    assert motor_current_figure.layout.height == 260
    assert motor_current_figure.layout.xaxis.title.text == "Timestamp (UTC)"
    assert motor_current_figure.layout.yaxis.title.text == "Motor Current (A)"
    assert list(motor_current_figure.data[0].y) == [17.2, 17.5]


def test_build_sensor_figures_skips_missing_or_empty_measurements() -> None:
    data = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(["2025-10-09T21:45:00Z"], utc=True),
            "motor_current_a": [None],
        }
    )

    assert build_sensor_figures(data) == []


def test_build_sensor_figures_uses_requested_sensor_options() -> None:
    data = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(["2025-10-09T21:45:00Z"], utc=True),
            "motor_current_a": [17.2],
            "fluid_temp_c": [42.0],
        }
    )
    sensor_options = {"Fluid Temperature": SENSOR_CHART_OPTIONS["Fluid Temperature"]}

    figures = build_sensor_figures(data, sensor_options)

    assert [label for label, _figure in figures] == ["Fluid Temperature"]
