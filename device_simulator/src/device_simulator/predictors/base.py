from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..replay import ReplayMinute


class PredictorAdapter(Protocol):
    def process_minute(
        self, minute: ReplayMinute
    ) -> Mapping[str, object] | None:
        """Consume one replay minute and return an alert, if one was raised."""
        ...
