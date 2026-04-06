from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loadtester.config import ConfigError, discover_configs, load_config, preload_payloads


class ConfigTests(unittest.TestCase):
    def test_relative_sample_paths_resolve_from_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "configs"
            request_dir = root / "requests"
            config_dir.mkdir()
            request_dir.mkdir()

            config_path = config_dir / "orders.json"
            sample_path = request_dir / "order.json"
            sample_path.write_text('{"order_id": 1}', encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "service_name": "Orders",
                        "url": "https://example.test/orders",
                        "sample_request_files": ["../requests/order.json"],
                        "duration_seconds": 10,
                        "parallel_users": 1,
                        "ramp_up_seconds": 0,
                    }
                ),
                encoding="utf-8",
            )

            cfg = load_config(config_path)

            self.assertEqual(cfg.sample_request_files, [sample_path.resolve()])

    def test_invalid_config_is_skipped_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            (config_dir / "valid.json").write_text(
                json.dumps(
                    {
                        "service_name": "Valid",
                        "url": "https://example.test/orders",
                        "sample_request_files": ["payload.json"],
                        "duration_seconds": 10,
                        "parallel_users": 1,
                        "ramp_up_seconds": 0,
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "bad.json").write_text(
                json.dumps(
                    {
                        "service_name": "Bad",
                        "url": "https://example.test/orders",
                        "sample_request_files": ["payload.json"],
                        "duration_seconds": 10,
                        "ramp_up_seconds": 0,
                    }
                ),
                encoding="utf-8",
            )

            discovered, skipped = discover_configs(config_dir)

            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0].path.name, "valid.json")
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0][0].name, "bad.json")
            self.assertIn("parallel_users", skipped[0][1])

    def test_invalid_sample_json_aborts_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            payload_path = root / "payload.json"
            payload_path.write_text("{invalid", encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "service_name": "Orders",
                        "url": "https://example.test/orders",
                        "sample_request_files": ["payload.json"],
                        "duration_seconds": 10,
                        "parallel_users": 1,
                        "ramp_up_seconds": 0,
                    }
                ),
                encoding="utf-8",
            )

            cfg = load_config(config_path)

            with self.assertRaises(ConfigError) as context:
                preload_payloads(cfg)

            self.assertIn("invalid sample JSON", str(context.exception))
