"""Einrichtung ueber die Oberflaeche.

Ablauf:
    user            Identitaet des simulierten Zaehlers + Versandparameter
    menu            Quelle hinzufuegen (Modbus / MQTT) oder abschliessen
    add_modbus      Verbindung + Geraeteprofil, optional manuelle Register
    add_mqtt        Broker (HA oder eigener) + Profil, optional manuelle Topics
    map_register    Schleife: je ein Register
    map_topic       Schleife: je ein Topic

Die Optionen bieten dieselbe Verwaltung noch einmal an, plus das Loeschen
einzelner Quellen.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_ADD_ANOTHER,
    CONF_ADDRESS,
    CONF_BROKER,
    CONF_DEVICE_TYPE,
    CONF_DTYPE,
    CONF_HOST,
    CONF_INPUT_REGISTER,
    CONF_INTERFACE_IP,
    CONF_INTERVAL_MS,
    CONF_INVERT_SIGN,
    CONF_KEY,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PHASE,
    CONF_PORT,
    CONF_PROFILE,
    CONF_REGISTERS,
    CONF_SCALE,
    CONF_SEND_INTERVAL_MS,
    CONF_SERIAL,
    CONF_SMOOTHING_MS,
    CONF_SOURCE_INDEX,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    CONF_SUSY_ID,
    CONF_TOPIC,
    CONF_TOPICS,
    CONF_UNIT,
    CONF_USE_HA_BROKER,
    CONF_USERNAME,
    CONF_VALUE_PATH,
    CONF_WORD_SWAP,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_UNIT,
    DEFAULT_MQTT_PORT,
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_SEND_INTERVAL_MS,
    DEFAULT_SMOOTHING_MS,
    DEVICE_TYPES,
    DOMAIN,
    DTYPES,
    MEASURE_KEYS,
    PHASE_CHOICES,
    SOURCE_MODBUS,
    SOURCE_MQTT,
)
from .profiles import PROFILE_CUSTOM, USER_DIR_NAME, load_profiles, profile_labels

METER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_TYPE, default="energy_meter_20"): vol.In(
            list(DEVICE_TYPES)
        ),
        vol.Required(CONF_SERIAL, default=1900000001): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=4294967295)
        ),
        vol.Optional(CONF_INTERFACE_IP, default=""): str,
        vol.Optional(CONF_SMOOTHING_MS, default=DEFAULT_SMOOTHING_MS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=10000)
        ),
        vol.Optional(CONF_SEND_INTERVAL_MS, default=DEFAULT_SEND_INTERVAL_MS): vol.All(
            vol.Coerce(int), vol.Range(min=100, max=10000)
        ),
    }
)

REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_KEY, default="p"): vol.In(MEASURE_KEYS),
        vol.Optional(CONF_PHASE, default=""): vol.In(PHASE_CHOICES),
        vol.Required(CONF_ADDRESS): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Required(CONF_DTYPE, default="float32"): vol.In(DTYPES),
        vol.Optional(CONF_SCALE, default=1.0): vol.Coerce(float),
        vol.Optional(CONF_INPUT_REGISTER, default=True): bool,
        vol.Optional(CONF_ADD_ANOTHER, default=True): bool,
    }
)

TOPIC_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOPIC): str,
        vol.Required(CONF_KEY, default="p"): vol.In(MEASURE_KEYS),
        vol.Optional(CONF_PHASE, default=""): vol.In(PHASE_CHOICES),
        vol.Optional(CONF_VALUE_PATH, default=""): str,
        vol.Optional(CONF_SCALE, default=1.0): vol.Coerce(float),
        vol.Optional(CONF_ADD_ANOTHER, default=True): bool,
    }
)


class SourceFlowMixin:
    """Gemeinsame Schritte fuer Config- und Options-Flow."""

    hass: Any
    _data: dict
    _sources: list[dict]
    _pending: dict

    async def _async_profiles(self, protocol: str) -> dict[str, str]:
        profiles = await self.hass.async_add_executor_job(
            load_profiles, self.hass.config.path(USER_DIR_NAME)
        )
        return profile_labels(profiles, protocol)

    async def async_step_menu(self, user_input=None):
        options = ["add_modbus", "add_mqtt"]
        if self._sources:
            options += ["remove_source", "finish"]
        return self.async_show_menu(step_id="menu", menu_options=options)

    # --- Modbus ------------------------------------------------------------
    async def async_step_add_modbus(self, user_input=None):
        labels = await self._async_profiles(SOURCE_MODBUS)
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(CONF_NAME, default="modbus"): str,
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_MODBUS_PORT): vol.Coerce(int),
                    vol.Optional(CONF_UNIT, default=DEFAULT_MODBUS_UNIT): vol.Coerce(int),
                    vol.Required(CONF_PROFILE, default=next(iter(labels))): vol.In(labels),
                    vol.Optional(
                        CONF_INTERVAL_MS, default=DEFAULT_POLL_INTERVAL_MS
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=60000)),
                    vol.Optional(CONF_WORD_SWAP, default=False): bool,
                    vol.Optional(CONF_INVERT_SIGN, default=False): bool,
                }
            )
            return self.async_show_form(step_id="add_modbus", data_schema=schema)

        self._pending = {CONF_SOURCE_TYPE: SOURCE_MODBUS, CONF_REGISTERS: [], **user_input}
        if user_input[CONF_PROFILE] == PROFILE_CUSTOM:
            return await self.async_step_map_register()
        self._sources.append(self._pending)
        self._pending = {}
        return await self.async_step_menu()

    async def async_step_map_register(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="map_register",
                data_schema=REGISTER_SCHEMA,
                description_placeholders={
                    "count": str(len(self._pending.get(CONF_REGISTERS, [])))
                },
            )
        again = user_input.pop(CONF_ADD_ANOTHER, False)
        user_input[CONF_PHASE] = user_input.get(CONF_PHASE) or None
        self._pending[CONF_REGISTERS].append(user_input)
        if again:
            return await self.async_step_map_register()
        self._sources.append(self._pending)
        self._pending = {}
        return await self.async_step_menu()

    # --- MQTT --------------------------------------------------------------
    async def async_step_add_mqtt(self, user_input=None):
        labels = await self._async_profiles(SOURCE_MQTT)
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(CONF_NAME, default="mqtt"): str,
                    vol.Required(CONF_USE_HA_BROKER, default=True): bool,
                    vol.Optional(CONF_BROKER, default=""): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_MQTT_PORT): vol.Coerce(int),
                    vol.Optional(CONF_USERNAME, default=""): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                    vol.Required(CONF_PROFILE, default=PROFILE_CUSTOM): vol.In(labels),
                }
            )
            return self.async_show_form(step_id="add_mqtt", data_schema=schema)

        if not user_input[CONF_USE_HA_BROKER] and not user_input.get(CONF_BROKER):
            return self.async_show_form(
                step_id="add_mqtt",
                data_schema=vol.Schema({}),
                errors={"base": "broker_required"},
            )

        self._pending = {CONF_SOURCE_TYPE: SOURCE_MQTT, CONF_TOPICS: [], **user_input}
        if user_input[CONF_PROFILE] == PROFILE_CUSTOM:
            return await self.async_step_map_topic()
        self._sources.append(self._pending)
        self._pending = {}
        return await self.async_step_menu()

    async def async_step_map_topic(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="map_topic",
                data_schema=TOPIC_SCHEMA,
                description_placeholders={
                    "count": str(len(self._pending.get(CONF_TOPICS, [])))
                },
            )
        again = user_input.pop(CONF_ADD_ANOTHER, False)
        user_input[CONF_PHASE] = user_input.get(CONF_PHASE) or None
        user_input[CONF_VALUE_PATH] = user_input.get(CONF_VALUE_PATH) or None
        self._pending[CONF_TOPICS].append(user_input)
        if again:
            return await self.async_step_map_topic()
        self._sources.append(self._pending)
        self._pending = {}
        return await self.async_step_menu()

    # --- Entfernen ---------------------------------------------------------
    async def async_step_remove_source(self, user_input=None):
        choices = {
            str(i): f"{s.get(CONF_NAME)} ({s.get(CONF_SOURCE_TYPE)})"
            for i, s in enumerate(self._sources)
        }
        if user_input is None:
            return self.async_show_form(
                step_id="remove_source",
                data_schema=vol.Schema({vol.Required(CONF_SOURCE_INDEX): vol.In(choices)}),
            )
        self._sources.pop(int(user_input[CONF_SOURCE_INDEX]))
        return await self.async_step_menu()


class SmaMeterSimConfigFlow(SourceFlowMixin, ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._data: dict = {}
        self._sources: list[dict] = []
        self._pending: dict = {}

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=METER_SCHEMA)

        await self.async_set_unique_id(str(user_input[CONF_SERIAL]))
        self._abort_if_unique_id_configured()

        self._data = dict(user_input)
        self._data[CONF_SUSY_ID] = DEVICE_TYPES[user_input[CONF_DEVICE_TYPE]]
        return await self.async_step_menu()

    async def async_step_finish(self, user_input=None):
        self._data[CONF_SOURCES] = self._sources
        return self.async_create_entry(
            title=f"SMA Meter Sim {self._data[CONF_SERIAL]}", data=self._data
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SmaMeterSimOptionsFlow(entry)


class SmaMeterSimOptionsFlow(SourceFlowMixin, OptionsFlow):
    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        merged = {**entry.data, **entry.options}
        self._data = dict(merged)
        self._sources = [dict(s) for s in merged.get(CONF_SOURCES, [])]
        self._pending: dict = {}

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init", menu_options=["timing", "add_modbus", "add_mqtt"]
            + (["remove_source"] if self._sources else [])
            + ["finish"]
        )

    async def async_step_menu(self, user_input=None):
        return await self.async_step_init()

    async def async_step_timing(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_init()
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SMOOTHING_MS,
                    default=self._data.get(CONF_SMOOTHING_MS, DEFAULT_SMOOTHING_MS),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=10000)),
                vol.Optional(
                    CONF_SEND_INTERVAL_MS,
                    default=self._data.get(
                        CONF_SEND_INTERVAL_MS, DEFAULT_SEND_INTERVAL_MS
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=100, max=10000)),
                vol.Optional(
                    CONF_INTERFACE_IP, default=self._data.get(CONF_INTERFACE_IP, "")
                ): str,
            }
        )
        return self.async_show_form(step_id="timing", data_schema=schema)

    async def async_step_finish(self, user_input=None):
        data = dict(self._data)
        data[CONF_SOURCES] = self._sources
        return self.async_create_entry(title="", data=data)
