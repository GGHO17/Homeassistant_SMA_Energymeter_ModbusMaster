"""Orchestrierung: Quellen -> Pipeline -> Speedwire-Sender.

Bewusst KEIN DataUpdateCoordinator von Home Assistant: der ist auf
Sekundentakt und Entity-Updates ausgelegt. Hier laufen Erfassung und Versand
in eigenen Tasks, Entities sehen nur langsame Diagnosewerte.
"""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import ENERGY_SAVE_INTERVAL, STORAGE_KEY, STORAGE_VERSION
from .pipeline import MeterPipeline
from .sources.base import Source
from .speedwire import SpeedwireSender

_LOGGER = logging.getLogger(__name__)


class MeterSimulator:
    def __init__(
        self,
        hass: HomeAssistant,
        sender: SpeedwireSender,
        pipeline: MeterPipeline,
        send_interval_ms: int = 1000,
        entry_id: str = "default",
    ) -> None:
        self.hass = hass
        self.sender = sender
        self.pipeline = pipeline
        self.sources: list[Source] = []
        self._interval = send_interval_ms / 1000.0
        self._task: asyncio.Task | None = None
        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry_id}")
        self._last_save = 0.0

    def add_source(self, source: Source) -> None:
        self.sources.append(source)

    async def async_start(self) -> None:
        if (data := await self._store.async_load()) is not None:
            self.pipeline.restore(data)
            _LOGGER.info("Zaehlerstaende wiederhergestellt")
        await self.sender.async_start()
        for source in self.sources:
            await source.async_start()
        self._task = asyncio.create_task(self._send_loop(), name="sma_meter_sim_send")

    async def _send_loop(self) -> None:
        next_tick = time.monotonic()
        while True:
            # Immer senden, auch ohne neue Daten: Empfaenger erwarten einen
            # zyklischen Strom und laufen sonst in einen Timeout.
            self.sender.send(self.pipeline.snapshot())

            now = time.monotonic()
            if now - self._last_save > ENERGY_SAVE_INTERVAL:
                self._last_save = now
                await self._store.async_save(self.pipeline.dump())

            next_tick += self._interval
            delay = next_tick - time.monotonic()
            if delay < 0:
                next_tick = time.monotonic()
                delay = 0
            await asyncio.sleep(delay)

    async def async_reset_energy(
        self, import_ws: float = 0.0, export_ws: float = 0.0
    ) -> None:
        self.pipeline.reset_energy(import_ws, export_ws)
        await self._store.async_save(self.pipeline.dump())

    async def async_stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for source in self.sources:
            await source.async_stop()
        await self.sender.async_stop()
        await self._store.async_save(self.pipeline.dump())

    @property
    def diagnostics(self) -> dict:
        return {
            "sender": self.sender.diagnostics(),
            "sources": [s.diagnostics() for s in self.sources],
            "data_age_s": self.pipeline.age_s,
        }
