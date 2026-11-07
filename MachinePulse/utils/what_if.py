from __future__ import annotations

import os
from pathlib import Path

from retrofit_sim.sandbox import HYPOTHESIS_KEYS, what_if

SIMULATION_DATA_ENV = "RETROFIT_SIM_DATA_DIR"

FAULT_SCENARIOS: dict[str, tuple[str, tuple[str, ...]]] = {
    "SUCTION_STRAINER_CLOGGING": ("clog", ("clean_strainer", "inspect")),
    "BEARING_WEAR": ("bearing_wear", ("replace_bearing", "inspect")),
    "MISALIGNMENT": ("misalignment", ("realign", "inspect")),
}

ACTION_LABELS = {
    "clean_strainer": "Clean strainer",
    "replace_bearing": "Replace bearing",
    "realign": "Realign",
    "inspect": "Inspect",
}


def scenario_for_fault(
    fault_type: str | None,
) -> tuple[str, tuple[str, ...]] | None:
    normalized = str(fault_type or "").strip().upper()
    return FAULT_SCENARIOS.get(normalized)


def hypothesis_bounds(key: str) -> tuple[float, float]:
    try:
        return HYPOTHESIS_KEYS[key]
    except KeyError as error:
        raise ValueError(f"Unknown model hypothesis '{key}'.") from error


def parse_hypothesis_value(key: str, raw_value: str | None) -> float:
    lower, upper = hypothesis_bounds(key)
    if raw_value is None or not raw_value.strip():
        raise ValueError(f"Enter an assumed {key} model value.")

    try:
        value = float(raw_value.strip())
    except ValueError as error:
        raise ValueError(f"Enter a numeric assumed {key} model value.") from error

    if not lower <= value <= upper:
        raise ValueError(
            f"Assumed {key} value must be between {lower:g} and {upper:g}."
        )
    return value


def resolve_simulation_data_dir(
    data_dir: str | Path | None = None,
) -> Path:
    configured = (
        str(data_dir).strip()
        if data_dir is not None
        else os.environ.get(SIMULATION_DATA_ENV, "").strip()
    )
    if configured:
        candidate = Path(configured).expanduser().resolve()
    else:
        candidate = (
            Path(__file__).resolve().parents[2]
            / "challenge_description"
            / "data"
            / "full"
        )

    if not candidate.is_dir():
        raise FileNotFoundError(
            f"What-if data directory was not found: {candidate}. "
            f"Set {SIMULATION_DATA_ENV} to a directory containing assets.csv "
            "and operating_context.csv."
        )

    missing_files = [
        filename
        for filename in ("assets.csv", "operating_context.csv")
        if not (candidate / filename).is_file()
    ]
    if missing_files:
        raise FileNotFoundError(
            f"What-if data directory {candidate} is missing: "
            f"{', '.join(missing_files)}."
        )

    return candidate


def run_historical_what_if(
    asset_id: str,
    time_utc: str,
    fault_type: str,
    action: str,
    hypothesis_value: str,
    *,
    data_dir: str | Path | None = None,
) -> dict:
    scenario = scenario_for_fault(fault_type)
    if scenario is None:
        raise ValueError(
            f"What-if analysis is not available for fault type '{fault_type}'."
        )

    hypothesis_key, allowed_actions = scenario
    if action not in allowed_actions:
        raise ValueError(
            f"Action '{action}' is not supported for fault type '{fault_type}'."
        )

    assumption = parse_hypothesis_value(hypothesis_key, hypothesis_value)
    resolved_data_dir = resolve_simulation_data_dir(data_dir)
    return what_if(
        str(resolved_data_dir),
        asset_id,
        time_utc,
        action=action,
        hypothesis={hypothesis_key: assumption},
    )
