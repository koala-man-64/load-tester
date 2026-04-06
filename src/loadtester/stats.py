from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from .models import LoadTestConfig, RequestResult, RunStatus, RunSummary


class LatencyStats:
    def __init__(self) -> None:
        self.count = 0
        self.min_ms: float | None = None
        self.max_ms: float | None = None
        self._mean = 0.0
        self._m2 = 0.0
        self._samples: list[float] = []

    def add(self, value_ms: float) -> None:
        self.count += 1
        if self.min_ms is None or value_ms < self.min_ms:
            self.min_ms = value_ms
        if self.max_ms is None or value_ms > self.max_ms:
            self.max_ms = value_ms

        self._samples.append(value_ms)
        delta = value_ms - self._mean
        self._mean += delta / self.count
        delta2 = value_ms - self._mean
        self._m2 += delta * delta2

    @property
    def avg_ms(self) -> float | None:
        if self.count == 0:
            return None
        return self._mean

    @property
    def stdev_ms(self) -> float | None:
        if self.count == 0:
            return None
        return math.sqrt(self._m2 / self.count)

    def percentile(self, percentile: float) -> float | None:
        if not self._samples:
            return None
        ordered = sorted(self._samples)
        if len(ordered) == 1:
            return ordered[0]
        rank = (percentile / 100.0) * (len(ordered) - 1)
        lower_index = math.floor(rank)
        upper_index = math.ceil(rank)
        if lower_index == upper_index:
            return ordered[lower_index]
        fraction = rank - lower_index
        lower_value = ordered[lower_index]
        upper_value = ordered[upper_index]
        return lower_value + ((upper_value - lower_value) * fraction)


class SummaryAccumulator:
    def __init__(
        self,
        cfg: LoadTestConfig,
        config_path: Path,
        started_at_utc: str,
        output_dir: Path,
    ) -> None:
        self._cfg = cfg
        self._config_path = config_path
        self._started_at_utc = started_at_utc
        self._output_dir = output_dir

        self.request_attempts = 0
        self.http_response_count = 0
        self.exception_count = 0
        self.status_code_counts: Counter[str] = Counter()
        self.latency_stats = LatencyStats()

    def record(self, result: RequestResult) -> None:
        self.request_attempts += 1
        if result.status_code is None:
            self.exception_count += 1
            return

        self.http_response_count += 1
        self.status_code_counts[str(result.status_code)] += 1
        self.latency_stats.add(result.elapsed_ms)

    def progress_snapshot(self, elapsed_seconds: float) -> dict[str, float | int]:
        return {
            "request_attempts": self.request_attempts,
            "http_response_count": self.http_response_count,
            "exception_count": self.exception_count,
            "requests_per_second": _rate(self.request_attempts, elapsed_seconds),
        }

    def build_summary(
        self,
        run_status: RunStatus,
        finished_at_utc: str,
        wall_time_seconds: float,
    ) -> RunSummary:
        http_total = self.http_response_count
        count_200 = self.status_code_counts.get("200", 0)
        count_4xx = sum(
            count for code, count in self.status_code_counts.items()
            if 400 <= int(code) < 500
        )
        count_5xx = sum(
            count for code, count in self.status_code_counts.items()
            if 500 <= int(code) < 600
        )
        other_count = http_total - count_200 - count_4xx - count_5xx

        return RunSummary(
            run_status=run_status,
            config_file=str(self._config_path),
            service_name=self._cfg.service_name,
            method=self._cfg.method,
            url=self._cfg.url,
            metadata=dict(self._cfg.metadata),
            duration_seconds=self._cfg.duration_seconds,
            wall_time_seconds=wall_time_seconds,
            parallel_users=self._cfg.parallel_users,
            ramp_up_seconds=self._cfg.ramp_up_seconds,
            request_attempts=self.request_attempts,
            requests_per_second=_rate(self.request_attempts, wall_time_seconds),
            http_response_count=self.http_response_count,
            exception_count=self.exception_count,
            status_code_counts=dict(self.status_code_counts),
            http_200_pct=_pct(count_200, http_total),
            http_4xx_pct=_pct(count_4xx, http_total),
            http_5xx_pct=_pct(count_5xx, http_total),
            other_http_pct=_pct(other_count, http_total),
            min_response_ms=self.latency_stats.min_ms,
            p50_response_ms=self.latency_stats.percentile(50),
            p90_response_ms=self.latency_stats.percentile(90),
            p95_response_ms=self.latency_stats.percentile(95),
            p99_response_ms=self.latency_stats.percentile(99),
            max_response_ms=self.latency_stats.max_ms,
            avg_response_ms=self.latency_stats.avg_ms,
            stdev_response_ms=self.latency_stats.stdev_ms,
            started_at_utc=self._started_at_utc,
            finished_at_utc=finished_at_utc,
            output_dir=str(self._output_dir),
        )


def _pct(part: int, total: int) -> float:
    if total == 0:
        return 0.0
    return (part / total) * 100.0


def _rate(count: int, elapsed_seconds: float) -> float:
    if elapsed_seconds <= 0:
        return 0.0
    return count / elapsed_seconds
