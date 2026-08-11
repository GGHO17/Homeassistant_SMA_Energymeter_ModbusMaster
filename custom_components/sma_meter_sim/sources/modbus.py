"""Modbus-TCP-Quelle.

Registerkarte ist frei konfigurierbar, damit dasselbe Modul spaeter auch
andere Geraete bedienen kann. Gelesen wird blockweise (ein Request pro
Zusammenhangsbereich), nicht Register fuer Register.

Beispielkonfiguration (PQI-DA smart, Adressen noch einzutragen):

    host: 192.168.1.50
    port: 502
    unit: 1
    interval_ms: 10
    registers:
      - {key: p,  phase: l1, address: 1000, dtype: float32, scale: 1.0}
      - {key: p,  phase: l2, address: 1002, dtype: float32, scale: 1.0}
      - {key: p,  phase: l3, address: 1004, dtype: float32, scale: 1.0}
      - {key: p,  address: 1006, dtype: float32, scale: 1.0}
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass

from .base import FeedCallback, Source

_LOGGER = logging.getLogger(__name__)

_DTYPE_WORDS = {
    "int16": 1,
    "uint16": 1,
    "int32": 2,
    "uint32": 2,
    "float32": 2,
    "float64": 4,
    "int64": 4,
}


@dataclass
class RegisterDef:
    key: str
    address: int
    dtype: str = "float32"
    scale: float = 1.0
    phase: str | None = None
    input_register: bool = True  # False -> Holding Register

    @property
    def words(self) -> int:
        return _DTYPE_WORDS[self.dtype]


def _decode(dtype: str, words: list[int], word_swap: bool = False) -> float:
    raw = b"".join(struct.pack(">H", w) for w in (reversed(words) if word_swap else words))
    if dtype == "float32":
        return struct.unpack(">f", raw)[0]
    if dtype == "float64":
        return struct.unpack(">d", raw)[0]
    if dtype == "int16":
        return struct.unpack(">h", raw)[0]
    if dtype == "uint16":
        return struct.unpack(">H", raw)[0]
    if dtype == "int32":
        return struct.unpack(">i", raw)[0]
    if dtype == "uint32":
        return struct.unpack(">I", raw)[0]
    if dtype == "int64":
        return struct.unpack(">q", raw)[0]
    raise ValueError(f"unbekannter Datentyp {dtype}")


def _blocks(regs: list[RegisterDef], max_words: int = 120):
    """Register zu moeglichst wenigen zusammenhaengenden Bloecken buendeln."""
    ordered = sorted(regs, key=lambda r: (r.input_register, r.address))
    block: list[RegisterDef] = []
    for reg in ordered:
        if not block:
            block = [reg]
            continue
        start = block[0].address
        end = reg.address + reg.words
        same_space = reg.input_register == block[0].input_register
        if same_space and end - start <= max_words:
            block.append(reg)
        else:
            yield block
            block = [reg]
    if block:
        yield block


class ModbusSource(Source):
    """Pollt zyklisch eine Modbus-TCP-Einheit."""

    def __init__(
        self,
        feed: FeedCallback,
        host: str,
        port: int = 502,
        unit: int = 1,
        interval_ms: int = 100,
        registers: list[RegisterDef] | None = None,
        word_swap: bool = False,
        name: str = "modbus",
    ) -> None:
        super().__init__(feed)
        self.name = name
        self._host = host
        self._port = port
        self._unit = unit
        self._interval = interval_ms / 1000.0
        self._registers = registers or []
        self._word_swap = word_swap
        self._client = None
        self._task: asyncio.Task | None = None
        self.cycle_time_ms: float = 0.0
        self.overruns = 0

    async def async_start(self) -> None:
        from pymodbus.client import AsyncModbusTcpClient

        self._client = AsyncModbusTcpClient(self._host, port=self._port)
        await self._client.connect()
        self._task = asyncio.create_task(self._run(), name=f"{self.name}_poll")

    async def async_stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            self._client.close()
            self._client = None

    async def _read_block(self, block: list[RegisterDef]) -> None:
        start = block[0].address
        count = max(r.address + r.words for r in block) - start
        if block[0].input_register:
            result = await self._client.read_input_registers(
                start, count=count, slave=self._unit
            )
        else:
            result = await self._client.read_holding_registers(
                start, count=count, slave=self._unit
            )
        if result.isError():
            raise OSError(str(result))
        words = result.registers
        for reg in block:
            offset = reg.address - start
            value = _decode(reg.dtype, words[offset : offset + reg.words], self._word_swap)
            self._feed(reg.key, value * reg.scale, reg.phase)

    async def _run(self) -> None:
        blocks = list(_blocks(self._registers))
        next_tick = time.monotonic()
        while True:
            t0 = time.monotonic()
            try:
                for block in blocks:
                    await self._read_block(block)
                self.update_count += 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - Quelle darf HA nicht killen
                self.error_count += 1
                self.last_error = str(err)
                _LOGGER.debug("Modbus-Poll fehlgeschlagen: %s", err)
                await asyncio.sleep(min(1.0, self._interval * 10))
            self.cycle_time_ms = (time.monotonic() - t0) * 1000

            # Feste Taktung ohne Drift; bei Ueberlast Takte auslassen statt
            # aufzulaufen (wichtig bei 10-ms-Zyklen).
            next_tick += self._interval
            delay = next_tick - time.monotonic()
            if delay < 0:
                self.overruns += 1
                next_tick = time.monotonic()
                delay = 0
            await asyncio.sleep(delay)

    def diagnostics(self) -> dict:
        data = super().diagnostics()
        data.update(
            {
                "cycle_ms": round(self.cycle_time_ms, 2),
                "overruns": self.overruns,
                "interval_ms": self._interval * 1000,
            }
        )
        return data
