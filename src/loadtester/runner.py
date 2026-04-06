from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from .auth import fetch_bearer_headers
from .models import LoadTestConfig, RequestResult, RunSummary, SamplePayload
from .reporting import (
    ResultWriter,
    copy_input_config,
    create_run_output_dir,
    format_heartbeat,
    utc_now,
)
from .stats import SummaryAccumulator


def compute_start_delay(user_id: int, parallel_users: int, ramp_up_seconds: float) -> float:
    if parallel_users <= 1 or ramp_up_seconds <= 0:
        return 0.0
    return (user_id / (parallel_users - 1)) * ramp_up_seconds


class _WorkerTracker:
    def __init__(self, total_workers: int) -> None:
        self._remaining = total_workers
        self._lock = asyncio.Lock()
        self.done = asyncio.Event()
        if total_workers == 0:
            self.done.set()

    async def mark_done(self) -> None:
        async with self._lock:
            self._remaining -= 1
            if self._remaining == 0:
                self.done.set()


async def run_load_test(
    cfg: LoadTestConfig,
    config_path: Path,
    payloads: list[SamplePayload],
    output_root: Path,
    *,
    heartbeat_interval_seconds: float | None = 5.0,
    heartbeat_printer: Callable[[str], None] | None = None,
) -> RunSummary:
    auth_headers = await fetch_auth_headers(cfg)
    started_at = utc_now()
    started_monotonic = time.perf_counter()
    output_dir = create_run_output_dir(output_root.resolve(), cfg.service_name, started_at)
    copy_input_config(config_path.resolve(), output_dir)

    queue: asyncio.Queue[RequestResult] = asyncio.Queue(maxsize=max(100, cfg.parallel_users * 4))
    tracker = _WorkerTracker(cfg.parallel_users)
    counter = itertools.count(1)
    writer = ResultWriter(output_dir)
    writer.open()
    started_at_utc = _format_datetime(started_at)
    accumulator = SummaryAccumulator(cfg, config_path.resolve(), started_at_utc, output_dir)
    run_status = "completed"
    merged_headers = dict(cfg.headers)
    merged_headers.update(auth_headers)

    try:
        async with _create_service_client(cfg) as client:
            deadline = time.perf_counter() + cfg.duration_seconds
            try:
                async with asyncio.TaskGroup() as task_group:
                    task_group.create_task(_writer_task(queue, writer, accumulator, tracker.done))
                    if heartbeat_printer is not None and heartbeat_interval_seconds is not None and heartbeat_interval_seconds > 0:
                        task_group.create_task(
                            _heartbeat_task(
                                accumulator=accumulator,
                                workers_done=tracker.done,
                                started_monotonic=started_monotonic,
                                interval_seconds=heartbeat_interval_seconds,
                                printer=heartbeat_printer,
                            )
                        )
                    for user_id in range(cfg.parallel_users):
                        task_group.create_task(
                            _worker(
                                user_id=user_id,
                                cfg=cfg,
                                payloads=payloads,
                                client=client,
                                headers=merged_headers,
                                queue=queue,
                                counter=counter,
                                deadline=deadline,
                                tracker=tracker,
                            )
                        )
            except* Exception:
                run_status = "failed"
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None:
            task.uncancel()
        run_status = "cancelled"
    finally:
        writer.close()

    finished_at = utc_now()
    wall_time_seconds = time.perf_counter() - started_monotonic
    summary = accumulator.build_summary(
        run_status,
        _format_datetime(finished_at),
        wall_time_seconds,
    )
    writer.write_summary(summary)
    return summary


async def fetch_auth_headers(cfg: LoadTestConfig) -> dict[str, str]:
    if cfg.auth is None:
        return {}
    return await fetch_bearer_headers(
        cfg.auth,
        timeout_seconds=cfg.timeout_seconds,
        verify_tls=cfg.verify_tls,
    )


async def _worker(
    *,
    user_id: int,
    cfg: LoadTestConfig,
    payloads: list[SamplePayload],
    client: httpx.AsyncClient,
    headers: dict[str, str],
    queue: asyncio.Queue[RequestResult],
    counter: itertools.count,
    deadline: float,
    tracker: _WorkerTracker,
) -> None:
    payload_index = user_id % len(payloads)
    delay = compute_start_delay(user_id, cfg.parallel_users, cfg.ramp_up_seconds)
    try:
        if delay > 0:
            await asyncio.sleep(delay)

        while True:
            if time.perf_counter() >= deadline:
                return

            sample = payloads[payload_index]
            payload_index = (payload_index + 1) % len(payloads)
            request_number = next(counter)
            request_started_at = _format_datetime(utc_now(), include_milliseconds=True)
            monotonic_started = time.perf_counter()

            try:
                response = await client.request(
                    cfg.method,
                    cfg.url,
                    headers=headers,
                    json=sample.payload,
                )
                elapsed_ms = (time.perf_counter() - monotonic_started) * 1000.0
                result = RequestResult(
                    request_number=request_number,
                    user_id=user_id,
                    sample_file=sample.source_file.name,
                    method=cfg.method,
                    url=cfg.url,
                    started_at_utc=request_started_at,
                    elapsed_ms=elapsed_ms,
                    status_code=response.status_code,
                    response_bytes=len(response.content),
                    error_type=None,
                    error_message=None,
                )
            except asyncio.CancelledError:
                elapsed_ms = (time.perf_counter() - monotonic_started) * 1000.0
                _try_queue_cancelled_result(
                    queue,
                    RequestResult(
                        request_number=request_number,
                        user_id=user_id,
                        sample_file=sample.source_file.name,
                        method=cfg.method,
                        url=cfg.url,
                        started_at_utc=request_started_at,
                        elapsed_ms=elapsed_ms,
                        status_code=None,
                        response_bytes=None,
                        error_type="CancelledError",
                        error_message="request cancelled",
                    ),
                )
                raise
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - monotonic_started) * 1000.0
                result = RequestResult(
                    request_number=request_number,
                    user_id=user_id,
                    sample_file=sample.source_file.name,
                    method=cfg.method,
                    url=cfg.url,
                    started_at_utc=request_started_at,
                    elapsed_ms=elapsed_ms,
                    status_code=None,
                    response_bytes=None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

            await queue.put(result)
    finally:
        await tracker.mark_done()


async def _writer_task(
    queue: asyncio.Queue[RequestResult],
    writer: ResultWriter,
    accumulator: SummaryAccumulator,
    workers_done: asyncio.Event,
) -> None:
    current_result: RequestResult | None = None
    try:
        while True:
            if workers_done.is_set() and queue.empty():
                return
            try:
                current_result = await asyncio.wait_for(queue.get(), timeout=0.1)
            except TimeoutError:
                continue
            accumulator.record(current_result)
            writer.write_result(current_result)
            current_result = None
    finally:
        if current_result is not None:
            accumulator.record(current_result)
            writer.write_result(current_result)

        while True:
            try:
                pending_result = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            accumulator.record(pending_result)
            writer.write_result(pending_result)

        writer.close()


async def _heartbeat_task(
    *,
    accumulator: SummaryAccumulator,
    workers_done: asyncio.Event,
    started_monotonic: float,
    interval_seconds: float,
    printer: Callable[[str], None],
) -> None:
    while True:
        try:
            await asyncio.wait_for(workers_done.wait(), timeout=interval_seconds)
            return
        except TimeoutError:
            elapsed_seconds = time.perf_counter() - started_monotonic
            snapshot = accumulator.progress_snapshot(elapsed_seconds)
            printer(
                format_heartbeat(
                    elapsed_seconds=elapsed_seconds,
                    request_attempts=int(snapshot["request_attempts"]),
                    http_response_count=int(snapshot["http_response_count"]),
                    exception_count=int(snapshot["exception_count"]),
                    requests_per_second=float(snapshot["requests_per_second"]),
                )
            )


def _try_queue_cancelled_result(queue: asyncio.Queue[RequestResult], result: RequestResult) -> None:
    try:
        queue.put_nowait(result)
    except asyncio.QueueFull:
        pass


def _create_service_client(cfg: LoadTestConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(cfg.timeout_seconds),
        verify=cfg.verify_tls,
        http2=False,
        limits=httpx.Limits(
            max_connections=cfg.parallel_users,
            max_keepalive_connections=cfg.parallel_users,
        ),
    )


def _format_datetime(value: datetime, *, include_milliseconds: bool = False) -> str:
    timespec = "milliseconds" if include_milliseconds else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")
