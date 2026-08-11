"""MQTT-Quelle.

Zwei Betriebsarten:
  * HA-Broker: nutzt den Client der MQTT-Integration von Home Assistant
    (kein zweiter Verbindungsaufbau, keine Zugangsdaten noetig)
  * Eigener Broker: eigene Verbindung per aiomqtt, mit Reconnect-Schleife

Jedes Topic wird auf (key, phase) gemappt. Der Payload darf eine nackte Zahl
sein oder JSON; im JSON-Fall greift 'value_path' (punktseparierter Pfad).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from .base import FeedCallback, Source

_LOGGER = logging.getLogger(__name__)

_RECONNECT_MIN = 2
_RECONNECT_MAX = 60


@dataclass
class TopicDef:
    topic: str
    key: str
    phase: str | None = None
    scale: float = 1.0
    value_path: str | None = None


def _extract(payload: str, path: str | None) -> float:
    if not path:
        return float(payload)
    data = json.loads(payload)
    for part in path.split("."):
        data = data[part] if isinstance(data, dict) else data[int(part)]
    return float(data)


class MqttSource(Source):
    def __init__(
        self,
        feed: FeedCallback,
        hass,
        topics: list[TopicDef],
        use_ha_broker: bool = True,
        broker: str | None = None,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        name: str = "mqtt",
    ) -> None:
        super().__init__(feed)
        self.name = name
        self._hass = hass
        self._topics = topics
        self._use_ha_broker = use_ha_broker
        self._broker = broker
        self._port = port
        self._username = username
        self._password = password
        self._unsubs: list = []
        self._task: asyncio.Task | None = None
        self.connected = False

    # --- gemeinsame Auswertung --------------------------------------------
    def _dispatch(self, topic_name: str, payload: str) -> None:
        for topic in self._topics:
            if topic.topic != topic_name:
                continue
            try:
                value = _extract(payload, topic.value_path) * topic.scale
            except (ValueError, KeyError, IndexError, TypeError) as err:
                self.error_count += 1
                self.last_error = f"{topic_name}: {err}"
                continue
            self.update_count += 1
            self._feed(topic.key, value, topic.phase)

    # --- Betriebsart HA-Broker --------------------------------------------
    async def _start_ha_broker(self) -> None:
        from homeassistant.components import mqtt

        for topic in {t.topic for t in self._topics}:
            self._unsubs.append(
                await mqtt.async_subscribe(
                    self._hass, topic, self._handle_ha_message, qos=0
                )
            )
        self.connected = True

    def _handle_ha_message(self, msg) -> None:
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        self._dispatch(msg.topic, payload)

    # --- Betriebsart eigener Broker ---------------------------------------
    async def _run_own_broker(self) -> None:
        import aiomqtt

        delay = _RECONNECT_MIN
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self._broker,
                    port=self._port,
                    username=self._username or None,
                    password=self._password or None,
                    identifier=f"ha-{self.name}",
                ) as client:
                    self.connected = True
                    delay = _RECONNECT_MIN
                    for topic in {t.topic for t in self._topics}:
                        await client.subscribe(topic)
                    _LOGGER.info(
                        "MQTT-Quelle %s verbunden mit %s", self.name, self._broker
                    )
                    async for message in client.messages:
                        self._dispatch(
                            str(message.topic),
                            message.payload.decode(errors="replace"),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - Verbindung darf HA nicht killen
                self.connected = False
                self.error_count += 1
                self.last_error = str(err)
                _LOGGER.warning(
                    "MQTT-Quelle %s getrennt (%s), neuer Versuch in %ss",
                    self.name,
                    err,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX)

    # --- Lebenszyklus ------------------------------------------------------
    async def async_start(self) -> None:
        if self._use_ha_broker:
            await self._start_ha_broker()
        else:
            self._task = asyncio.create_task(
                self._run_own_broker(), name=f"{self.name}_mqtt"
            )

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.connected = False

    def diagnostics(self) -> dict:
        data = super().diagnostics()
        data.update(
            {
                "connected": self.connected,
                "broker": "HA" if self._use_ha_broker else self._broker,
                "topics": len(self._topics),
            }
        )
        return data
