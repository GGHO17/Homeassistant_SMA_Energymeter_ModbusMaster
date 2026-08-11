"""Kodierung und Versand von SMA-Energy-Meter-Telegrammen (Speedwire).

Referenz: SMA Technische Information "SMA ENERGY METER Zaehlerprotokoll"
(EMETER-Protokoll-TI, cdn.sma.de). Multicast 239.12.255.254:9522,
SMA-Net-Protokoll-ID 0x6069 ("Energy Meter Protocol").

Aufbau eines Telegramms:

    "SMA\\0"                       4 B  Signatur
    00 04 02 A0                    4 B  Tag0 (Laenge 4, Tag 0x02A0)
    00 00 00 01                    4 B  Gruppe 1
    LL LL 00 10                    4 B  Laenge des SMA-Net-2-Blocks, Tag 0x0010
    60 69                          2 B  Protokoll-ID
    SS SS                          2 B  SusyID
    NN NN NN NN                    4 B  Seriennummer
    TT TT TT TT                    4 B  Ticker [ms], laeuft ueber bei 2^32
    <OBIS-Eintraege>                    je 4 B Kopf + 4 B (aktuell) / 8 B (Zaehler)
    00 00 00 00                    4 B  Endetag

OBIS-Kopf: (Kanal, Index, Typ, Tarif). Typ 4 = 32-Bit-Momentanwert,
Typ 8 = 64-Bit-Zaehler, Typ 0 = Softwareversion (Kanal 144).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

MULTICAST_GROUP = "239.12.255.254"
MULTICAST_PORT = 9522

_SMA_SIGNATURE = b"SMA\x00"
_TAG0 = struct.pack(">HH", 4, 0x02A0)
_GROUP = struct.pack(">I", 1)
_TAG_SMANET2 = 0x0010
_PROTOCOL_ID_EMETER = 0x6069
_END_TAG = b"\x00\x00\x00\x00"

# SusyIDs der emulierbaren Geraetetypen
SUSYID_ENERGY_METER_20 = 349
SUSYID_HOME_MANAGER_20 = 372

TYPE_ACTUAL = 4  # 32 Bit
TYPE_COUNTER = 8  # 64 Bit

# Skalierungen laut TI: Leistung in 0,1 W, Zaehler in Ws,
# Strom in mA, Spannung in mV, Leistungsfaktor/Frequenz in 0,001.
SCALE_POWER = 10.0
SCALE_ENERGY = 1.0
SCALE_CURRENT = 1000.0
SCALE_VOLTAGE = 1000.0
SCALE_MILLI = 1000.0

# OBIS-Index je Messgroesse. Summenwerte 1..14, L1 ab 21, L2 ab 41, L3 ab 61.
_PHASE_OFFSET = {"": 0, "l1": 20, "l2": 40, "l3": 60}

# (Index-Basis, Snapshot-Feld, Skalierung, Typ)
_ACTUAL_MAP: list[tuple[int, str, float, int]] = [
    (1, "p_import", SCALE_POWER, TYPE_ACTUAL),
    (2, "p_export", SCALE_POWER, TYPE_ACTUAL),
    (3, "q_import", SCALE_POWER, TYPE_ACTUAL),
    (4, "q_export", SCALE_POWER, TYPE_ACTUAL),
    (9, "s_import", SCALE_POWER, TYPE_ACTUAL),
    (10, "s_export", SCALE_POWER, TYPE_ACTUAL),
    (13, "cos_phi", SCALE_MILLI, TYPE_ACTUAL),
]
_COUNTER_MAP: list[tuple[int, str]] = [
    (1, "e_import"),
    (2, "e_export"),
    (3, "eq_import"),
    (4, "eq_export"),
    (9, "es_import"),
    (10, "es_export"),
]
# Nur je Phase vorhanden
_PHASE_EXTRA: list[tuple[int, str, float]] = [
    (11, "current", SCALE_CURRENT),
    (12, "voltage", SCALE_VOLTAGE),
]
# Nur als Summenwert (ab FW 2.x)
_SUM_EXTRA: list[tuple[int, str, float]] = [
    (14, "frequency", SCALE_MILLI),
]

SOFTWARE_VERSION = (2, 3, 4, 1)  # Major, Minor, Build, Revision


def _entry(index: int, value_type: int, raw: int, channel: int = 0) -> bytes:
    """Einen OBIS-Eintrag kodieren."""
    head = bytes((channel, index, value_type, 0))
    if value_type == TYPE_ACTUAL:
        return head + struct.pack(">I", max(0, int(raw)) & 0xFFFFFFFF)
    return head + struct.pack(">Q", max(0, int(raw)) & 0xFFFFFFFFFFFFFFFF)


def _version_entry() -> bytes:
    major, minor, build, rev = SOFTWARE_VERSION
    return bytes((144, 0, 0, 0)) + bytes((major, minor, build, rev))


@dataclass
class MeterValues:
    """Werte, aus denen ein Telegramm gebaut wird.

    Leistungen in W (import/export bereits getrennt, beide >= 0),
    Energien in Ws, Strom in A, Spannung in V, cos phi 0..1, Frequenz in Hz.
    """

    sum: dict[str, float]
    phases: dict[str, dict[str, float]]

    @staticmethod
    def empty() -> "MeterValues":
        return MeterValues(sum={}, phases={"l1": {}, "l2": {}, "l3": {}})


class TelegramBuilder:
    """Baut ein Energy-Meter-Datagramm aus MeterValues."""

    def __init__(self, susy_id: int, serial: int) -> None:
        self._susy_id = susy_id
        self._serial = serial
        self._t0 = time.monotonic()

    @property
    def ticker_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000) & 0xFFFFFFFF

    def build(self, values: MeterValues) -> bytes:
        entries = bytearray()

        def emit(offset: int, data: dict[str, float]) -> None:
            for base, key, scale, vtype in _ACTUAL_MAP:
                entries.extend(
                    _entry(base + offset, vtype, round(data.get(key, 0.0) * scale))
                )
                counter_key = next(
                    (ckey for cbase, ckey in _COUNTER_MAP if cbase == base), None
                )
                if counter_key is not None:
                    entries.extend(
                        _entry(
                            base + offset,
                            TYPE_COUNTER,
                            round(data.get(counter_key, 0.0) * SCALE_ENERGY),
                        )
                    )
            extra = _PHASE_EXTRA if offset else _SUM_EXTRA
            for base, key, scale in extra:
                if key in data:
                    entries.extend(
                        _entry(base + offset, TYPE_ACTUAL, round(data[key] * scale))
                    )

        emit(0, values.sum)
        for phase, offset in (("l1", 20), ("l2", 40), ("l3", 60)):
            emit(offset, values.phases.get(phase, {}))

        entries.extend(_version_entry())

        body = (
            struct.pack(">HII", self._susy_id, self._serial, self.ticker_ms)
            + bytes(entries)
        )
        smanet2 = struct.pack(">H", _PROTOCOL_ID_EMETER) + body
        return (
            _SMA_SIGNATURE
            + _TAG0
            + _GROUP
            + struct.pack(">HH", len(smanet2), _TAG_SMANET2)
            + smanet2
            + _END_TAG
        )


class SpeedwireSender:
    """Sendet Telegramme zyklisch per UDP-Multicast."""

    def __init__(
        self,
        susy_id: int = SUSYID_ENERGY_METER_20,
        serial: int = 1900000001,
        interface_ip: str | None = None,
        ttl: int = 1,
    ) -> None:
        self._builder = TelegramBuilder(susy_id, serial)
        self._interface_ip = interface_ip
        self._ttl = ttl
        self._transport: asyncio.DatagramTransport | None = None
        self.sent_count = 0
        self.last_error: str | None = None

    async def async_start(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self._ttl)
        # Wichtig bei mehreren Interfaces (VM, Docker, VLANs):
        # explizit das sendende Interface waehlen.
        if self._interface_ip:
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_IF,
                socket.inet_aton(self._interface_ip),
            )
            sock.bind((self._interface_ip, 0))
        self._transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, sock=sock
        )
        _LOGGER.info(
            "Speedwire-Sender aktiv (Interface %s)", self._interface_ip or "default"
        )

    def send(self, values: MeterValues) -> None:
        if self._transport is None:
            return
        try:
            self._transport.sendto(
                self._builder.build(values), (MULTICAST_GROUP, MULTICAST_PORT)
            )
            self.sent_count += 1
            self.last_error = None
        except OSError as err:  # Interface weg, kein Multicast-Routing, ...
            self.last_error = str(err)
            _LOGGER.warning("Telegramm konnte nicht gesendet werden: %s", err)

    async def async_stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def diagnostics(self) -> dict[str, Any]:
        return {"sent": self.sent_count, "last_error": self.last_error}
