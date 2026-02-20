import tempfile
import unittest
from pathlib import Path

from app.config_store import ConfigError, ConfigStore


class ConfigStoreTest(unittest.TestCase):
    def test_create_default_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ConfigStore(path)
            cfg = store.config
            self.assertEqual(cfg.mqtt.base_topic, "algodomo2mqtt")
            self.assertTrue(path.exists())

    def test_reject_duplicate_topics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ConfigStore(path)
            payload = store.config.to_dict()
            payload["boards"] = [
                {
                    "id": "1",
                    "name": "Luce Sala",
                    "type": "luci",
                    "address": 2,
                    "channel": 1,
                    "topic": "sala",
                    "enabled": True,
                },
                {
                    "id": "2",
                    "name": "Luce Cucina",
                    "type": "luci",
                    "address": 3,
                    "channel": 1,
                    "topic": "sala",
                    "enabled": True,
                },
            ]

            with self.assertRaises(ConfigError):
                store.update_from_dict(payload)

    def test_accept_lights_channel_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ConfigStore(path)
            payload = store.config.to_dict()
            payload["boards"] = [
                {
                    "id": "1",
                    "name": "Luci Zona Giorno",
                    "type": "luci",
                    "address": 2,
                    "channel_start": 1,
                    "channel_end": 8,
                    "topic": "zona_giorno",
                    "enabled": True,
                }
            ]

            cfg = store.update_from_dict(payload)
            self.assertEqual(cfg.boards[0].channel_start, 1)
            self.assertEqual(cfg.boards[0].channel_end, 8)

    def test_reject_invalid_lights_channel_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ConfigStore(path)
            payload = store.config.to_dict()
            payload["boards"] = [
                {
                    "id": "1",
                    "name": "Luci Range Errato",
                    "type": "luci",
                    "address": 2,
                    "channel_start": 8,
                    "channel_end": 1,
                    "topic": "luci_errato",
                    "enabled": True,
                }
            ]

            with self.assertRaises(ConfigError):
                store.update_from_dict(payload)

    def test_publish_enabled_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ConfigStore(path)
            payload = store.config.to_dict()
            payload["boards"] = [
                {
                    "id": "1",
                    "name": "Scheda Non Pubblicata",
                    "type": "luci",
                    "address": 2,
                    "channel_start": 1,
                    "channel_end": 1,
                    "topic": "non_pubblicata",
                    "publish_enabled": False,
                    "enabled": True,
                }
            ]

            cfg = store.update_from_dict(payload)
            self.assertFalse(cfg.boards[0].publish_enabled)

    def test_accept_shutters_channel_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ConfigStore(path)
            payload = store.config.to_dict()
            payload["boards"] = [
                {
                    "id": "1",
                    "name": "Tapparelle Zona Notte",
                    "type": "tapparelle",
                    "address": 4,
                    "channel_start": 1,
                    "channel_end": 4,
                    "topic": "tapparelle_zona_notte",
                    "enabled": True,
                }
            ]

            cfg = store.update_from_dict(payload)
            self.assertEqual(cfg.boards[0].channel_start, 1)
            self.assertEqual(cfg.boards[0].channel_end, 4)

    def test_reject_invalid_shutters_channel_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ConfigStore(path)
            payload = store.config.to_dict()
            payload["boards"] = [
                {
                    "id": "1",
                    "name": "Tapparelle Range Errato",
                    "type": "tapparelle",
                    "address": 4,
                    "channel_start": 1,
                    "channel_end": 5,
                    "topic": "tapparelle_errato",
                    "enabled": True,
                }
            ]

            with self.assertRaises(ConfigError):
                store.update_from_dict(payload)

    def test_invalid_serial_values_are_sanitized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ConfigStore(path)
            payload = store.config.to_dict()
            payload["serial"] = {
                "port": "/dev/ttyS0",
                "baudrate": 9600,
                "bytesize": 15,
                "parity": "X",
                "stopbits": 9,
                "timeout_sec": 0,
            }

            cfg = store.update_from_dict(payload)
            self.assertEqual(cfg.serial.bytesize, 8)
            self.assertEqual(cfg.serial.parity, "N")
            self.assertEqual(cfg.serial.stopbits, 1)
            self.assertEqual(cfg.serial.timeout_sec, 0.25)

if __name__ == "__main__":
    unittest.main()
