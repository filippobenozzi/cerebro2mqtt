from __future__ import annotations

import copy
import json
from pathlib import Path
from threading import RLock

from app.models import AppConfig, BoardType


class ConfigError(ValueError):
    pass


class ConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = RLock()
        self._config = self._load_or_create()

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return copy.deepcopy(self._config)

    def update_from_dict(self, data: dict) -> AppConfig:
        config = AppConfig.from_dict(data)
        self._validate(config)
        self.save(config)
        return config

    def save(self, config: AppConfig) -> None:
        with self._lock:
            self._validate(config)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(config.to_dict(), handle, indent=2, ensure_ascii=True)
            tmp_path.replace(self.path)
            self._config = config

    def _load_or_create(self) -> AppConfig:
        if not self.path.exists():
            default = AppConfig()
            self.save(default)
            return default

        with self.path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

        config = AppConfig.from_dict(raw)
        self._validate(config)
        return config

    def _validate(self, config: AppConfig) -> None:
        if config.serial.baudrate <= 0:
            raise ConfigError("Serial baudrate non valido")
        if config.mqtt.port <= 0:
            raise ConfigError("Porta MQTT non valida")
        if config.polling.interval_sec < 1:
            raise ConfigError("Polling interval deve essere >= 1")
        if config.web.port <= 0:
            raise ConfigError("Porta web non valida")

        seen_topics: set[str] = set()

        for board in config.boards:
            if not board.name:
                raise ConfigError("Ogni scheda deve avere un nome")
            if board.address < 1 or board.address > 254:
                raise ConfigError(f"Indirizzo non valido per {board.name}: {board.address}")

            if board.board_type in (BoardType.LIGHTS, BoardType.SHUTTERS):
                if board.channel < 1 or board.channel > 8:
                    raise ConfigError(f"Canale non valido per {board.name}: {board.channel}")
            else:
                if board.channel < 1:
                    raise ConfigError(f"Canale non valido per {board.name}: {board.channel}")

            topic = board.topic_slug
            if topic in seen_topics:
                raise ConfigError(f"Topic duplicato: {topic}")
            seen_topics.add(topic)
