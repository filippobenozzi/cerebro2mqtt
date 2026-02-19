from __future__ import annotations

import json
import logging
from typing import Callable

import paho.mqtt.client as mqtt

from app.models import MQTTConfig

LOGGER = logging.getLogger(__name__)


class MqttBridge:
    def __init__(
        self,
        config: MQTTConfig,
        on_command: Callable[[str, str], None],
        on_connected: Callable[[], None] | None = None,
    ):
        self._config = config
        self._on_command = on_command
        self._on_connected = on_connected

        self._client = mqtt.Client(client_id=config.client_id, protocol=mqtt.MQTTv311)
        if config.username:
            self._client.username_pw_set(config.username, config.password)

        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

    def start(self) -> None:
        self._client.connect_async(self._config.host, self._config.port, keepalive=self._config.keepalive)
        self._client.loop_start()

    def stop(self) -> None:
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            LOGGER.exception("Errore stop MQTT")

    def publish(self, topic: str, payload: str | int | float | dict, retain: bool = False, qos: int = 0) -> None:
        if isinstance(payload, dict):
            raw_payload = json.dumps(payload, ensure_ascii=True)
        else:
            raw_payload = str(payload)

        result = self._client.publish(topic, raw_payload, qos=qos, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.warning("Publish MQTT fallita su %s rc=%s", topic, result.rc)

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self._client.subscribe(topic, qos=qos)

    def _handle_connect(self, client, userdata, flags, reason_code):
        if reason_code != 0:
            LOGGER.error("Connessione MQTT fallita: rc=%s", reason_code)
            return
        LOGGER.info("Connesso a MQTT %s:%d", self._config.host, self._config.port)

        base_topic = self._config.base_topic
        self.subscribe(f"{base_topic}/#")

        if self._on_connected:
            self._on_connected()

    def _handle_disconnect(self, client, userdata, reason_code):
        LOGGER.warning("Disconnesso da MQTT rc=%s", reason_code)

    def _handle_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="ignore").strip()
        except Exception:
            payload = ""
        self._on_command(msg.topic, payload)
