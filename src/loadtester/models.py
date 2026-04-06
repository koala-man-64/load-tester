from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

RunStatus = Literal["completed", "cancelled", "failed"]


@dataclass(slots=True)
class AuthConfig:
    token_url: str
    token_json_path: str
    method: str = "POST"
    body_mode: str = "json"
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    header_name: str = "Authorization"
    header_template: str = "Bearer {token}"


@dataclass(slots=True)
class LoadTestConfig:
    service_name: str
    method: str
    url: str
    headers: dict[str, str]
    metadata: dict[str, Any]
    auth: AuthConfig | None
    sample_request_files: list[Path]
    duration_seconds: float
    parallel_users: int
    ramp_up_seconds: float
    timeout_seconds: float
    verify_tls: bool


@dataclass(slots=True)
class DiscoveredConfig:
    path: Path
    config: LoadTestConfig


@dataclass(slots=True)
class SamplePayload:
    source_file: Path
    payload: Any


@dataclass(slots=True)
class RequestResult:
    request_number: int
    user_id: int
    sample_file: str
    method: str
    url: str
    started_at_utc: str
    elapsed_ms: float
    status_code: int | None
    response_bytes: int | None
    error_type: str | None
    error_message: str | None

    def to_csv_row(self) -> dict[str, Any]:
        row = asdict(self)
        return {
            key: "" if value is None else value
            for key, value in row.items()
        }


@dataclass(slots=True)
class RunSummary:
    run_status: RunStatus
    config_file: str
    service_name: str
    method: str
    url: str
    metadata: dict[str, Any]
    duration_seconds: float
    wall_time_seconds: float
    parallel_users: int
    ramp_up_seconds: float
    request_attempts: int
    requests_per_second: float
    http_response_count: int
    exception_count: int
    status_code_counts: dict[str, int]
    http_200_pct: float
    http_4xx_pct: float
    http_5xx_pct: float
    other_http_pct: float
    min_response_ms: float | None
    p50_response_ms: float | None
    p90_response_ms: float | None
    p95_response_ms: float | None
    p99_response_ms: float | None
    max_response_ms: float | None
    avg_response_ms: float | None
    stdev_response_ms: float | None
    started_at_utc: str
    finished_at_utc: str
    output_dir: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
