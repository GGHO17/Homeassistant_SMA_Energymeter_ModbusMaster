"""Aufbereitung der Rohmesswerte zu einem sendefertigen Zaehlerabbild.

Drei Aufgaben:
  1. Glaettung/Aggregation (z. B. 10 ms Rohwerte -> 100 ms Mittelwert)
  2. Aufteilung vorzeichenbehafteter Leistungen in Bezug/Lieferung
  3. Energieintegration inkl. Persistenz (Zaehler duerfen nie zurueckspringen)
"""

from __future__ import annotations

import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .speedwire import MeterValues

_LOGGER = logging.getLogger(__name__)

PHASES = ("l1", "l2", "l3")

# Zaehlerstaende, die ein Geraet direkt liefern kann. Sie werden NICHT
# geglaettet (das wuerde einen monoton steigenden Zaehler verfaelschen) und
# haben Vorrang vor der eigenen Integration.
ENERGY_KEYS = (
    "e_import",
    "e_export",
    "eq_import",
    "eq_export",
    "es_import",
    "es_export",
)

# Faellt ein Zaehler unter diese Schwelle, wird das als Werksreset des Geraets
# gewertet und nicht als Stoerung: die alten Staende werden verworfen und der
# neue Wert uebernommen. 1 kWh Toleranz, weil zwischen Reset und erstem Lesen
# schon etwas Energie geflossen sein kann.
ENERGY_RESET_THRESHOLD_WS = 3_600_000.0


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
        self._has_sum_power = False
        self._energy: dict[str, float] = {}
        self._energy_warned: set[str] = set()

    def _name(self, key: str, phase: str | None) -> str:
        return f"{phase}.{key}" if phase else key

    def feed(self, key: str, value: float, phase: str | None = None) -> None:
        """Einen Rohwert einspeisen (aus Modbus- oder MQTT-Quelle)."""
        name = self._name(key, phase)

        if key in ENERGY_KEYS:
            self._feed_energy(name, float(value))
            self.last_update = time.monotonic()
            return

        smoother = self._smoothers.get(name)
        if smoother is None:
            smoother = self._smoothers[name] = Smoother(self._smoothing_s)
        now = time.monotonic()
        self._values[name] = smoother.add(float(value), now)
        self.last_update = now

        if key == "p":
            if phase is None:
                self._has_sum_power = True
                self.energy_sum.update(self._values[name], now)
            else:
                self.energy_phase[phase].update(self._values[name], now)
                # Ist kein Summenregister konfiguriert, die Summe aus den
                # Phasen bilden - sonst bliebe die gesendete Leistung 0.
                if not self._has_sum_power:
                    total = sum(self._values.get(f"{p}.p", 0.0) for p in PHASES)
                    self._values["p"] = total
                    self.energy_sum.update(total, now)

        # Gleiches Prinzip fuer Blind- und Scheinleistung
        elif key in ("q", "s") and phase is not None and key not in self._values:
            self._values[key] = sum(
                self._values.get(f"{p}.{key}", 0.0) for p in PHASES
            )

    def feed_many(self, items: Iterable[tuple[str, float, str | None]]) -> None:
        for key, value, phase in items:
            self.feed(key, value, phase)

    def _feed_energy(self, name: str, value: float) -> None:
        """Zaehlerstand uebernehmen, ohne Glaettung.

        Zwei Faelle bei einem fallenden Wert:
          * Sturz auf nahezu 0 -> Werksreset des Geraets. Die alten Staende
            sind damit ungueltig und werden komplett verworfen.
          * Sonstiger Rueckgang -> Stoerung oder Lesefehler. Der bisherige
            Hoechststand wird gehalten, damit die Gegenstelle keinen
            fallenden Zaehler sieht.
        """
        previous = self._energy.get(name)
        if previous is None or value >= previous:
            self._energy[name] = value
            self._energy_warned.discard(name)
            return

        if value <= ENERGY_RESET_THRESHOLD_WS < previous:
            _LOGGER.warning(
                "Zaehlerstand %s auf %.3f kWh gefallen (vorher %.3f kWh) - "
                "als Geraetereset gewertet, alte Staende werden verworfen",
                name,
                value / 3_600_000,
                previous / 3_600_000,
            )
            self._discard_energy_history()
            self._energy[name] = value
            return

        if name not in self._energy_warned:
            self._energy_warned.add(name)
            _LOGGER.warning(
                "Zaehlerstand %s ist gefallen (%.3f -> %.3f kWh) - vorheriger "
                "Wert wird gehalten",
                name,
                previous / 3_600_000,
                value / 3_600_000,
            )

    def _discard_energy_history(self) -> None:
        """Alle Zaehlerstaende verwerfen und neu vom Geraet uebernehmen.

        Auch die eigene Integration wird genullt - sie diente nur als
        Rueckfallebene und passt nach einem Geraetereset nicht mehr.
        """
        self._energy.clear()
        self._energy_warned.clear()
        self.energy_sum = EnergyIntegrator()
        self.energy_phase = {p: EnergyIntegrator() for p in PHASES}

    def has_external_energy(self, phase: str | None = None) -> bool:
        """True, wenn Zaehlerstaende vom Geraet kommen statt integriert werden."""
        return self._name("e_import", phase) in self._energy

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
            "e_import": self._energy.get(
                self._name("e_import", phase), energy.import_ws
            ),
            "e_export": self._energy.get(
                self._name("e_export", phase), energy.export_ws
            ),
        }
        for key in ("eq_import", "eq_export", "es_import", "es_export"):
            if (value := self._energy.get(self._name(key, phase))) is not None:
                block[key] = value
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

    def reset_energy(self, import_ws: float = 0.0, export_ws: float = 0.0) -> None:
        """Energiezaehler setzen - auf 0 oder auf einen Startwert.

        Die Phasenzaehler bekommen je ein Drittel, damit Summe und Straenge
        zueinander passen.
        """
        self.energy_sum = EnergyIntegrator(import_ws, export_ws)
        self.energy_phase = {
            p: EnergyIntegrator(import_ws / 3, export_ws / 3) for p in PHASES
        }

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
