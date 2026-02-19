import unittest

from app.protocol import (
    CMD_DIMMER_CONTROL,
    CMD_LIGHT_CONTROL_START_FIRST_FOUR,
    CMD_LIGHT_CONTROL_START_FIFTH_ONWARD,
    CMD_POLLING_EXTENDED,
    CMD_POLLING_RESPONSE,
    CMD_SHUTTER_CONTROL,
    build_dimmer_control,
    build_light_control,
    build_polling_extended,
    build_shutter_control,
    ProtocolError,
    parse_frame,
    parse_polling_status,
)


class ProtocolTest(unittest.TestCase):
    def test_build_polling_frame(self):
        frame = build_polling_extended(2)
        self.assertEqual(len(frame), 14)
        self.assertEqual(frame[0], 0x49)
        self.assertEqual(frame[1], 2)
        self.assertEqual(frame[2], CMD_POLLING_EXTENDED)
        self.assertEqual(frame[-1], 0x46)

    def test_build_light_command_mapping(self):
        frame1 = build_light_control(10, 1, True)
        frame5 = build_light_control(10, 5, False)

        self.assertEqual(frame1[2], CMD_LIGHT_CONTROL_START_FIRST_FOUR)
        self.assertEqual(frame5[2], CMD_LIGHT_CONTROL_START_FIFTH_ONWARD)

    def test_build_shutter_and_dimmer(self):
        shutter = build_shutter_control(7, 3, True)
        dimmer = build_dimmer_control(7, 80)

        self.assertEqual(shutter[2], CMD_SHUTTER_CONTROL)
        self.assertEqual(dimmer[2], CMD_DIMMER_CONTROL)

    def test_build_shutter_rejects_channel_out_of_range(self):
        with self.assertRaises(ProtocolError):
            build_shutter_control(7, 5, True)

    def test_parse_polling_status(self):
        raw = bytes([
            0x49,
            0x02,
            CMD_POLLING_EXTENDED,
            0x11,
            0b00000101,
            0x00,
            0x04,
            0x16,
            0x00,
            0x00,
            0x02,
            0x02,
            0x01,
            0x46,
        ])

        parsed = parse_frame(raw)
        polling = parse_polling_status(parsed)

        self.assertEqual(polling.device_type, 0x11)
        self.assertEqual(polling.outputs, 0b00000101)
        self.assertEqual(polling.dimmer_0_10, 4)
        self.assertAlmostEqual(polling.temperature, 22.0)

    def test_parse_polling_status_accepts_0x50(self):
        raw = bytes([
            0x49,
            0x02,
            CMD_POLLING_RESPONSE,
            0x11,
            0b00000001,
            0x00,
            0x04,
            0x16,
            0x00,
            0x00,
            0x02,
            0x02,
            0x01,
            0x46,
        ])

        parsed = parse_frame(raw)
        polling = parse_polling_status(parsed)
        self.assertEqual(polling.outputs, 0b00000001)

    def test_parse_frame_accepts_15_bytes(self):
        raw = bytes([
            0x49,
            0x02,
            CMD_POLLING_RESPONSE,
            0x11,
            0b00000001,
            0x00,
            0x04,
            0x16,
            0x00,
            0x00,
            0x02,
            0x02,
            0x01,
            0xF8,
            0x46,
        ])

        parsed = parse_frame(raw)
        self.assertEqual(parsed.address, 0x02)
        self.assertEqual(parsed.command, CMD_POLLING_RESPONSE)
        self.assertEqual(parsed.extra, bytes([0xF8]))


if __name__ == "__main__":
    unittest.main()
