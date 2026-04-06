from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .models import LoadTestConfig, RequestResult, RunSummary

DETAILS_COLUMNS = [
    "request_number",
    "user_id",
    "sample_file",
    "method",
    "url",
    "started_at_utc",
    "elapsed_ms",
    "status_code",
    "response_bytes",
    "error_type",
    "error_message",
]


class ResultWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.details_path = output_dir / "details.csv"
        self.summary_path = output_dir / "summary.json"
        self._file_handle = None
        self._writer: csv.DictWriter[str] | None = None
        self._rows_since_flush = 0

    def open(self) -> None:
        self._file_handle = self.details_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file_handle, fieldnames=DETAILS_COLUMNS)
        self._writer.writeheader()
        self._file_handle.flush()

    def write_result(self, result: RequestResult) -> None:
        if self._writer is None:
            raise RuntimeError("writer is not open")
        self._writer.writerow(result.to_csv_row())
        self._rows_since_flush += 1
        if self._rows_since_flush >= 100:
            self.flush()

    def flush(self) -> None:
        if self._file_handle is not None:
            self._file_handle.flush()
            self._rows_since_flush = 0

    def close(self) -> None:
        if self._file_handle is None:
            return
        try:
            self.flush()
        finally:
            self._file_handle.close()
            self._file_handle = None
            self._writer = None

    def write_summary(self, summary: RunSummary) -> None:
        with self.summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary.to_dict(), handle, indent=2)
            handle.write("\n")


def create_run_output_dir(output_root: Path, service_name: str, started_at: datetime) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = started_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = output_root / f"{timestamp}_{slugify_service_name(service_name)}"
    run_dir.mkdir(parents=False, exist_ok=False)
    return run_dir


def copy_input_config(config_path: Path, output_dir: Path) -> Path:
    destination = output_dir / "input_config.json"
    shutil.copyfile(config_path, destination)
    return destination


def slugify_service_name(service_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", service_name.strip().lower())
    slug = slug.strip("-")
    return slug or "run"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_selected_config(cfg: LoadTestConfig) -> str:
    auth_state = "enabled" if cfg.auth else "disabled"
    metadata_state = "present" if cfg.metadata else "none"
    return "\n".join(
        [
            f"Selected: {cfg.service_name}",
            f"Method:   {cfg.method}",
            f"URL:      {cfg.url}",
            f"Users:    {cfg.parallel_users}",
            f"Duration: {cfg.duration_seconds:g}s",
            f"Ramp-up:  {cfg.ramp_up_seconds:g}s",
            f"Payloads: {len(cfg.sample_request_files)}",
            f"Auth:     {auth_state}",
            f"Metadata: {metadata_state}",
        ]
    )


def format_summary(summary: RunSummary) -> str:
    details_path = Path(summary.output_dir) / "details.csv"
    summary_path = Path(summary.output_dir) / "summary.json"
    lines = [
        f"Run status: {summary.run_status}",
        f"Requests attempted: {summary.request_attempts}",
        f"Requests/sec:       {summary.requests_per_second:.2f}",
        f"HTTP responses:     {summary.http_response_count}",
        f"Exceptions:         {summary.exception_count}",
        f"Wall time:          {summary.wall_time_seconds:.2f}s",
        f"200:                {summary.http_200_pct:.2f}%",
        f"4xx:                {summary.http_4xx_pct:.2f}%",
        f"5xx:                {summary.http_5xx_pct:.2f}%",
        f"Other HTTP:         {summary.other_http_pct:.2f}%",
        "Latency ms:",
        f"  min: {_format_latency(summary.min_response_ms)}",
        f"  p50: {_format_latency(summary.p50_response_ms)}",
        f"  p90: {_format_latency(summary.p90_response_ms)}",
        f"  p95: {_format_latency(summary.p95_response_ms)}",
        f"  p99: {_format_latency(summary.p99_response_ms)}",
        f"  avg: {_format_latency(summary.avg_response_ms)}",
        f"  max: {_format_latency(summary.max_response_ms)}",
        f"  stdev: {_format_latency(summary.stdev_response_ms)}",
    ]
    if summary.metadata:
        lines.extend(
            [
                "",
                f"Metadata: {json.dumps(summary.metadata, sort_keys=True)}",
            ]
        )
    lines.extend(
        [
        "",
        f"Details: {details_path}",
        f"Summary: {summary_path}",
        ]
    )
    return "\n".join(lines)


def _format_latency(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def format_heartbeat(
    *,
    elapsed_seconds: float,
    request_attempts: int,
    http_response_count: int,
    exception_count: int,
    requests_per_second: float,
) -> str:
    return (
        f"[progress {elapsed_seconds:.1f}s] "
        f"attempts={request_attempts} "
        f"responses={http_response_count} "
        f"exceptions={exception_count} "
        f"rps={requests_per_second:.2f}"
    )
