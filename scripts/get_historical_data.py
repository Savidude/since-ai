#!/usr/bin/env python3
"""Export one-minute aggregated payloads for a pump's historical data."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterator
import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "challenge_description" / "data" / "full"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "historical_data"
PUMP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
WINDOW = timedelta(days=2)


@dataclass
class _MinuteAccumulator:
    numeric_sums: dict[str, float] = field(default_factory=dict)
    numeric_counts: dict[str, int] = field(default_factory=dict)
    categorical_counts: dict[str, Counter[str]] = field(default_factory=dict)
    quality_flags: int = 0
    has_quality_flags: bool = False

    def add(self, payload: dict[str, object]) -> None:
        for name, value in payload.items():
            if name == "timestamp_utc" or name == "sample_interval_s":
                continue
            if name in ("operating_mode", "sensor_source"):
                if isinstance(value, str):
                    self.categorical_counts.setdefault(name, Counter())[value] += 1
                continue
            if name == "quality_flags":
                if isinstance(value, int) and not isinstance(value, bool):
                    self.quality_flags |= value
                    self.has_quality_flags = True
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.numeric_sums[name] = self.numeric_sums.get(name, 0.0) + value
                self.numeric_counts[name] = self.numeric_counts.get(name, 0) + 1

    def to_payload(self, minute_start: datetime) -> dict[str, object]:
        payload: dict[str, object] = {"timestamp_utc": minute_start.isoformat()}
        for name, total in self.numeric_sums.items():
            mean = total / self.numeric_counts[name]
            payload[name] = float(f"{mean:.6g}")
        for name, counts in self.categorical_counts.items():
            payload[name] = counts.most_common(1)[0][0]
        if self.has_quality_flags:
            payload["quality_flags"] = self.quality_flags
        payload["sample_interval_s"] = 60
        return payload


def _parse_utc_timestamp(value: str) -> datetime:
    value = value.strip()
    if value.endswith(("Z", "z")):
        value = f"{value[:-1]}+00:00"
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"invalid ISO 8601 timestamp: {value!r}") from error

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _iter_dataset_payloads(
    data_dir: Path,
    pump: str,
    window_start: datetime | None,
    window_end: datetime | None,
) -> Iterator[dict[str, object]]:
    source_dir = str(PROJECT_ROOT / "device_simulator" / "src")
    if source_dir not in sys.path:
        sys.path.insert(0, source_dir)
    from device_simulator.replay import iter_replay_payloads

    if window_end is None:
        exclusive_end = None
    else:
        # The shared replay loader excludes its end boundary; include the requested endpoint.
        exclusive_end = (window_end + timedelta(microseconds=1)).isoformat()
    return iter_replay_payloads(
        data_dir,
        pump,
        start=window_start.isoformat() if window_start is not None else None,
        end=exclusive_end,
    )


def _iter_minute_payloads(
    payloads: Iterator[dict[str, object]],
) -> Iterator[dict[str, object]]:
    minute_start: datetime | None = None
    accumulator = _MinuteAccumulator()
    for payload in payloads:
        timestamp_value = payload.get("timestamp_utc")
        if not isinstance(timestamp_value, str):
            raise ValueError("Telemetry payload is missing a string 'timestamp_utc'.")
        timestamp = _parse_utc_timestamp(timestamp_value)
        next_minute = timestamp.replace(second=0, microsecond=0)
        if minute_start is not None and next_minute < minute_start:
            raise ValueError("Telemetry payloads are not sorted by timestamp.")
        if minute_start is not None and next_minute != minute_start:
            yield accumulator.to_payload(minute_start)
            accumulator = _MinuteAccumulator()
        minute_start = next_minute
        accumulator.add(payload)
    if minute_start is not None:
        yield accumulator.to_payload(minute_start)


def _write_history(
    data_dir: Path,
    output_path: Path,
    pump: str,
    timestamp: datetime | None,
) -> int:
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Telemetry data directory not found: {data_dir}")
    if output_path.suffix.lower() != ".jsonl":
        raise ValueError(f"Output path must end in .jsonl: {output_path}")

    window_start = timestamp - WINDOW if timestamp is not None else None
    window_end = timestamp + WINDOW if timestamp is not None else None
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temporary_path = Path(output_file.name)
            count = 0
            payloads = _iter_dataset_payloads(
                data_dir, pump, window_start, window_end
            )
            for payload in _iter_minute_payloads(payloads):
                output_file.write(
                    json.dumps(
                        payload,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                )
                output_file.write("\n")
                count += 1
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return count


def _load_pumps(data_dir: Path) -> list[str]:
    assets_path = data_dir / "assets.csv"
    if not assets_path.is_file():
        raise FileNotFoundError(f"Asset metadata file not found: {assets_path}")

    pumps: list[str] = []
    seen: set[str] = set()
    with assets_path.open("r", encoding="utf-8", newline="") as assets_file:
        reader = csv.DictReader(assets_file)
        if reader.fieldnames is None or "asset_id" not in reader.fieldnames:
            raise ValueError(f"{assets_path} is missing the 'asset_id' column.")
        for line_number, row in enumerate(reader, start=2):
            pump = (row.get("asset_id") or "").strip()
            if not pump or not PUMP_ID_PATTERN.fullmatch(pump):
                raise ValueError(
                    f"{assets_path}:{line_number}: invalid or missing asset_id."
                )
            if pump in seen:
                raise ValueError(f"{assets_path}:{line_number}: duplicate asset_id {pump!r}.")
            seen.add(pump)
            pumps.append(pump)
    if not pumps:
        raise ValueError(f"No pumps found in {assets_path}.")
    return pumps


def _default_output_path(pump: str, timestamp: datetime | None) -> Path:
    if timestamp is None:
        tag = "all"
    else:
        tag = timestamp.strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_OUTPUT_DIR / f"{pump.lower()}_history_{tag}.jsonl"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Examples:\n"
            "  python scripts/get_historical_data.py "
            "--pump PUMP-06 --timestamp 2025-10-10T00:10:00Z\n"
            "  python scripts/get_historical_data.py --pump PUMP-06 --all\n"
            "  python scripts/get_historical_data.py --all-pumps --all"
        ),
    )
    pump_group = parser.add_mutually_exclusive_group(required=True)
    pump_group.add_argument("--pump", help="Pump ID, for example PUMP-06.")
    pump_group.add_argument(
        "--all-pumps",
        action="store_true",
        help="Export data for every pump listed in assets.csv.",
    )
    range_group = parser.add_mutually_exclusive_group(required=True)
    range_group.add_argument(
        "--timestamp",
        type=_parse_utc_timestamp,
        metavar="ISO-8601",
        help="Center timestamp; timezone-naive values are interpreted as UTC.",
    )
    range_group.add_argument(
        "--all",
        action="store_true",
        help="Export all available data for the pump.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Telemetry dataset directory (default: challenge_description/data/full/).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSONL path for a single pump (default: scripts/historical_data/).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.pump is not None and not PUMP_ID_PATTERN.fullmatch(args.pump):
        parser.error("--pump must contain 1-128 letters, digits, hyphens, or underscores.")
    if args.all_pumps and args.output is not None:
        parser.error("--output cannot be used with --all-pumps; each pump gets its own file.")

    try:
        pumps = _load_pumps(args.data_dir) if args.all_pumps else [args.pump]
    except (OSError, ValueError) as error:
        parser.exit(1, f"{parser.prog}: error: {error}\n")

    failures: list[str] = []
    for pump in pumps:
        if pump is None:
            parser.error("A pump ID is required unless --all-pumps is selected.")
        output_path = args.output or _default_output_path(pump, args.timestamp)
        try:
            count = _write_history(args.data_dir, output_path, pump, args.timestamp)
        except (ImportError, OSError, ValueError) as error:
            print(f"{parser.prog}: error for {pump}: {error}", file=sys.stderr)
            failures.append(pump)
            continue
        if args.timestamp is None:
            print(
                f"Wrote {count} minute payloads to {output_path} "
                f"for all available {pump} data."
            )
        else:
            print(
                f"Wrote {count} minute payloads to {output_path} "
                f"for {pump} from "
                f"{(args.timestamp - WINDOW).isoformat()} through "
                f"{(args.timestamp + WINDOW).isoformat()}."
            )
    if failures:
        print(
            f"Failed to export {len(failures)} pump(s): {', '.join(failures)}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
