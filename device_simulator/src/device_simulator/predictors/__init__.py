from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

from .base import PredictorAdapter
from .polar_mind import PolarMindAdapter

PredictorFactory: TypeAlias = Callable[[Path, str], PredictorAdapter]

_PREDICTOR_REGISTRY: dict[str, PredictorFactory] = {
    "polar-mind": PolarMindAdapter,
}


def register_predictor(name: str, factory: PredictorFactory) -> None:
    if not name or name == "none" or name.strip() != name:
        raise ValueError("Predictor names must be non-empty and cannot be 'none'.")
    if name in _PREDICTOR_REGISTRY:
        raise ValueError(f"Predictor {name!r} is already registered.")
    _PREDICTOR_REGISTRY[name] = factory


def available_predictors() -> tuple[str, ...]:
    return ("none", *sorted(_PREDICTOR_REGISTRY))


def create_predictor(
    name: str,
    *,
    data_dir: str | Path,
    asset: str,
) -> PredictorAdapter | None:
    if name == "none":
        return None
    try:
        factory = _PREDICTOR_REGISTRY[name]
    except KeyError as error:
        choices = ", ".join(available_predictors())
        raise ValueError(f"Unknown predictor {name!r}; choose from: {choices}.") from error
    return factory(Path(data_dir), asset)


__all__ = [
    "PredictorAdapter",
    "PredictorFactory",
    "available_predictors",
    "create_predictor",
    "register_predictor",
]
