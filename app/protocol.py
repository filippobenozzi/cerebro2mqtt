from __future__ import annotations

from dataclasses import dataclass


FRAME_START_BYTE = 0x49
FRAME_END_BYTE = 0x46
FRAME_LENGTH = 14
FRAME_MIN_LENGTH = 14
FRAME_MAX_LENGTH = 15
DATA_LENGTH = 10

CMD_POLLING_EXTENDED = 0x40
CMD_POLLING_RESPONSE = 0x50
CMD_SET_POINT_TEMPERATURE = 0x5A
CMD_SET_SEASON = 0x6B
CMD_LIGHT_CONTROL_START_FIRST_FOUR = 0x51
CMD_LIGHT_CONTROL_START_FIFTH_ONWARD = 0x65
CMD_SHUTTER_CONTROL = 0x5C
CMD_DIMMER_CONTROL = 0x5B
CMD_SCENARIO_CONTROL = 0xAA

CMD_LIGHT_DATA_RELAY_ON = 0x41
CMD_LIGHT_DATA_RELAY_OFF = 0x53

CMD_SHUTTER_DATA_UP = 0x55
CMD_SHUTTER_DATA_DOWN = 0x44
CMD_DIMMER_DATA = 0x53


@dataclass
class ParsedFrame:
    address: int
    command: int
    data: bytes
    raw: bytes
    extra: bytes = b""


@dataclass
class PollingStatus:
    device_type: int
    outputs: int
    inputs: int
    dimmer_0_10: int
    temperature: float
    temperature_setpoint: float
    season: int


class ProtocolError(ValueError):
    pass


def _check_address(address: int) -> None:
    if address < 1 or address > 254:
        raise ProtocolError(f"Address fuori range: {address}")


def build_frame(address: int, command: int, data: list[int] | None = None) -> bytes:
    _check_address(address)

    data = data or []
    if len(data) > DATA_LENGTH:
        raise ProtocolError("Data troppo lungo")

    payload = [0] * DATA_LENGTH
    for index, value in enumerate(data):
        payload[index] = int(value) & 0xFF

    return bytes([FRAME_START_BYTE, address & 0xFF, command & 0xFF, *payload, FRAME_END_BYTE])


def parse_frame(raw: bytes) -> ParsedFrame:
    frame_len = len(raw)
    if frame_len < FRAME_MIN_LENGTH or frame_len > FRAME_MAX_LENGTH:
        raise ProtocolError("Lunghezza frame non valida")
    if raw[0] != FRAME_START_BYTE:
        raise ProtocolError("Start byte non valido")
    if raw[-1] != FRAME_END_BYTE:
        raise ProtocolError("End byte non valido")

    data = raw[3:13]
    extra = raw[13:-1] if frame_len > FRAME_LENGTH else b""
    return ParsedFrame(address=raw[1], command=raw[2], data=data, raw=raw, extra=extra)


def build_polling_extended(address: int) -> bytes:
    return build_frame(address, CMD_POLLING_EXTENDED)


def build_set_point_temperature(address: int, temperature_set: float) -> bytes:
    if temperature_set < 0:
        raise ProtocolError("Setpoint negativo non supportato")

    integer = int(temperature_set)
    decimal = int(round((temperature_set - integer) * 10))
    return build_frame(address, CMD_SET_POINT_TEMPERATURE, [integer, decimal])


def build_set_season(address: int, season: int) -> bytes:
    if season not in (0, 1):
        raise ProtocolError("Season deve essere 0 (winter) o 1 (summer)")
    return build_frame(address, CMD_SET_SEASON, [season])


def build_light_control(address: int, relay_index: int, enabled: bool) -> bytes:
    if relay_index < 1 or relay_index > 8:
        raise ProtocolError("relay_index deve essere fra 1 e 8")

    if relay_index >= 5:
        command = CMD_LIGHT_CONTROL_START_FIFTH_ONWARD + (relay_index - 5)
    else:
        command = CMD_LIGHT_CONTROL_START_FIRST_FOUR + (relay_index - 1)

    state = CMD_LIGHT_DATA_RELAY_ON if enabled else CMD_LIGHT_DATA_RELAY_OFF
    return build_frame(address, command, [state])


def build_shutter_control(address: int, shutter_index: int, up: bool) -> bytes:
    if shutter_index < 1 or shutter_index > 4:
        raise ProtocolError("shutter_index deve essere fra 1 e 4")
    action = CMD_SHUTTER_DATA_UP if up else CMD_SHUTTER_DATA_DOWN
    return build_frame(address, CMD_SHUTTER_CONTROL, [shutter_index, action])


def percent_to_bus_dimmer(percent: int) -> int:
    bounded = max(0, min(100, int(percent)))
    value = (bounded * 10) // 100
    return min(9, value)


def bus_dimmer_to_percent(value: int) -> int:
    bounded = max(0, min(10, int(value)))
    return int((bounded / 10.0) * 100)


def build_dimmer_control(address: int, percent: int) -> bytes:
    return build_frame(address, CMD_DIMMER_CONTROL, [CMD_DIMMER_DATA, percent_to_bus_dimmer(percent)])


def parse_polling_status(frame: ParsedFrame) -> PollingStatus:
    if frame.command not in (CMD_POLLING_EXTENDED, CMD_POLLING_RESPONSE):
        raise ProtocolError(f"Comando inatteso in polling response: 0x{frame.command:02X}")

    raw_dimmer = frame.data[3]
    dimmer = 10 if raw_dimmer > 8 else raw_dimmer

    temperature = float(frame.data[4]) + float(frame.data[5]) / 10.0
    if frame.data[6] == 0x2D:
        temperature = -temperature

    temperature_setpoint = float(frame.data[8]) + float(frame.data[7]) / 10.0

    return PollingStatus(
        device_type=frame.data[0],
        outputs=frame.data[1],
        inputs=frame.data[2],
        dimmer_0_10=dimmer,
        temperature=temperature,
        temperature_setpoint=temperature_setpoint,
        season=frame.data[9],
    )
