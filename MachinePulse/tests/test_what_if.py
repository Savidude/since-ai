from pathlib import Path

import pytest

from utils import what_if as what_if_utils


def _create_data_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "assets.csv").write_text("asset_id\nPUMP-01\n", encoding="utf-8")
    (path / "operating_context.csv").write_text(
        "asset_id,timestamp_utc\nPUMP-01,2025-08-12T15:00:00Z\n",
        encoding="utf-8",
    )
    return path


def test_resolve_simulation_data_dir_uses_environment_override(
    tmp_path,
    monkeypatch,
):
    data_dir = _create_data_dir(tmp_path / "simulation-data")
    monkeypatch.setenv(what_if_utils.SIMULATION_DATA_ENV, str(data_dir))

    assert what_if_utils.resolve_simulation_data_dir() == data_dir.resolve()


def test_resolve_simulation_data_dir_uses_adjacent_challenge_data(
    tmp_path,
    monkeypatch,
):
    project_root = tmp_path / "workspace"
    fake_module = project_root / "MachinePulse" / "utils" / "what_if.py"
    data_dir = _create_data_dir(
        project_root / "challenge_description" / "data" / "full"
    )
    monkeypatch.setattr(what_if_utils, "__file__", str(fake_module))
    monkeypatch.delenv(what_if_utils.SIMULATION_DATA_ENV, raising=False)

    assert what_if_utils.resolve_simulation_data_dir() == data_dir.resolve()


def test_resolve_simulation_data_dir_reports_missing_required_files(tmp_path):
    data_dir = tmp_path / "simulation-data"
    data_dir.mkdir()
    (data_dir / "assets.csv").write_text("asset_id\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="operating_context.csv"):
        what_if_utils.resolve_simulation_data_dir(data_dir)


@pytest.mark.parametrize(
    ("fault_type", "expected"),
    [
        ("SUCTION_STRAINER_CLOGGING", ("clog", ("clean_strainer", "inspect"))),
        ("bearing_wear", ("bearing_wear", ("replace_bearing", "inspect"))),
        ("MISALIGNMENT", ("misalignment", ("realign", "inspect"))),
        ("SENSOR_DRIFT", None),
        (None, None),
    ],
)
def test_scenario_for_fault(fault_type, expected):
    assert what_if_utils.scenario_for_fault(fault_type) == expected


@pytest.mark.parametrize(
    ("key", "raw_value", "expected"),
    [
        ("clog", "2.0", 2.0),
        ("bearing_wear", "0.5", 0.5),
        ("misalignment", "0.8", 0.8),
    ],
)
def test_parse_hypothesis_value_accepts_in_range_values(key, raw_value, expected):
    assert what_if_utils.parse_hypothesis_value(key, raw_value) == expected


@pytest.mark.parametrize(
    ("key", "raw_value", "message"),
    [
        ("clog", "", "Enter an assumed clog"),
        ("clog", "unknown", "Enter a numeric assumed clog"),
        ("clog", "9", "between 0 and 8"),
        ("bearing_wear", "1.0", "between 0 and 0.95"),
    ],
)
def test_parse_hypothesis_value_rejects_missing_invalid_or_out_of_range_values(
    key,
    raw_value,
    message,
):
    with pytest.raises(ValueError, match=message):
        what_if_utils.parse_hypothesis_value(key, raw_value)


def test_run_historical_what_if_calls_sandbox_with_explicit_hypothesis(
    tmp_path,
    monkeypatch,
):
    data_dir = _create_data_dir(tmp_path / "simulation-data")
    calls = []

    def fake_what_if(*args, **kwargs):
        calls.append((args, kwargs))
        return {"accepted": True}

    monkeypatch.setattr(what_if_utils, "what_if", fake_what_if)

    result = what_if_utils.run_historical_what_if(
        "PUMP-01",
        "2025-08-12T15:00:00Z",
        "SUCTION_STRAINER_CLOGGING",
        "clean_strainer",
        "2.0",
        data_dir=data_dir,
    )

    assert result == {"accepted": True}
    assert calls == [
        (
            (str(data_dir.resolve()), "PUMP-01", "2025-08-12T15:00:00Z"),
            {
                "action": "clean_strainer",
                "hypothesis": {"clog": 2.0},
            },
        )
    ]


def test_run_historical_what_if_rejects_unmapped_fault_before_running():
    with pytest.raises(ValueError, match="not available"):
        what_if_utils.run_historical_what_if(
            "PUMP-01",
            "2025-08-12T15:00:00Z",
            "SENSOR_DRIFT",
            "inspect",
            "1",
        )
