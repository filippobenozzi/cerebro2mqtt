from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import serial

from app.models import SerialConfig
from app.protocol import FRAME_END_BYTE, FRAME_LENGTH, FRAME_START_BYTE, ParsedFrame, parse_frame

LOGGER = logging.getLogger(__name__)


class SerialBridge:
    def __init__(self, config: SerialConfig, on_frame: Callable[[ParsedFrame], None]):
        self._config = config
        self._on_frame = on_frame
        self._running = threading.Event()
        self._serial_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._serial: serial.Serial | None = None

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._running.set()
        self._worker = threading.Thread(target=self._run, name="serial-bridge", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._running.clear()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)
        self._close_serial()

    def send_frame(self, frame: bytes) -> bool:
        with self._serial_lock:
            ser = self._serial
            if ser is None:
                LOGGER.warning("Seriale non connessa, frame scartato")
                return False

            try:
                ser.write(frame)
                ser.flush()
                return True
            except serial.SerialException:
                LOGGER.exception("Errore invio su seriale")
                self._close_serial_locked()
                return False

    def _open_serial(self) -> bool:
        with self._serial_lock:
            if self._serial is not None:
                return True

            open_kwargs = {
                "port": self._config.port,
                "baudrate": self._config.baudrate,
                "bytesize": self._config.bytesize,
                "parity": self._config.parity,
                "stopbits": self._config.stopbits,
                "timeout": self._config.timeout_sec,
                "write_timeout": self._config.timeout_sec,
                "exclusive": True,
            }

            try:
                self._serial = serial.Serial(**open_kwargs)
            except TypeError:
                # Fallback pyserial without "exclusive" support
                open_kwargs.pop("exclusive", None)
                self._serial = serial.Serial(**open_kwargs)
            except serial.SerialException as exc:
                message = str(exc).lower()
                if "busy" in message or "denied" in message or "permission" in message:
                    LOGGER.warning(
                        "Impossibile aprire seriale %s: %s (porta occupata/non permessa)",
                        self._config.port,
                        exc,
                    )
                else:
                    LOGGER.exception(
                        "Impossibile aprire seriale %s (porta occupata o non disponibile)",
                        self._config.port,
                    )
                return False

            try:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
            except serial.SerialException:
                LOGGER.warning("Impossibile resettare i buffer seriali")

            LOGGER.info("Seriale aperta su %s @ %d", self._config.port, self._config.baudrate)
            return True

    def _close_serial(self) -> None:
        with self._serial_lock:
            self._close_serial_locked()

    def _close_serial_locked(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        except serial.SerialException:
            LOGGER.exception("Errore chiusura seriale")
        finally:
            self._serial = None

    def _get_serial(self) -> serial.Serial | None:
        with self._serial_lock:
            return self._serial

    def _read_exact(self, ser: serial.Serial, size: int, timeout_multiplier: float = 1.0) -> bytes:
        deadline = time.monotonic() + max(self._config.timeout_sec * timeout_multiplier, 0.25)
        chunks = bytearray()

        while self._running.is_set() and len(chunks) < size:
            remaining = size - len(chunks)
            chunk = ser.read(remaining)
            if chunk:
                chunks.extend(chunk)
                continue

            if time.monotonic() >= deadline:
                break

        return bytes(chunks)

    def _run(self) -> None:
        backoff_sec = 1.0
        while self._running.is_set():
            if not self._open_serial():
                time.sleep(min(backoff_sec, 8.0))
                backoff_sec = min(backoff_sec * 1.5, 8.0)
                continue

            backoff_sec = 1.0
            ser = self._get_serial()
            if ser is None:
                time.sleep(0.2)
                continue

            try:
                first = self._read_exact(ser, 1, timeout_multiplier=1.0)
                if len(first) != 1:
                    continue
                if first[0] != FRAME_START_BYTE:
                    continue

                rest = self._read_exact(ser, FRAME_LENGTH - 1, timeout_multiplier=float(FRAME_LENGTH))
                if len(rest) != FRAME_LENGTH - 1:
                    continue

                raw = first + rest
                if raw[-1] != FRAME_END_BYTE:
                    LOGGER.warning("Frame ricevuto con end marker errato: 0x%02X", raw[-1])
                    continue

                try:
                    parsed = parse_frame(raw)
                except Exception:
                    LOGGER.exception("Frame ricevuto non valido")
                    continue

                self._on_frame(parsed)
            except serial.SerialException as exc:
                message = str(exc).lower()
                if "multiple access" in message or "disconnected" in message:
                    LOGGER.warning(
                        "Seriale persa su %s: %s. Possibile conflitto con un altro processo o disconnessione.",
                        self._config.port,
                        exc,
                    )
                else:
                    LOGGER.exception("Errore lettura seriale")
                self._close_serial()
                time.sleep(1)
