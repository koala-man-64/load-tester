from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx

from loadtester import cli


class SimpleTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})


class AuthTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "token-123"})


class CliTests(unittest.TestCase):
    def test_direct_config_path_runs_without_discovery_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "orders.json"
            output_dir = root / "runs"
            _write_valid_config(config_path, sample_name="payload.json")
            (root / "payload.json").write_text(json.dumps({"order_id": 1}), encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch(
                "builtins.input",
                side_effect=AssertionError("input should not be called"),
            ), patch(
                "loadtester.runner._create_service_client",
                side_effect=lambda config: httpx.AsyncClient(
                    transport=SimpleTransport(),
                    timeout=httpx.Timeout(config.timeout_seconds),
                    verify=config.verify_tls,
                ),
            ):
                exit_code = cli.main(
                    [
                        "--config",
                        str(config_path),
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertNotIn("Configs found in", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())

    def test_index_bypasses_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "configs"
            output_dir = root / "runs"
            config_dir.mkdir()
            _write_valid_config(config_dir / "orders.json", sample_name="payload.json")
            (config_dir / "payload.json").write_text(json.dumps({"order_id": 1}), encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch(
                "builtins.input",
                side_effect=AssertionError("input should not be called"),
            ), patch(
                "loadtester.runner._create_service_client",
                side_effect=lambda config: httpx.AsyncClient(
                    transport=SimpleTransport(),
                    timeout=httpx.Timeout(config.timeout_seconds),
                    verify=config.verify_tls,
                ),
            ):
                exit_code = cli.main(
                    [
                        "--config-dir",
                        str(config_dir),
                        "--index",
                        "0",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Run status: completed", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())

    def test_invalid_config_is_reported_in_skipped_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "configs"
            output_dir = root / "runs"
            config_dir.mkdir()
            _write_valid_config(config_dir / "orders.json", sample_name="payload.json")
            (config_dir / "payload.json").write_text(json.dumps({"order_id": 1}), encoding="utf-8")
            (config_dir / "bad.json").write_text(
                json.dumps(
                    {
                        "service_name": "Bad",
                        "url": "https://service.test/orders",
                        "sample_request_files": ["payload.json"],
                        "duration_seconds": 1,
                        "ramp_up_seconds": 0,
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout), patch(
                "loadtester.runner._create_service_client",
                side_effect=lambda config: httpx.AsyncClient(
                    transport=SimpleTransport(),
                    timeout=httpx.Timeout(config.timeout_seconds),
                    verify=config.verify_tls,
                ),
            ):
                exit_code = cli.main(
                    [
                        "--config-dir",
                        str(config_dir),
                        "--index",
                        "0",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Skipped:", stdout.getvalue())
            self.assertIn("parallel_users", stdout.getvalue())

    def test_list_prints_configs_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "configs"
            output_dir = root / "runs"
            config_dir.mkdir()
            _write_valid_config(config_dir / "orders.json", sample_name="payload.json")
            (config_dir / "payload.json").write_text(json.dumps({"order_id": 1}), encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "--config-dir",
                        str(config_dir),
                        "--list",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("[0] orders.json", stdout.getvalue())
            self.assertFalse(output_dir.exists())
            self.assertEqual("", stderr.getvalue())

    def test_invalid_sample_json_aborts_before_run_folder_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "configs"
            output_dir = root / "runs"
            config_dir.mkdir()
            _write_valid_config(config_dir / "orders.json", sample_name="payload.json")
            (config_dir / "payload.json").write_text("{invalid", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = cli.main(
                    [
                        "--config-dir",
                        str(config_dir),
                        "--index",
                        "0",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("invalid sample JSON", stderr.getvalue())
            self.assertFalse(output_dir.exists())

    def test_validate_only_runs_auth_preflight_without_creating_run_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "orders.json"
            output_dir = root / "runs"
            _write_valid_config(
                config_path,
                sample_name="payload.json",
                extra={
                    "auth": {
                        "token_url": "https://auth.test/token",
                        "token_json_path": "access_token",
                        "body": {"client_id": "abc"},
                    }
                },
            )
            (root / "payload.json").write_text(json.dumps({"order_id": 1}), encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch(
                "loadtester.auth._create_auth_client",
                side_effect=lambda timeout_seconds, verify_tls: httpx.AsyncClient(
                    transport=AuthTransport(),
                    timeout=httpx.Timeout(timeout_seconds),
                    verify=verify_tls,
                ),
            ):
                exit_code = cli.main(
                    [
                        "--config",
                        str(config_path),
                        "--validate-only",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Validation status: passed", stdout.getvalue())
            self.assertIn("Auth preflight:    passed", stdout.getvalue())
            self.assertFalse(output_dir.exists())
            self.assertEqual("", stderr.getvalue())


def _write_valid_config(path: Path, *, sample_name: str, extra: dict | None = None) -> None:
    payload = {
        "service_name": "Orders",
        "url": "https://service.test/orders",
        "sample_request_files": [sample_name],
        "duration_seconds": 0.01,
        "parallel_users": 1,
        "ramp_up_seconds": 0,
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
