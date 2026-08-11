"""Aufbereitung der Rohmesswerte zu einem sendefertigen Zaehlerabbild.

Drei Aufgaben:
  1. Glaettung/Aggregation (z. B. 10 ms Rohwerte -> 100 ms Mittelwert)
  2. Aufteilung vorzeichenbehafteter Leistungen in Bezug/Lieferung
  3. Energieintegration inkl. Persistenz (Zaehler duerfen nie zurueckspringen)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .speedwire import MeterValues

PHASES = ("l1", "l2", "l3")


class Smoother:
    """Gleitender Mittelwert ueber ein Zeitfenster.

    Bewusst zeit- statt anzahlbasiert: faellt ein Poll aus, verschiebt sich
    das Fenster nicht. Alternativ EMA (alpha) fuer konstante Rechenlast.
    """

    def __init__(self, window_s: float = 0.1, alpha: float | None = None) -> None:
        self._window = window_s
        self._alpha = alpha
        self._buf: deque[tuple[float, float]] = deque()
        self._ema: float | None = None

    def add(self, value: float, ts: float | None = None) -> float:
        ts = ts if ts is not None else time.monotonic()
        if self._alpha is not None:
            self._ema = value if self._ema is None else (
                self._alpha * value + (1 - self._alpha) * self._ema
            )
            return self._ema
        self._buf.append((ts, value))
        cutoff = ts - self._window
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.popleft()
        return sum(v for _, v in self._buf) / len(self._buf)

    @property
    def samples(self) -> int:
        return len(self._buf)


class EnergyIntegrator:
    """Integriert Leistung zu Ws und haelt Bezug/Lieferung getrennt."""

    def __init__(self, import_ws: float = 0.0, export_ws: float = 0.0) -> None:
        self.import_ws = import_ws
        self.export_ws = export_ws
        self._last_ts: float | None = None

    def update(self, power_w: float, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.monotonic()
        if self._last_ts is not None:
            dt = ts - self._last_ts
            # Ausreisser (Suspend, Uhrensprung) verwerfen
            if 0 < dt < 5:
                if power_w >= 0:
                    self.import_ws += power_w * dt
                else:
                    self.export_ws += -power_w * dt
        self._last_ts = ts

    def as_dict(self) -> dict[str, float]:
        return {"import_ws": self.import_ws, "export_ws": self.export_ws}


@dataclass
class ChannelConfig:
    """Zuordnung einer Quellgroesse zum Zaehlerabbild."""

    key: str  # z. B. "p", "q", "s", "current", "voltage", "cos_phi", "frequency"
    phase: str | None = None  # None = Summenwert, sonst l1/l2/l3
    smoothing_s: float = 0.1


class MeterPipeline:
    """Nimmt Rohwerte entgegen und liefert jederzeit ein MeterValues-Abbild."""

    def __init__(self, smoothing_s: float = 0.1) -> None:
        self._smoothing_s = smoothing_s
        self._smoothers: dict[str, Smoother] = {}
        self._values: dict[str, float] = {}
        self.energy_sum = EnergyIntegrator()
        self.energy_phase = {p: EnergyIntegrator() for p in PHASES}
        self.last_update: float | None = None

    def _name(self, key: str, phase: str | None) -> str:
        return f"{phase}.{key}" if phase else key

    def feed(self, key: str, value: float, phase: str | None = None) -> None:
        """Einen Rohwert einspeisen (aus Modbus- oder MQTT-Quelle)."""
        name = self._name(key, phase)
        smoother = self._smoothers.get(name)
        if smoother is None:
            smoother = self._smoothers[name] = Smoother(self._smoothing_s)
        now = time.monotonic()
        self._values[name] = smoother.add(float(value), now)
        self.last_update = now
        if key == "p":
            target = self.energy_sum if phase is None else self.energy_phase[phase]
            target.update(self._values[name], now)

    def feed_many(self, items: Iterable[tuple[str, float, str | None]]) -> None:
        for key, value, phase in items:
            self.feed(key, value, phase)

    def get(self, key: str, phase: str | None = None, default: float = 0.0) -> float:
        return self._values.get(self._name(key, phase), default)

    @property
    def age_s(self) -> float | None:
        if self.last_update is None:
            return None
        return time.monotonic() - self.last_update

    def _block(self, phase: str | None) -> dict[str, float]:
        energy = self.energy_sum if phase is None else self.energy_phase[phase]
        p = self.get("p", phase)
        q = self.get("q", phase)
        s = self.get("s", phase) or abs(p)
        block: dict[str, float] = {
            "p_import": max(p, 0.0),
            "p_export": max(-p, 0.0),
            "q_import": max(q, 0.0),
            "q_export": max(-q, 0.0),
            "s_import": s if p >= 0 else 0.0,
            "s_export": s if p < 0 else 0.0,
            "cos_phi": abs(self.get("cos_phi", phase, 1.0)),
            "e_import": energy.import_ws,
            "e_export": energy.export_ws,
        }
        if phase:
            block["current"] = abs(self.get("current", phase))
            block["voltage"] = self.get("voltage", phase)
        else:
            freq = self.get("frequency", None, 50.0)
            if freq:
                block["frequency"] = freq
        return block

    def snapshot(self) -> MeterValues:
        return MeterValues(
            sum=self._block(None),
            phases={p: self._block(p) for p in PHASES},
        )

    # --- Persistenz der Zaehlerstaende -------------------------------------
    def restore(self, data: dict) -> None:
        self.energy_sum = EnergyIntegrator(
            data.get("sum", {}).get("import_ws", 0.0),
            data.get("sum", {}).get("export_ws", 0.0),
        )
        for p in PHASES:
            d = data.get(p, {})
            self.energy_phase[p] = EnergyIntegrator(
                d.get("import_ws", 0.0), d.get("export_ws", 0.0)
            )

    def dump(self) -> dict:
        return {
            "sum": self.energy_sum.as_dict(),
            **{p: self.energy_phase[p].as_dict() for p in PHASES},
        }
