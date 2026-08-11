"""Gemeinsame Basis fuer alle Datenquellen (Modbus, MQTT, ...)."""

from __future__ import annotations

import abc
from typing import Callable

# (key, value, phase|None)
FeedCallback = Callable[[str, float, str | None], None]


class Source(abc.ABC):
    """Eine Quelle liefert Messwerte in ihrem eigenen Takt an die Pipeline."""

    name: str = "source"

    def __init__(self, feed: FeedCallback) -> None:
        self._feed = feed
        self.error_count = 0
        self.update_count = 0
        self.last_error: str | None = None

    @abc.abstractmethod
    async def async_start(self) -> None:
        """Verbindung aufbauen und Erfassung starten."""

    @abc.abstractmethod
    async def async_stop(self) -> None:
        """Erfassung beenden."""

    def diagnostics(self) -> dict:
        return {
            "name": self.name,
            "updates": self.update_count,
            "errors": self.error_count,
            "last_error": self.last_error,
        }
