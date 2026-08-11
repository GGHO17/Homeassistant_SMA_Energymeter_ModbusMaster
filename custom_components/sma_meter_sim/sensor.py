"""Diagnose-Entities.

Bewusst nur langsame Kennzahlen - die 10-ms-Rohwerte gehoeren NICHT als
Entity in den Recorder, das sprengt jede Datenbank.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MeterSimulator

SCAN_INTERVAL = timedelta(seconds=10)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    sim: MeterSimulator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PowerSensor(sim, entry),
            TelegramCountSensor(sim, entry),
            DataAgeSensor(sim, entry),
        ]
    )


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = True

    def __init__(self, sim: MeterSimulator, entry: ConfigEntry, key: str) -> None:
        self._sim = sim
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Simulation",
            "model": "SMA Energy Meter (emuliert)",
        }


class PowerSensor(_Base):
    _attr_name = "Wirkleistung (gesendet)"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, sim, entry):
        super().__init__(sim, entry, "power")

    @property
    def native_value(self) -> float:
        return round(self._sim.pipeline.get("p"), 1)


class TelegramCountSensor(_Base):
    _attr_name = "Gesendete Telegramme"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, sim, entry):
        super().__init__(sim, entry, "telegrams")

    @property
    def native_value(self) -> int:
        return self._sim.sender.sent_count

    @property
    def extra_state_attributes(self) -> dict:
        return self._sim.diagnostics


class DataAgeSensor(_Base):
    _attr_name = "Datenalter"
    _attr_native_unit_of_measurement = "s"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, sim, entry):
        super().__init__(sim, entry, "data_age")

    @property
    def native_value(self) -> float | None:
        age = self._sim.pipeline.age_s
        return None if age is None else round(age, 2)
