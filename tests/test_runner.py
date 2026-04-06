from __future__ import annotations

import asyncio
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from loadtester.config import load_config, preload_payloads
from loadtester.runner import compute_start_delay, run_load_test


class SequenceTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._handler(request)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def test_compute_start_delay(self) -> None:
        self.assertEqual(compute_start_delay(0, 4, 6), 0.0)
        self.assertEqual(compute_start_delay(3, 4, 6), 6.0)
        self.assertAlmostEqual(compute_start_delay(1, 4, 6), 2.0)

    async def test_run_load_test_writes_artifacts_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = _write_config_and_payloads(
                root,
                config_data={
                    "service_name": "Orders",
                    "url": "https://service.test/orders",
                    "headers": {"Content-Type": "application/json"},
                    "metadata": {"environment": "dev", "build_id": "42"},
                    "auth": {
                        "token_url": "https://auth.test/token",
                        "token_json_path": "access_token",
                        "body_mode": "json",
                        "body": {"client_id": "abc"},
                    },
                    "duration_seconds": 0.13,
                    "parallel_users": 1,
                    "ramp_up_seconds": 0,
                    "timeout_seconds": 5,
                },
                payloads=[
                    {"order_id": 1},
                    {"order_id": 2},
                ],
            )
            cfg = load_config(config_path)
            payloads = preload_payloads(cfg)
            output_root = root / "runs"

            auth_transport = SequenceTransport(_auth_handler)
            service_transport = SequenceTransport(_service_sequence_handler())

            with patch(
                "loadtester.auth._create_auth_client",
                side_effect=lambda timeout_seconds, verify_tls: httpx.AsyncClient(
                    transport=auth_transport,
                    timeout=httpx.Timeout(timeout_seconds),
                    verify=verify_tls,
                ),
            ), patch(
                "loadtester.runner._create_service_client",
                side_effect=lambda config: httpx.AsyncClient(
                    transport=service_transport,
                    timeout=httpx.Timeout(config.timeout_seconds),
                    verify=config.verify_tls,
                ),
            ):
                summary = await run_load_test(cfg, config_path, payloads, output_root)

            self.assertEqual(summary.run_status, "completed")
            self.assertGreaterEqual(summary.request_attempts, 3)
            self.assertEqual(summary.request_attempts, summary.http_response_count + summary.exception_count)
            self.assertGreaterEqual(summary.http_response_count, 2)
            self.assertGreaterEqual(summary.exception_count, 1)
            self.assertEqual(summary.status_code_counts["200"], 1)
            self.assertEqual(summary.status_code_counts["500"], 1)
            self.assertEqual(summary.metadata, {"environment": "dev", "build_id": "42"})
            self.assertGreater(summary.requests_per_second, 0)
            self.assertGreater(summary.wall_time_seconds, 0)
            self.assertIsNotNone(summary.p50_response_ms)
            self.assertIsNotNone(summary.p95_response_ms)

            run_dir = Path(summary.output_dir)
            self.assertTrue((run_dir / "input_config.json").exists())
            self.assertTrue((run_dir / "details.csv").exists())
            self.assertTrue((run_dir / "summary.json").exists())

            with (run_dir / "details.csv").open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), summary.request_attempts)
            self.assertEqual(rows[0]["sample_file"], "payload_1.json")
            self.assertEqual(rows[1]["sample_file"], "payload_2.json")
            self.assertTrue(any(row["error_type"] == "ConnectError" for row in rows))

            self.assertEqual(service_transport.requests[0].headers["Authorization"], "Bearer token-123")

            with (run_dir / "summary.json").open("r", encoding="utf-8") as handle:
                summary_json = json.load(handle)
            self.assertEqual(summary_json["request_attempts"], summary.request_attempts)
            self.assertEqual(summary_json["metadata"]["build_id"], "42")

    async def test_inflight_request_can_finish_after_deadline(self) -> None:
        async def delayed_handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.08)
            return httpx.Response(200, json={"ok": True})

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = _write_config_and_payloads(
                root,
                config_data={
                    "service_name": "Orders",
                    "url": "https://service.test/orders",
                    "duration_seconds": 0.05,
                    "parallel_users": 1,
                    "ramp_up_seconds": 0,
                },
                payloads=[{"order_id": 1}],
            )
            cfg = load_config(config_path)
            payloads = preload_payloads(cfg)
            transport = SequenceTransport(delayed_handler)

            with patch(
                "loadtester.runner._create_service_client",
                side_effect=lambda config: httpx.AsyncClient(
                    transport=transport,
                    timeout=httpx.Timeout(config.timeout_seconds),
                    verify=config.verify_tls,
                ),
            ):
                summary = await run_load_test(cfg, config_path, payloads, root / "runs")

            self.assertEqual(summary.request_attempts, 1)
            self.assertEqual(summary.http_response_count, 1)
            self.assertEqual(len(transport.requests), 1)

    async def test_cancellation_writes_partial_summary(self) -> None:
        async def slow_handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.2)
            return httpx.Response(200, json={"ok": True})

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = _write_config_and_payloads(
                root,
                config_data={
                    "service_name": "Orders",
                    "url": "https://service.test/orders",
                    "duration_seconds": 5,
                    "parallel_users": 2,
                    "ramp_up_seconds": 0,
                },
                payloads=[{"order_id": 1}],
            )
            cfg = load_config(config_path)
            payloads = preload_payloads(cfg)
            transport = SequenceTransport(slow_handler)

            with patch(
                "loadtester.runner._create_service_client",
                side_effect=lambda config: httpx.AsyncClient(
                    transport=transport,
                    timeout=httpx.Timeout(config.timeout_seconds),
                    verify=config.verify_tls,
                ),
            ):
                task = asyncio.create_task(run_load_test(cfg, config_path, payloads, root / "runs"))
                await asyncio.sleep(0.05)
                task.cancel()
                summary = await task

            self.assertEqual(summary.run_status, "cancelled")
            self.assertTrue((Path(summary.output_dir) / "summary.json").exists())

    async def test_heartbeat_emits_periodic_progress(self) -> None:
        async def slow_handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.03)
            return httpx.Response(200, json={"ok": True})

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = _write_config_and_payloads(
                root,
                config_data={
                    "service_name": "Orders",
                    "url": "https://service.test/orders",
                    "duration_seconds": 0.08,
                    "parallel_users": 1,
                    "ramp_up_seconds": 0,
                },
                payloads=[{"order_id": 1}],
            )
            cfg = load_config(config_path)
            payloads = preload_payloads(cfg)
            transport = SequenceTransport(slow_handler)
            progress_lines: list[str] = []

            with patch(
                "loadtester.runner._create_service_client",
                side_effect=lambda config: httpx.AsyncClient(
                    transport=transport,
                    timeout=httpx.Timeout(config.timeout_seconds),
                    verify=config.verify_tls,
                ),
            ):
                await run_load_test(
                    cfg,
                    config_path,
                    payloads,
                    root / "runs",
                    heartbeat_interval_seconds=0.01,
                    heartbeat_printer=progress_lines.append,
                )

            self.assertTrue(progress_lines)
            self.assertTrue(progress_lines[0].startswith("[progress "))


async def _auth_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "token-123"})


def _service_sequence_handler():
    sequence = [
        ("response", 200),
        ("response", 500),
        ("exception", "boom"),
        ("response", 400),
    ]
    index = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal index
        await asyncio.sleep(0.03)
        kind, value = sequence[index]
        index += 1
        if kind == "exception":
            raise httpx.ConnectError(str(value), request=request)
        return httpx.Response(int(value), json={"ok": True})

    return handler


def _write_config_and_payloads(
    root: Path,
    *,
    config_data: dict,
    payloads: list[dict],
) -> Path:
    normalized_sample_files = []
    for idx, payload in enumerate(payloads, start=1):
        suffix = f"_{idx}" if len(payloads) > 1 else ""
        file_name = f"payload{suffix}.json"
        (root / file_name).write_text(json.dumps(payload), encoding="utf-8")
        normalized_sample_files.append(file_name)

    config_json = dict(config_data)
    config_json["sample_request_files"] = normalized_sample_files

    config_path = root / "config.json"
    config_path.write_text(json.dumps(config_json), encoding="utf-8")
    return config_path
