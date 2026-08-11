"""Erzeugt Quellenobjekte aus der gespeicherten Konfiguration.

Eine Quelle ist ein Dictionary, wie es der Config Flow ablegt:

    {"source_type": "modbus", "name": "pqi", "profile": "pqi_da_smart",
     "host": "192.168.1.50", "port": 502, "unit": 1, "interval_ms": 100,
     "word_swap": false, "invert_sign": false,
     "registers": [...]}            # nur bei profile == "custom"

    {"source_type": "mqtt", "name": "shelly", "profile": "custom",
     "use_ha_broker": true, "broker": "", "port": 1883,
     "topics": [{"topic": "...", "key": "p", "phase": null, "scale": 1.0,
                 "value_path": null}]}
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BROKER,
    CONF_HOST,
    CONF_INTERVAL_MS,
    CONF_INVERT_SIGN,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PROFILE,
    CONF_REGISTERS,
    CONF_SOURCE_TYPE,
    CONF_TOPICS,
    CONF_UNIT,
    CONF_USE_HA_BROKER,
    CONF_USERNAME,
    CONF_WORD_SWAP,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_UNIT,
    DEFAULT_MQTT_PORT,
    DEFAULT_POLL_INTERVAL_MS,
    SOURCE_MODBUS,
    SOURCE_MQTT,
)
from .profiles import PROFILE_CUSTOM, DeviceProfile, load_profiles
from .sources.base import FeedCallback, Source
from .sources.modbus import ModbusSource, RegisterDef
from .sources.mqtt import MqttSource, TopicDef

_LOGGER = logging.getLogger(__name__)


def _registers_from_config(conf: dict, profile: DeviceProfile | None) -> list[RegisterDef]:
    if conf.get(CONF_PROFILE, PROFILE_CUSTOM) != PROFILE_CUSTOM and profile:
        regs = list(profile.registers)
    else:
        regs = [
            RegisterDef(
                key=r["key"],
                address=int(r["address"]),
                dtype=r.get("dtype", "float32"),
                scale=float(r.get("scale", 1.0)),
                phase=r.get("phase") or None,
                input_register=bool(r.get("input_register", True)),
            )
            for r in conf.get(CONF_REGISTERS, [])
        ]
    if conf.get(CONF_INVERT_SIGN):
        # Vorzeichenkonvention drehen: p > 0 muss Bezug aus dem Netz sein.
        regs = [
            RegisterDef(
                key=r.key,
                address=r.address,
                dtype=r.dtype,
                scale=r.scale * (-1.0 if r.key in ("p", "q", "s") else 1.0),
                phase=r.phase,
                input_register=r.input_register,
            )
            for r in regs
        ]
    return regs


def _topics_from_config(conf: dict, profile: DeviceProfile | None) -> list[TopicDef]:
    if conf.get(CONF_PROFILE, PROFILE_CUSTOM) != PROFILE_CUSTOM and profile:
        return list(profile.topics)
    return [
        TopicDef(
            topic=t["topic"],
            key=t["key"],
            phase=t.get("phase") or None,
            scale=float(t.get("scale", 1.0)),
            value_path=t.get("value_path") or None,
        )
        for t in conf.get(CONF_TOPICS, [])
    ]


async def async_build_sources(
    hass: HomeAssistant, configs: list[dict], feed: FeedCallback
) -> list[Source]:
    """Alle konfigurierten Quellen instanziieren."""
    from .profiles import USER_DIR_NAME

    profiles = await hass.async_add_executor_job(
        load_profiles, hass.config.path(USER_DIR_NAME)
    )
    sources: list[Source] = []

    for conf in configs:
        name = conf.get(CONF_NAME) or conf.get(CONF_SOURCE_TYPE, "source")
        profile = profiles.get(conf.get(CONF_PROFILE, ""))

        if conf.get(CONF_SOURCE_TYPE) == SOURCE_MODBUS:
            registers = _registers_from_config(conf, profile)
            if not registers:
                _LOGGER.warning("Quelle %s hat keine Register - wird uebersprungen", name)
                continue
            sources.append(
                ModbusSource(
                    feed,
                    host=conf[CONF_HOST],
                    port=conf.get(CONF_PORT, DEFAULT_MODBUS_PORT),
                    unit=conf.get(CONF_UNIT, DEFAULT_MODBUS_UNIT),
                    interval_ms=conf.get(
                        CONF_INTERVAL_MS,
                        profile.default_interval_ms if profile else DEFAULT_POLL_INTERVAL_MS,
                    ),
                    registers=registers,
                    word_swap=conf.get(
                        CONF_WORD_SWAP, profile.word_swap if profile else False
                    ),
                    name=name,
                )
            )

        elif conf.get(CONF_SOURCE_TYPE) == SOURCE_MQTT:
            topics = _topics_from_config(conf, profile)
            if not topics:
                _LOGGER.warning("Quelle %s hat keine Topics - wird uebersprungen", name)
                continue
            sources.append(
                MqttSource(
                    feed,
                    hass,
                    topics=topics,
                    use_ha_broker=conf.get(CONF_USE_HA_BROKER, True),
                    broker=conf.get(CONF_BROKER) or None,
                    port=conf.get(CONF_PORT, DEFAULT_MQTT_PORT),
                    username=conf.get(CONF_USERNAME) or None,
                    password=conf.get(CONF_PASSWORD) or None,
                    name=name,
                )
            )

        else:
            _LOGGER.error("Unbekannter Quellentyp in %s", conf)

    return sources
