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
        self._writer_lock = threading.Lock()
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
        ser = self._serial
        if ser is None:
            LOGGER.warning("Seriale non connessa, frame scartato")
            return False

        with self._writer_lock:
            try:
                ser.write(frame)
                ser.flush()
                return True
            except serial.SerialException:
                LOGGER.exception("Errore invio su seriale")
                self._close_serial()
                return False

    def _open_serial(self) -> bool:
        if self._serial is not None:
            return True

        try:
            self._serial = serial.Serial(
                port=self._config.port,
                baudrate=self._config.baudrate,
                bytesize=self._config.bytesize,
                parity=self._config.parity,
                stopbits=self._config.stopbits,
                timeout=self._config.timeout_sec,
            )
            LOGGER.info("Seriale aperta su %s @ %d", self._config.port, self._config.baudrate)
            return True
        except serial.SerialException:
            LOGGER.exception("Impossibile aprire seriale %s", self._config.port)
            return False

    def _close_serial(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        except serial.SerialException:
            LOGGER.exception("Errore chiusura seriale")
        finally:
            self._serial = None

    def _run(self) -> None:
        while self._running.is_set():
            if not self._open_serial():
                time.sleep(3)
                continue

            assert self._serial is not None
            try:
                first = self._serial.read(1)
                if not first:
                    continue
                if first[0] != FRAME_START_BYTE:
                    continue

                rest = self._serial.read(FRAME_LENGTH - 1)
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
            except serial.SerialException:
                LOGGER.exception("Errore lettura seriale")
                self._close_serial()
                time.sleep(1)
