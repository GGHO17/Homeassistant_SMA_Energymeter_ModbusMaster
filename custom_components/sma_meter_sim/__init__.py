"""SMA Meter Simulator - sendet Energy-Meter-Telegramme aus beliebigen Quellen."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CONF_INTERFACE_IP,
    CONF_SEND_INTERVAL_MS,
    CONF_SERIAL,
    CONF_SMOOTHING_MS,
    CONF_SOURCES,
    CONF_SUSY_ID,
    DEFAULT_SEND_INTERVAL_MS,
    DEFAULT_SMOOTHING_MS,
    DOMAIN,
    SERVICE_RESET_ENERGY,
)
from .coordinator import MeterSimulator
from .factory import async_build_sources
from .pipeline import MeterPipeline
from .speedwire import SpeedwireSender

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = {**entry.data, **entry.options}

    pipeline = MeterPipeline(
        smoothing_s=data.get(CONF_SMOOTHING_MS, DEFAULT_SMOOTHING_MS) / 1000
    )
    sender = SpeedwireSender(
        susy_id=data[CONF_SUSY_ID],
        serial=data[CONF_SERIAL],
        interface_ip=data.get(CONF_INTERFACE_IP) or None,
    )
    sim = MeterSimulator(
        hass,
        sender,
        pipeline,
        send_interval_ms=data.get(CONF_SEND_INTERVAL_MS, DEFAULT_SEND_INTERVAL_MS),
        entry_id=entry.entry_id,
    )

    for source in await async_build_sources(
        hass, data.get(CONF_SOURCES, []), pipeline.feed
    ):
        sim.add_source(source)

    if not sim.sources:
        _LOGGER.warning(
            "Keine nutzbare Quelle konfiguriert - es werden Nullwerte gesendet"
        )

    await sim.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = sim
    _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _async_register_services(hass: HomeAssistant) -> None:
    """Dienst zum Nullsetzen der Energiezaehler (einmalig registrieren)."""
    if hass.services.has_service(DOMAIN, SERVICE_RESET_ENERGY):
        return

    async def _reset_energy(call: ServiceCall) -> None:
        # Eingabe in kWh, intern wird in Ws gerechnet (1 kWh = 3.6e6 Ws)
        import_ws = float(call.data.get("import_kwh", 0.0)) * 3_600_000
        export_ws = float(call.data.get("export_kwh", 0.0)) * 3_600_000
        for sim in hass.data.get(DOMAIN, {}).values():
            await sim.async_reset_energy(import_ws, export_ws)
        _LOGGER.info(
            "Energiezaehler gesetzt: Bezug %.1f kWh, Lieferung %.1f kWh",
            import_ws / 3_600_000,
            export_ws / 3_600_000,
        )

    hass.services.async_register(DOMAIN, SERVICE_RESET_ENERGY, _reset_energy)


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        sim: MeterSimulator = hass.data[DOMAIN].pop(entry.entry_id)
        await sim.async_stop()
    return unload_ok
