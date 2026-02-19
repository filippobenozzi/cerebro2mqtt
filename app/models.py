from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4
import re


class BoardType(str, Enum):
    LIGHTS = "luci"
    SHUTTERS = "tapparelle"
    THERMOSTAT = "termostato"
    DIMMER = "dimmer"


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "board"


@dataclass
class BoardConfig:
    name: str
    board_type: BoardType
    address: int
    channel_start: int = 1
    channel_end: int = 1
    topic: str = ""
    enabled: bool = True
    board_id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def type(self) -> str:
        return self.board_type.value

    @property
    def topic_slug(self) -> str:
        return slugify(self.topic or self.name)

    @property
    def primary_channel(self) -> int:
        return self.channel_start

    @property
    def channels(self) -> list[int]:
        if self.board_type == BoardType.LIGHTS:
            return list(range(self.channel_start, self.channel_end + 1))
        return [self.channel_start]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.board_id,
            "name": self.name,
            "type": self.board_type.value,
            "address": self.address,
            "channel": self.primary_channel,
            "channel_start": self.channel_start,
            "channel_end": self.channel_end,
            "topic": self.topic,
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "BoardConfig":
        board_type_raw = str(data.get("type", BoardType.LIGHTS.value)).strip().lower()
        try:
            board_type = BoardType(board_type_raw)
        except ValueError:
            board_type = BoardType.LIGHTS

        legacy_channel = int(data.get("channel", 1))
        channel_start = int(data.get("channel_start", legacy_channel))
        channel_end = int(data.get("channel_end", channel_start))
        if board_type != BoardType.LIGHTS:
            channel_end = channel_start

        return BoardConfig(
            board_id=str(data.get("id") or uuid4()),
            name=str(data.get("name", "Scheda")).strip(),
            board_type=board_type,
            address=int(data.get("address", 1)),
            channel_start=channel_start,
            channel_end=channel_end,
            topic=str(data.get("topic", "")).strip(),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class SerialConfig:
    port: str = "/dev/ttyUSB0"
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    timeout_sec: float = 0.25

    def to_dict(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "bytesize": self.bytesize,
            "parity": self.parity,
            "stopbits": self.stopbits,
            "timeout_sec": self.timeout_sec,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SerialConfig":
        return SerialConfig(
            port=str(data.get("port", "/dev/ttyUSB0")).strip() or "/dev/ttyUSB0",
            baudrate=int(data.get("baudrate", 9600)),
            bytesize=int(data.get("bytesize", 8)),
            parity=str(data.get("parity", "N")).strip().upper() or "N",
            stopbits=int(data.get("stopbits", 1)),
            timeout_sec=float(data.get("timeout_sec", 0.25)),
        )


@dataclass
class MQTTConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = "cerebro2mqtt"
    base_topic: str = "cerebro2mqtt"
    discovery_prefix: str = "homeassistant"
    keepalive: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "client_id": self.client_id,
            "base_topic": self.base_topic,
            "discovery_prefix": self.discovery_prefix,
            "keepalive": self.keepalive,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "MQTTConfig":
        raw_base_topic = str(data.get("base_topic", "cerebro2mqtt")).strip().strip("/")
        if not raw_base_topic:
            raw_base_topic = "cerebro2mqtt"

        return MQTTConfig(
            host=str(data.get("host", "127.0.0.1")).strip() or "127.0.0.1",
            port=int(data.get("port", 1883)),
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
            client_id=str(data.get("client_id", "cerebro2mqtt")).strip() or "cerebro2mqtt",
            base_topic=raw_base_topic,
            discovery_prefix=str(data.get("discovery_prefix", "homeassistant")).strip() or "homeassistant",
            keepalive=int(data.get("keepalive", 60)),
        )


@dataclass
class PollingConfig:
    interval_sec: int = 30
    auto_start: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_sec": self.interval_sec,
            "auto_start": self.auto_start,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PollingConfig":
        return PollingConfig(
            interval_sec=int(data.get("interval_sec", 30)),
            auto_start=bool(data.get("auto_start", True)),
        )


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 80

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "WebConfig":
        return WebConfig(
            host=str(data.get("host", "0.0.0.0")).strip() or "0.0.0.0",
            port=int(data.get("port", 80)),
        )


@dataclass
class ServiceConfig:
    restart_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "restart_command": self.restart_command,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ServiceConfig":
        return ServiceConfig(
            restart_command=str(data.get("restart_command", "")).strip(),
        )


@dataclass
class AppConfig:
    serial: SerialConfig = field(default_factory=SerialConfig)
    mqtt: MQTTConfig = field(default_factory=MQTTConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    web: WebConfig = field(default_factory=WebConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    boards: list[BoardConfig] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial.to_dict(),
            "mqtt": self.mqtt.to_dict(),
            "polling": self.polling.to_dict(),
            "web": self.web.to_dict(),
            "service": self.service.to_dict(),
            "boards": [b.to_dict() for b in self.boards],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AppConfig":
        serial = SerialConfig.from_dict(data.get("serial", {}))
        mqtt = MQTTConfig.from_dict(data.get("mqtt", {}))
        polling = PollingConfig.from_dict(data.get("polling", {}))
        web = WebConfig.from_dict(data.get("web", {}))
        service = ServiceConfig.from_dict(data.get("service", {}))
        boards = [BoardConfig.from_dict(item) for item in data.get("boards", [])]
        return AppConfig(serial=serial, mqtt=mqtt, polling=polling, web=web, service=service, boards=boards)
