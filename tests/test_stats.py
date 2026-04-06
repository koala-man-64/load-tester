from __future__ import annotations

import unittest
from pathlib import Path

from loadtester.models import LoadTestConfig, RequestResult
from loadtester.stats import LatencyStats, SummaryAccumulator


class StatsTests(unittest.TestCase):
    def test_latency_stats_percentiles(self) -> None:
        stats = LatencyStats()
        for value in [10.0, 20.0, 30.0, 40.0]:
            stats.add(value)

        self.assertEqual(stats.min_ms, 10.0)
        self.assertEqual(stats.max_ms, 40.0)
        self.assertEqual(stats.percentile(50), 25.0)
        self.assertEqual(stats.percentile(90), 37.0)
        self.assertEqual(stats.percentile(95), 38.5)
        self.assertAlmostEqual(stats.percentile(99), 39.7)

    def test_summary_accumulator_includes_metadata_and_rates(self) -> None:
        cfg = LoadTestConfig(
            service_name="Orders",
            method="POST",
            url="https://service.test/orders",
            headers={},
            metadata={"environment": "qa"},
            auth=None,
            sample_request_files=[Path("payload.json")],
            duration_seconds=10.0,
            parallel_users=1,
            ramp_up_seconds=0.0,
            timeout_seconds=30.0,
            verify_tls=True,
        )
        accumulator = SummaryAccumulator(
            cfg=cfg,
            config_path=Path("config.json"),
            started_at_utc="2026-04-06T00:00:00Z",
            output_dir=Path("runs/test"),
        )
        accumulator.record(
            RequestResult(
                request_number=1,
                user_id=0,
                sample_file="payload.json",
                method="POST",
                url=cfg.url,
                started_at_utc="2026-04-06T00:00:01Z",
                elapsed_ms=10.0,
                status_code=200,
                response_bytes=12,
                error_type=None,
                error_message=None,
            )
        )
        accumulator.record(
            RequestResult(
                request_number=2,
                user_id=0,
                sample_file="payload.json",
                method="POST",
                url=cfg.url,
                started_at_utc="2026-04-06T00:00:02Z",
                elapsed_ms=40.0,
                status_code=500,
                response_bytes=13,
                error_type=None,
                error_message=None,
            )
        )
        summary = accumulator.build_summary(
            run_status="completed",
            finished_at_utc="2026-04-06T00:00:10Z",
            wall_time_seconds=2.0,
        )

        self.assertEqual(summary.metadata, {"environment": "qa"})
        self.assertEqual(summary.requests_per_second, 1.0)
        self.assertEqual(summary.p50_response_ms, 25.0)
        self.assertEqual(summary.p90_response_ms, 37.0)
