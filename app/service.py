from __future__ import annotations
import logging
import os
import subprocess
import threading
import time
from collections import defaultdict
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config_store import ConfigStore
from app.models import AppConfig, BoardConfig, BoardType
from app.mqtt_bridge import MqttBridge
from app.protocol import (
    CMD_DIMMER_CONTROL,
    CMD_DIMMER_DATA,
    CMD_LIGHT_CONTROL_START_FIFTH_ONWARD,
    CMD_LIGHT_CONTROL_START_FIRST_FOUR,
    CMD_LIGHT_DATA_RELAY_OFF,
    CMD_LIGHT_DATA_RELAY_ON,
    CMD_POLLING_EXTENDED,
    CMD_POLLING_RESPONSE,
    CMD_SET_POINT_TEMPERATURE,
    CMD_SET_SEASON,
    ParsedFrame,
    build_dimmer_control,
    build_light_control,
    build_polling_extended,
    build_set_point_temperature,
    build_set_season,
    build_shutter_control,
    bus_dimmer_to_percent,
    percent_to_bus_dimmer,
    parse_polling_status,
)
from app.serial_bridge import SerialBridge

LOGGER = logging.getLogger(__name__)
COMMAND_ACK_TIMEOUT_SEC = 2.0


@dataclass
class _AckWaiter:
    address: int
    matcher: Callable[[ParsedFrame], bool]
    event: threading.Event = field(default_factory=threading.Event)
    frame: ParsedFrame | None = None


class BridgeService:
    def __init__(self, store: ConfigStore):
        self._store = store
        self._config: AppConfig = store.config
        self._lock = threading.RLock()

        self._running = False
        self._shutdown_event = threading.Event()
        self._manual_poll_event = threading.Event()
        self._poll_worker: threading.Thread | None = None

        self._serial: SerialBridge | None = None
        self._mqtt: MqttBridge | None = None

        self._boards_by_topic: dict[str, BoardConfig] = {}
        self._boards_by_address: dict[int, list[BoardConfig]] = defaultdict(list)
        self._dimmer_cache: dict[str, int] = {}
        self._ack_lock = threading.Lock()
        self._ack_waiters: list[_AckWaiter] = []
        self._transaction_lock = threading.Lock()

        self._rebuild_indexes()

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._shutdown_event.clear()
            self._manual_poll_event.clear()
            self._start_components_locked()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._shutdown_event.set()
            self._manual_poll_event.set()
            self._stop_components_locked()

    def reload(self) -> None:
        with self._lock:
            was_running = self._running
            if was_running:
                self._shutdown_event.set()
                self._manual_poll_event.set()
                self._stop_components_locked()

            self._config = self._store.config
            self._rebuild_indexes()

            if was_running:
                self._shutdown_event.clear()
                self._manual_poll_event.clear()
                self._start_components_locked()

        LOGGER.info("Configurazione ricaricata")

    def trigger_poll_all(self) -> None:
        self._manual_poll_event.set()

    def trigger_poll_for_board(self, board: BoardConfig) -> None:
        self._send_poll(board.address)

    def restart_self(self) -> str:
        def _delayed_exit() -> None:
            time.sleep(1)
            os._exit(0)

        threading.Thread(target=_delayed_exit, daemon=True).start()
        return "Riavvio applicazione richiesto"

    def run_restart_command(self) -> str:
        command = self._config.service.restart_command.strip()
        if not command:
            raise ValueError("restart_command non configurato")

        subprocess.Popen(command, shell=True)
        return f"Comando eseguito: {command}"

    def _start_components_locked(self) -> None:
        self._serial = SerialBridge(self._config.serial, self._handle_serial_frame)
        self._serial.start()

        self._mqtt = MqttBridge(
            self._config.mqtt,
            on_command=self._handle_mqtt_command,
            on_connected=self._handle_mqtt_connected,
        )
        self._mqtt.start()

        self._poll_worker = threading.Thread(target=self._poll_loop, name="poll-loop", daemon=True)
        self._poll_worker.start()

    def _stop_components_locked(self) -> None:
        if self._serial:
            self._serial.stop()
            self._serial = None
        if self._mqtt:
            self._mqtt.stop()
            self._mqtt = None
        if self._poll_worker and self._poll_worker.is_alive():
            self._poll_worker.join(timeout=2)
        self._poll_worker = None

    def _rebuild_indexes(self) -> None:
        self._boards_by_topic = {}
        self._boards_by_address = defaultdict(list)

        for board in self._config.boards:
            if not board.enabled:
                continue
            self._boards_by_topic[board.topic_slug] = board
            self._boards_by_address[board.address].append(board)

    def _poll_loop(self) -> None:
        while not self._shutdown_event.is_set():
            with self._lock:
                interval = self._config.polling.interval_sec
                auto_start = self._config.polling.auto_start

            timeout = interval if auto_start else None
            triggered = self._manual_poll_event.wait(timeout=timeout)

            if self._shutdown_event.is_set():
                return

            if triggered:
                self._manual_poll_event.clear()
                self._poll_all_addresses()
                continue

            if auto_start:
                self._poll_all_addresses()

    def _poll_all_addresses(self) -> None:
        with self._lock:
            addresses = sorted(self._boards_by_address.keys())

        for address in addresses:
            self._send_poll(address)
            time.sleep(0.05)

    def _send_poll(self, address: int) -> None:
        frame = build_polling_extended(address)
        ok, rx = self._send_with_ack(
            frame=frame,
            address=address,
            matcher=lambda rx: rx.command in (CMD_POLLING_EXTENDED, CMD_POLLING_RESPONSE),
            timeout=COMMAND_ACK_TIMEOUT_SEC,
        )

        if ok and rx is not None:
            try:
                polling = parse_polling_status(rx)
                boards = self._boards_by_address.get(address, [])
                for board in boards:
                    self._publish_board_state_from_polling(board, polling)
            except Exception:
                LOGGER.exception("Errore parsing polling su indirizzo %s", address)
                ok = False

        boards = self._boards_by_address.get(address, [])
        for board in boards:
            self._publish_poll_result(board, ok)

        if not ok:
            LOGGER.warning("Polling timeout su indirizzo %s", address)

    def _send_frame(self, frame: bytes) -> bool:
        serial = self._serial
        if serial is None:
            return False
        return serial.send_frame(frame)

    def _send_with_ack(
        self,
        frame: bytes,
        address: int,
        matcher: Callable[[ParsedFrame], bool],
        timeout: float,
    ) -> tuple[bool, ParsedFrame | None]:
        with self._transaction_lock:
            waiter = _AckWaiter(address=address, matcher=matcher)
            with self._ack_lock:
                self._ack_waiters.append(waiter)

            sent = self._send_frame(frame)
            if not sent:
                self._discard_waiter(waiter)
                return False, None

            completed = waiter.event.wait(timeout)
            if not completed:
                self._discard_waiter(waiter)
                return False, None

            return True, waiter.frame

    def _discard_waiter(self, waiter: _AckWaiter) -> None:
        with self._ack_lock:
            if waiter in self._ack_waiters:
                self._ack_waiters.remove(waiter)

    def _resolve_waiters(self, frame: ParsedFrame) -> None:
        with self._ack_lock:
            resolved: list[_AckWaiter] = []
            for waiter in self._ack_waiters:
                if waiter.address != frame.address:
                    continue

                try:
                    if not waiter.matcher(frame):
                        continue
                except Exception:
                    LOGGER.exception("Errore matcher ack")
                    continue

                waiter.frame = frame
                waiter.event.set()
                resolved.append(waiter)

            for waiter in resolved:
                self._ack_waiters.remove(waiter)

    def _topic_prefix(self, board: BoardConfig) -> str:
        return f"{self._config.mqtt.base_topic}/{board.topic_slug}"

    def _publish(self, topic: str, payload: str | int | float | dict, retain: bool = False) -> None:
        mqtt = self._mqtt
        if mqtt is None:
            return
        mqtt.publish(topic, payload, retain=retain)

    def _publish_action_result(self, board: BoardConfig, action: str, success: bool, detail: str) -> None:
        if not board.publish_enabled:
            return

        topic_prefix = self._topic_prefix(board)
        payload = {
            "action": action,
            "success": success,
            "detail": detail,
            "ts": int(time.time()),
        }
        self._publish(f"{topic_prefix}/action/result", payload, retain=False)

    def _publish_poll_result(self, board: BoardConfig, success: bool) -> None:
        if not board.publish_enabled:
            return

        topic_prefix = self._topic_prefix(board)
        payload = {
            "success": success,
            "ts": int(time.time()),
        }
        self._publish(f"{topic_prefix}/poll/last", payload, retain=True)

    def _publish_light_channel_state(self, board: BoardConfig, channel: int, is_on: bool) -> None:
        if not board.publish_enabled:
            return
        if channel not in board.channels:
            return

        topic_prefix = self._topic_prefix(board)
        state = "ON" if is_on else "OFF"
        self._publish(f"{topic_prefix}/ch/{channel}/state", state, retain=True)
        if len(board.channels) == 1 or channel == board.primary_channel:
            self._publish(f"{topic_prefix}/state", state, retain=True)

    def _handle_non_polling_frame(self, frame: ParsedFrame) -> None:
        boards = self._boards_by_address.get(frame.address, [])
        if not boards:
            return

        command = frame.command

        if (
            CMD_LIGHT_CONTROL_START_FIRST_FOUR <= command <= (CMD_LIGHT_CONTROL_START_FIRST_FOUR + 3)
            or CMD_LIGHT_CONTROL_START_FIFTH_ONWARD <= command <= (CMD_LIGHT_CONTROL_START_FIFTH_ONWARD + 3)
        ):
            if command >= CMD_LIGHT_CONTROL_START_FIFTH_ONWARD:
                channel = (command - CMD_LIGHT_CONTROL_START_FIFTH_ONWARD) + 5
            else:
                channel = (command - CMD_LIGHT_CONTROL_START_FIRST_FOUR) + 1

            if frame.data[0] == CMD_LIGHT_DATA_RELAY_ON:
                is_on = True
            elif frame.data[0] == CMD_LIGHT_DATA_RELAY_OFF:
                is_on = False
            else:
                return

            for board in boards:
                if board.board_type != BoardType.LIGHTS:
                    continue
                self._publish_light_channel_state(board, channel, is_on)
            return

        if command == CMD_DIMMER_CONTROL:
            if frame.data[0] != CMD_DIMMER_DATA:
                return
            raw = 10 if frame.data[1] > 8 else int(frame.data[1])
            percent = bus_dimmer_to_percent(raw)
            brightness_255 = int(round((percent / 100.0) * 255))
            for board in boards:
                if board.board_type != BoardType.DIMMER or not board.publish_enabled:
                    continue
                self._dimmer_cache[board.board_id] = percent
                topic_prefix = self._topic_prefix(board)
                self._publish(f"{topic_prefix}/state", "ON" if percent > 0 else "OFF", retain=True)
                self._publish(f"{topic_prefix}/brightness/state", brightness_255, retain=True)
            return

        if command == CMD_SET_POINT_TEMPERATURE:
            setpoint = float(frame.data[0]) + (float(frame.data[1]) / 10.0)
            for board in boards:
                if board.board_type != BoardType.THERMOSTAT or not board.publish_enabled:
                    continue
                topic_prefix = self._topic_prefix(board)
                self._publish(f"{topic_prefix}/setpoint/state", round(setpoint, 1), retain=True)
            return

        if command == CMD_SET_SEASON:
            season = frame.data[0]
            label = "SUMMER" if season == 1 else "WINTER"
            for board in boards:
                if board.board_type != BoardType.THERMOSTAT or not board.publish_enabled:
                    continue
                topic_prefix = self._topic_prefix(board)
                self._publish(f"{topic_prefix}/season/state", label, retain=True)

    def _request_polling_status(self, address: int, timeout: float) -> tuple[bool, Any | None]:
        frame = build_polling_extended(address)
        ok, rx = self._send_with_ack(
            frame=frame,
            address=address,
            matcher=lambda f: f.command in (CMD_POLLING_EXTENDED, CMD_POLLING_RESPONSE),
            timeout=timeout,
        )
        if not ok or rx is None:
            return False, None

        try:
            polling = parse_polling_status(rx)
        except Exception:
            LOGGER.exception("Errore parsing polling durante conferma comando")
            return False, None

        return True, polling

    def _handle_mqtt_connected(self) -> None:
        self._publish_discovery()
        self.trigger_poll_all()

    def _handle_mqtt_command(self, topic: str, payload: str) -> None:
        base = self._config.mqtt.base_topic

        if topic == f"{base}/poll_all/set":
            self.trigger_poll_all()
            return

        if not topic.startswith(f"{base}/"):
            return

        tail = topic[len(base) + 1 :]
        parts = tail.split("/")
        if len(parts) < 2:
            return

        board_slug = parts[0]
        command_path = "/".join(parts[1:])

        board = self._boards_by_topic.get(board_slug)
        if board is None:
            return
        if not board.publish_enabled:
            return

        if command_path == "poll/set":
            self.trigger_poll_for_board(board)
            return

        if board.board_type == BoardType.LIGHTS:
            self._handle_light_command(board, command_path, payload)
            return

        if board.board_type == BoardType.SHUTTERS:
            self._handle_shutter_command(board, command_path, payload)
            return

        if board.board_type == BoardType.DIMMER:
            self._handle_dimmer_command(board, command_path, payload)
            return

        if board.board_type == BoardType.THERMOSTAT:
            self._handle_thermostat_command(board, command_path, payload)

    def _handle_light_command(self, board: BoardConfig, command_path: str, payload: str) -> None:
        channel: int | None = None
        publish_legacy_state = False

        if command_path == "set":
            channel = board.primary_channel
            publish_legacy_state = True
        else:
            match = re.fullmatch(r"ch/(\d+)/set", command_path)
            if not match:
                return
            channel = int(match.group(1))

        if channel not in board.channels:
            LOGGER.warning("Canale %s fuori range per scheda %s", channel, board.name)
            return

        state = _parse_on_off(payload)
        if state is None:
            return

        frame = build_light_control(board.address, channel, state)
        expected_data0 = frame[3]
        expected_command = frame[2]
        ack_ok, _ = self._send_with_ack(
            frame=frame,
            address=board.address,
            matcher=lambda rx: rx.command == expected_command and rx.data[0] == expected_data0,
            timeout=COMMAND_ACK_TIMEOUT_SEC,
        )

        poll_ok, polling = self._request_polling_status(board.address, COMMAND_ACK_TIMEOUT_SEC)
        if poll_ok and polling is not None:
            bit = 1 << (channel - 1)
            is_on = (polling.outputs & bit) != 0
            ok = is_on == state
            self._publish_board_state_from_polling(board, polling)
        else:
            ok = ack_ok

        channel_state = "ON" if state else "OFF"
        if not ok:
            self._publish_action_result(
                board,
                "light_set",
                False,
                f"timeout channel={channel} desired={channel_state}",
            )
            return

        if not poll_ok:
            self._publish_light_channel_state(board, channel, state)
        if publish_legacy_state and not poll_ok:
            topic_prefix = self._topic_prefix(board)
            self._publish(f"{topic_prefix}/state", channel_state, retain=True)
        self._publish_action_result(board, "light_set", True, f"channel={channel} state={channel_state}")

    def _handle_shutter_command(self, board: BoardConfig, command_path: str, payload: str) -> None:
        if command_path != "set":
            return

        normalized = payload.strip().upper()
        if normalized in {"OPEN", "UP", "ON", "1"}:
            up = True
            state = "opening"
        elif normalized in {"CLOSE", "DOWN", "OFF", "0"}:
            up = False
            state = "closing"
        elif normalized == "STOP":
            LOGGER.warning("STOP non supportato dal protocollo tapparelle")
            return
        else:
            return

        frame = build_shutter_control(board.address, board.primary_channel, up)
        expected_data0 = frame[3]
        expected_data1 = frame[4]
        expected_command = frame[2]
        ack_ok, _ = self._send_with_ack(
            frame=frame,
            address=board.address,
            matcher=lambda rx: (
                rx.command == expected_command
                and rx.data[0] == expected_data0
                and rx.data[1] == expected_data1
            ),
            timeout=COMMAND_ACK_TIMEOUT_SEC,
        )
        poll_ok, polling = self._request_polling_status(board.address, COMMAND_ACK_TIMEOUT_SEC)
        if poll_ok and polling is not None:
            bit = 1 << (board.primary_channel - 1)
            is_open = (polling.outputs & bit) != 0
            ok = is_open == up
            self._publish_board_state_from_polling(board, polling)
        else:
            ok = ack_ok

        topic_prefix = self._topic_prefix(board)
        if not ok:
            self._publish_action_result(
                board,
                "shutter_set",
                False,
                f"timeout channel={board.primary_channel} desired={state}",
            )
            return

        if not poll_ok:
            self._publish(f"{topic_prefix}/state", state, retain=True)
        self._publish_action_result(board, "shutter_set", True, state)

    def _handle_dimmer_command(self, board: BoardConfig, command_path: str, payload: str) -> None:
        percent: int | None = None

        if command_path == "set":
            state = _parse_on_off(payload)
            if state is None:
                return
            if state:
                percent = self._dimmer_cache.get(board.board_id, 100)
            else:
                percent = 0
        elif command_path == "brightness/set":
            raw_value = _parse_int(payload)
            if raw_value is None:
                return
            if raw_value <= 100:
                percent = max(0, min(100, raw_value))
            else:
                percent = max(0, min(100, int(round((raw_value / 255.0) * 100))))
        else:
            return

        frame = build_dimmer_control(board.address, percent)
        expected_command = frame[2]
        expected_data0 = frame[3]
        expected_data1 = frame[4]
        ack_ok, _ = self._send_with_ack(
            frame=frame,
            address=board.address,
            matcher=lambda rx: (
                rx.command == expected_command
                and rx.data[0] == expected_data0
                and rx.data[1] == expected_data1
            ),
            timeout=COMMAND_ACK_TIMEOUT_SEC,
        )
        poll_ok, polling = self._request_polling_status(board.address, COMMAND_ACK_TIMEOUT_SEC)
        if poll_ok and polling is not None:
            expected_bus = percent_to_bus_dimmer(percent)
            # Alcune schede riportano 9 come 10 nel polling esteso.
            observed_bus = 10 if polling.dimmer_0_10 >= 9 else polling.dimmer_0_10
            wanted_bus = 10 if expected_bus >= 9 else expected_bus
            ok = observed_bus == wanted_bus
            self._publish_board_state_from_polling(board, polling)
        else:
            ok = ack_ok

        if percent > 0:
            self._dimmer_cache[board.board_id] = percent

        topic_prefix = self._topic_prefix(board)
        brightness_255 = int(round((percent / 100.0) * 255))
        if not ok:
            self._publish_action_result(board, "dimmer_set", False, f"timeout desired_percent={percent}")
            return

        if not poll_ok:
            self._publish(f"{topic_prefix}/state", "ON" if percent > 0 else "OFF", retain=True)
            self._publish(f"{topic_prefix}/brightness/state", brightness_255, retain=True)
        self._publish_action_result(board, "dimmer_set", True, f"percent={percent}")

    def _handle_thermostat_command(self, board: BoardConfig, command_path: str, payload: str) -> None:
        topic_prefix = self._topic_prefix(board)

        if command_path == "setpoint/set":
            setpoint = _parse_float(payload)
            if setpoint is None:
                return
            frame = build_set_point_temperature(board.address, setpoint)
            expected_command = frame[2]
            expected_data0 = frame[3]
            expected_data1 = frame[4]
            ack_ok, _ = self._send_with_ack(
                frame=frame,
                address=board.address,
                matcher=lambda rx: (
                    rx.command == expected_command
                    and rx.data[0] == expected_data0
                    and rx.data[1] == expected_data1
                ),
                timeout=COMMAND_ACK_TIMEOUT_SEC,
            )
            poll_ok, polling = self._request_polling_status(board.address, COMMAND_ACK_TIMEOUT_SEC)
            if poll_ok and polling is not None:
                # Il polling potrebbe arrotondare il decimale del setpoint.
                ok = abs(polling.temperature_setpoint - setpoint) <= 0.6
                self._publish_board_state_from_polling(board, polling)
            else:
                ok = ack_ok
            if not ok:
                self._publish_action_result(board, "setpoint_set", False, f"timeout desired={round(setpoint, 1)}")
                return

            if not poll_ok:
                self._publish(f"{topic_prefix}/setpoint/state", round(setpoint, 1), retain=True)
            self._publish_action_result(board, "setpoint_set", True, f"setpoint={round(setpoint, 1)}")
            return

        if command_path == "season/set":
            season = _parse_season(payload)
            if season is None:
                return
            frame = build_set_season(board.address, season)
            expected_command = frame[2]
            expected_data0 = frame[3]
            ack_ok, _ = self._send_with_ack(
                frame=frame,
                address=board.address,
                matcher=lambda rx: rx.command == expected_command and rx.data[0] == expected_data0,
                timeout=COMMAND_ACK_TIMEOUT_SEC,
            )
            poll_ok, polling = self._request_polling_status(board.address, COMMAND_ACK_TIMEOUT_SEC)
            if poll_ok and polling is not None:
                ok = polling.season == season
                self._publish_board_state_from_polling(board, polling)
            else:
                ok = ack_ok
            if not ok:
                self._publish_action_result(board, "season_set", False, f"timeout desired={season}")
                return

            if not poll_ok:
                self._publish(f"{topic_prefix}/season/state", "SUMMER" if season == 1 else "WINTER", retain=True)
            self._publish_action_result(board, "season_set", True, f"season={season}")

    def _handle_serial_frame(self, frame: ParsedFrame) -> None:
        self._resolve_waiters(frame)

        if frame.command not in (CMD_POLLING_EXTENDED, CMD_POLLING_RESPONSE):
            self._handle_non_polling_frame(frame)
            return

        try:
            polling = parse_polling_status(frame)
        except Exception:
            LOGGER.exception("Errore parsing polling")
            return

        boards = self._boards_by_address.get(frame.address, [])
        for board in boards:
            self._publish_board_state_from_polling(board, polling)

    def _publish_board_state_from_polling(self, board: BoardConfig, polling) -> None:
        if not board.publish_enabled:
            return

        topic_prefix = self._topic_prefix(board)

        raw_payload: dict[str, Any] = {
            "device_type": polling.device_type,
            "outputs": polling.outputs,
            "inputs": polling.inputs,
            "dimmer_0_10": polling.dimmer_0_10,
            "temperature": polling.temperature,
            "temperature_setpoint": polling.temperature_setpoint,
            "season": polling.season,
            "address": board.address,
        }
        self._publish(f"{topic_prefix}/polling/raw", raw_payload, retain=False)

        if board.board_type == BoardType.LIGHTS:
            channels = board.channels
            for channel in channels:
                bit = 1 << (channel - 1)
                state = "ON" if (polling.outputs & bit) else "OFF"
                self._publish(f"{topic_prefix}/ch/{channel}/state", state, retain=True)

            if len(channels) == 1:
                self._publish(f"{topic_prefix}/state", state, retain=True)
            return

        if board.board_type == BoardType.SHUTTERS:
            bit = 1 << (board.primary_channel - 1)
            state = "open" if (polling.outputs & bit) else "closed"
            self._publish(f"{topic_prefix}/state", state, retain=True)
            return

        if board.board_type == BoardType.DIMMER:
            percent = bus_dimmer_to_percent(polling.dimmer_0_10)
            brightness_255 = int(round((percent / 100.0) * 255))
            self._dimmer_cache[board.board_id] = percent
            self._publish(f"{topic_prefix}/state", "ON" if percent > 0 else "OFF", retain=True)
            self._publish(f"{topic_prefix}/brightness/state", brightness_255, retain=True)
            return

        if board.board_type == BoardType.THERMOSTAT:
            self._publish(f"{topic_prefix}/temperature/state", round(polling.temperature, 1), retain=True)
            self._publish(f"{topic_prefix}/setpoint/state", round(polling.temperature_setpoint, 1), retain=True)
            season = "SUMMER" if polling.season == 1 else "WINTER"
            self._publish(f"{topic_prefix}/season/state", season, retain=True)

    def _publish_discovery(self) -> None:
        base = self._config.mqtt.base_topic
        discovery_prefix = self._config.mqtt.discovery_prefix

        poll_button_topic = f"{discovery_prefix}/button/cerebro2mqtt_poll_all/config"
        poll_button_payload = {
            "name": "Cerebro Polling",
            "unique_id": "cerebro2mqtt_poll_all",
            "command_topic": f"{base}/poll_all/set",
            "payload_press": "PRESS",
            "icon": "mdi:refresh",
            "device": {
                "identifiers": ["cerebro2mqtt_bridge"],
                "name": "Cerebro2MQTT Bridge",
                "manufacturer": "Custom",
                "model": "BUS-MQTT",
            },
        }
        self._publish(poll_button_topic, poll_button_payload, retain=True)

        for board in self._config.boards:
            if not board.enabled or not board.publish_enabled:
                self._clear_discovery_for_board(board)
                continue
            self._publish_discovery_for_board(board)

    def _publish_discovery_for_board(self, board: BoardConfig) -> None:
        base = self._config.mqtt.base_topic
        discovery_prefix = self._config.mqtt.discovery_prefix
        slug = board.topic_slug
        topic_prefix = f"{base}/{slug}"
        device = {
            "identifiers": [f"cerebro2mqtt_{board.board_id}"],
            "name": board.name,
            "manufacturer": "AlgoDomo",
            "model": board.board_type.value,
        }

        poll_button_topic = f"{discovery_prefix}/button/cerebro2mqtt_{board.board_id}_poll/config"
        poll_button_payload = {
            "name": f"{board.name} Polling",
            "unique_id": f"cerebro2mqtt_{board.board_id}_poll",
            "command_topic": f"{topic_prefix}/poll/set",
            "payload_press": "PRESS",
            "icon": "mdi:refresh",
            "device": device,
        }
        self._publish(poll_button_topic, poll_button_payload, retain=True)

        if board.board_type == BoardType.LIGHTS:
            for channel in board.channels:
                config_topic = f"{discovery_prefix}/switch/cerebro2mqtt_{board.board_id}_ch{channel}/config"
                payload = {
                    "name": f"{board.name} CH{channel}",
                    "unique_id": f"cerebro2mqtt_{board.board_id}_ch{channel}",
                    "command_topic": f"{topic_prefix}/ch/{channel}/set",
                    "state_topic": f"{topic_prefix}/ch/{channel}/state",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "device": device,
                }
                self._publish(config_topic, payload, retain=True)
            return

        if board.board_type == BoardType.SHUTTERS:
            config_topic = f"{discovery_prefix}/cover/cerebro2mqtt_{board.board_id}/config"
            payload = {
                "name": board.name,
                "unique_id": f"cerebro2mqtt_{board.board_id}",
                "command_topic": f"{topic_prefix}/set",
                "state_topic": f"{topic_prefix}/state",
                "payload_open": "OPEN",
                "payload_close": "CLOSE",
                "payload_stop": "STOP",
                "state_open": "open",
                "state_opening": "opening",
                "state_closed": "closed",
                "state_closing": "closing",
                "device": device,
            }
            self._publish(config_topic, payload, retain=True)
            return

        if board.board_type == BoardType.DIMMER:
            config_topic = f"{discovery_prefix}/light/cerebro2mqtt_{board.board_id}/config"
            payload = {
                "name": board.name,
                "unique_id": f"cerebro2mqtt_{board.board_id}",
                "command_topic": f"{topic_prefix}/set",
                "state_topic": f"{topic_prefix}/state",
                "brightness_command_topic": f"{topic_prefix}/brightness/set",
                "brightness_state_topic": f"{topic_prefix}/brightness/state",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device": device,
            }
            self._publish(config_topic, payload, retain=True)
            return

        if board.board_type == BoardType.THERMOSTAT:
            temp_sensor_topic = f"{discovery_prefix}/sensor/cerebro2mqtt_{board.board_id}_temperature/config"
            temp_sensor_payload = {
                "name": f"{board.name} Temperatura",
                "unique_id": f"cerebro2mqtt_{board.board_id}_temperature",
                "state_topic": f"{topic_prefix}/temperature/state",
                "unit_of_measurement": "C",
                "device_class": "temperature",
                "device": device,
            }
            self._publish(temp_sensor_topic, temp_sensor_payload, retain=True)

            setpoint_topic = f"{discovery_prefix}/number/cerebro2mqtt_{board.board_id}_setpoint/config"
            setpoint_payload = {
                "name": f"{board.name} Setpoint",
                "unique_id": f"cerebro2mqtt_{board.board_id}_setpoint",
                "command_topic": f"{topic_prefix}/setpoint/set",
                "state_topic": f"{topic_prefix}/setpoint/state",
                "mode": "box",
                "min": 5,
                "max": 35,
                "step": 0.5,
                "unit_of_measurement": "C",
                "device": device,
            }
            self._publish(setpoint_topic, setpoint_payload, retain=True)

            season_topic = f"{discovery_prefix}/select/cerebro2mqtt_{board.board_id}_season/config"
            season_payload = {
                "name": f"{board.name} Stagione",
                "unique_id": f"cerebro2mqtt_{board.board_id}_season",
                "command_topic": f"{topic_prefix}/season/set",
                "state_topic": f"{topic_prefix}/season/state",
                "options": ["WINTER", "SUMMER"],
                "device": device,
            }
            self._publish(season_topic, season_payload, retain=True)

    def _clear_discovery_for_board(self, board: BoardConfig) -> None:
        discovery_prefix = self._config.mqtt.discovery_prefix

        self._publish(
            f"{discovery_prefix}/button/cerebro2mqtt_{board.board_id}_poll/config",
            "",
            retain=True,
        )

        if board.board_type == BoardType.LIGHTS:
            for channel in board.channels:
                self._publish(
                    f"{discovery_prefix}/switch/cerebro2mqtt_{board.board_id}_ch{channel}/config",
                    "",
                    retain=True,
                )
            return

        if board.board_type == BoardType.SHUTTERS:
            self._publish(f"{discovery_prefix}/cover/cerebro2mqtt_{board.board_id}/config", "", retain=True)
            return

        if board.board_type == BoardType.DIMMER:
            self._publish(f"{discovery_prefix}/light/cerebro2mqtt_{board.board_id}/config", "", retain=True)
            return

        if board.board_type == BoardType.THERMOSTAT:
            self._publish(
                f"{discovery_prefix}/sensor/cerebro2mqtt_{board.board_id}_temperature/config",
                "",
                retain=True,
            )
            self._publish(
                f"{discovery_prefix}/number/cerebro2mqtt_{board.board_id}_setpoint/config",
                "",
                retain=True,
            )
            self._publish(
                f"{discovery_prefix}/select/cerebro2mqtt_{board.board_id}_season/config",
                "",
                retain=True,
            )


def _parse_on_off(payload: str) -> bool | None:
    normalized = payload.strip().upper()
    if normalized in {"ON", "1", "TRUE", "OPEN", "UP"}:
        return True
    if normalized in {"OFF", "0", "FALSE", "CLOSE", "DOWN"}:
        return False
    return None


def _parse_int(payload: str) -> int | None:
    try:
        return int(float(payload.strip()))
    except ValueError:
        return None


def _parse_float(payload: str) -> float | None:
    try:
        return float(payload.strip().replace(",", "."))
    except ValueError:
        return None


def _parse_season(payload: str) -> int | None:
    normalized = payload.strip().upper()
    if normalized in {"0", "WINTER", "INVERNO"}:
        return 0
    if normalized in {"1", "SUMMER", "ESTATE"}:
        return 1
    return None
