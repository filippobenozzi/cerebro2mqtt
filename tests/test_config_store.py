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
            self.assertEqual(cfg.mqtt.base_topic, "cerebro2mqtt")
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


if __name__ == "__main__":
    unittest.main()
