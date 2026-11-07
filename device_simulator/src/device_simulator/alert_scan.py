from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Sequence

from .predictors import available_predictors, create_predictor
from .predictors.base import PredictorAdapter
from .replay import DEFAULT_START, iter_replay_minutes, list_telemetry_assets

LOGGER = logging.getLogger("device_simulator.alert_scan")
PREDICTOR_CHOICES = tuple(name for name in available_predictors() if name != "none")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan local replay data for predictor alerts and save them as JSON "
            "without publishing telemetry to AWS."
        )
    )
    parser.add_argument(
        "--data",
        required=True,
        help="Telemetry dataset directory to scan.",
    )
    asset_group = parser.add_mutually_exclusive_group(required=True)
    asset_group.add_argument(
        "--asset",
        help="Asset identifier to scan.",
    )
    asset_group.add_argument(
        "--all-assets",
        action="store_true",
        help="Scan every asset found in the telemetry dataset.",
    )
    parser.add_argument(
        "--start",
        default=DEFAULT_START,
        help=f"Replay start timestamp, inclusive (default: {DEFAULT_START}).",
    )
    parser.add_argument(
        "--model-history-start",
        help=(
            "Earliest timestamp fed to the predictor; defaults to up to 24 hours "
            "before --start."
        ),
    )
    parser.add_argument(
        "--end",
        help="Optional replay end timestamp, exclusive.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Maximum replay minutes to scan per asset, excluding warm-up "
            "(when using --all-assets)."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        help=(
            "Maximum parallel asset scans for --all-assets "
            "(default: number of logical CPU cores)."
        ),
    )
    parser.add_argument(
        "--model",
        choices=PREDICTOR_CHOICES,
        default="polar-mind",
        help="Prediction adapter to run (default: polar-mind).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to the JSON file that will contain detected alerts.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def _collect_alerts(
    *,
    data_dir: str | Path,
    asset: str,
    start: str | None,
    end: str | None,
    limit: int | None,
    predictor: PredictorAdapter,
    model_history_start: str | None = None,
) -> tuple[list[dict[str, object]], int, int]:
    alerts: list[dict[str, object]] = []
    warmup_minutes = 0
    replay_minutes = 0

    LOGGER.info("Scanning local telemetry for %s without AWS publishing", asset)
    if model_history_start is None:
        LOGGER.info("Using up to 24 hours before %s as predictor history", start)
    else:
        LOGGER.info("Using predictor history from %s", model_history_start)
    for minute in iter_replay_minutes(
        data_dir,
        asset,
        start,
        end,
        limit,
        warmup_hours=24,
        history_start=model_history_start,
    ):
        prediction = predictor.process_minute(minute)
        if minute.is_warmup:
            warmup_minutes += 1
            if warmup_minutes % 360 == 0:
                LOGGER.info("Processed %d predictor warm-up minutes", warmup_minutes)
            continue

        replay_minutes += 1
        if replay_minutes % 360 == 0:
            LOGGER.info("Scanned %d replay minutes", replay_minutes)

        if prediction is None or prediction.get("fault_type") is None:
            continue

        alert = dict(prediction)
        alert.setdefault("minute_utc", minute.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
        alert.setdefault(
            "alert_time_utc",
            (minute.timestamp + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        alerts.append(alert)
        LOGGER.info(
            "Detected %s at %s",
            alert["fault_type"],
            alert["alert_time_utc"],
        )

    return alerts, warmup_minutes, replay_minutes


def _write_alerts(alerts: list[dict[str, object]], output: str | Path) -> None:
    output_path = Path(output)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(alerts, output_file, indent=2)
        output_file.write("\n")


def scan_alerts(
    *,
    data_dir: str | Path,
    asset: str,
    start: str | None,
    end: str | None,
    limit: int | None,
    predictor: PredictorAdapter,
    output: str | Path,
    model_history_start: str | None = None,
) -> int:
    alerts, warmup_minutes, replay_minutes = _collect_alerts(
        data_dir=data_dir,
        asset=asset,
        start=start,
        model_history_start=model_history_start,
        end=end,
        limit=limit,
        predictor=predictor,
    )
    output_path = Path(output)
    _write_alerts(alerts, output_path)

    LOGGER.info(
        "Offline scan complete: %d replay minutes, %d warm-up minutes, "
        "%d alerts written to %s",
        replay_minutes,
        warmup_minutes,
        len(alerts),
        output_path,
    )
    return len(alerts)


def _scan_asset(
    asset: str,
    *,
    data_dir: str | Path,
    model: str,
    start: str | None,
    model_history_start: str | None,
    end: str | None,
    limit: int | None,
    verbose: bool,
) -> tuple[str, list[dict[str, object]], int, int]:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    predictor = create_predictor(model, data_dir=data_dir, asset=asset)
    if predictor is None:
        raise ValueError("An alert scan requires a prediction model.")

    alerts, warmup_minutes, replay_minutes = _collect_alerts(
        data_dir=data_dir,
        asset=asset,
        start=start,
        model_history_start=model_history_start,
        end=end,
        limit=limit,
        predictor=predictor,
    )
    for alert in alerts:
        alert.setdefault("asset_id", asset)
    return asset, alerts, warmup_minutes, replay_minutes


def _scan_all_assets(
    *,
    data_dir: str | Path,
    model: str,
    start: str | None,
    model_history_start: str | None,
    end: str | None,
    limit: int | None,
    workers: int | None,
    output: str | Path,
    verbose: bool,
) -> int:
    assets = list_telemetry_assets(data_dir)
    if workers is not None and workers < 1:
        raise ValueError("--workers must be at least 1.")

    worker_count = min(workers or (os.cpu_count() or 1), len(assets))
    scan_asset = partial(
        _scan_asset,
        data_dir=data_dir,
        model=model,
        start=start,
        model_history_start=model_history_start,
        end=end,
        limit=limit,
        verbose=verbose,
    )
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(scan_asset, assets))

    alerts: list[dict[str, object]] = []
    replay_minutes = 0
    warmup_minutes = 0
    for asset, asset_alerts, asset_warmup_minutes, asset_replay_minutes in results:
        alerts.extend(asset_alerts)
        replay_minutes += asset_replay_minutes
        warmup_minutes += asset_warmup_minutes
        LOGGER.info(
            "Completed %s: %d replay minutes, %d warm-up minutes, %d alerts",
            asset,
            asset_replay_minutes,
            asset_warmup_minutes,
            len(asset_alerts),
        )

    _write_alerts(alerts, output)
    LOGGER.info(
        "Offline scan complete for %d assets: %d replay minutes, "
        "%d warm-up minutes, %d alerts written to %s",
        len(assets),
        replay_minutes,
        warmup_minutes,
        len(alerts),
        output,
    )
    return len(alerts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        if args.workers is not None and not args.all_assets:
            raise ValueError("--workers can only be used with --all-assets.")
        if args.all_assets:
            _scan_all_assets(
                data_dir=args.data,
                model=args.model,
                start=args.start,
                model_history_start=args.model_history_start,
                end=args.end,
                limit=args.limit,
                workers=args.workers,
                output=args.output,
                verbose=args.verbose,
            )
        else:
            predictor = create_predictor(
                args.model,
                data_dir=args.data,
                asset=args.asset,
            )
            if predictor is None:
                raise ValueError("An alert scan requires a prediction model.")
            scan_alerts(
                data_dir=args.data,
                asset=args.asset,
                start=args.start,
                model_history_start=args.model_history_start,
                end=args.end,
                limit=args.limit,
                predictor=predictor,
                output=args.output,
            )
    except (OSError, ValueError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
