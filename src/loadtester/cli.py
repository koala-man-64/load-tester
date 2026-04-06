from __future__ import annotations

import argparse
import asyncio
import builtins
import sys
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import TypeVar

from .config import ConfigError, discover_configs, load_config, preload_payloads
from .models import DiscoveredConfig, LoadTestConfig
from .reporting import format_selected_config, format_summary
from .runner import fetch_auth_headers, run_load_test

T = TypeVar("T")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    validation_error = _validate_args(args)
    if validation_error is not None:
        print(f"Error: {validation_error}", file=sys.stderr)
        return 1

    config_dir = Path(args.config_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    config_path = Path(args.config).resolve() if args.config else None

    try:
        selected, discovered = _resolve_selection(
            config_dir=config_dir,
            config_path=config_path,
            index=args.index,
            list_only=args.list,
        )
    except (ConfigError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.list:
        return 0 if discovered else 1

    try:
        if selected is None:
            raise ValueError("no config selected")
        payloads = preload_payloads(selected.config)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print()
    print(format_selected_config(selected.config))
    if not args.validate_only:
        print(f"Output root: {output_dir}")
    print()

    if args.validate_only:
        try:
            auth_enabled = _run_async(_validate_selected_config(selected.config))
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print("Validation status: passed")
        print(f"Auth preflight:    {'passed' if auth_enabled else 'skipped'}")
        return 0

    try:
        summary, cancelled = _run_async_with_cancel(
            run_load_test(
                selected.config,
                selected.path,
                payloads,
                output_dir,
                heartbeat_printer=print,
            )
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if summary is not None:
        print(format_summary(summary))
        if summary.run_status == "failed":
            return 1

    if cancelled:
        return 130
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local API load test")
    parser.add_argument(
        "--config",
        help="Run a specific config file directly.",
    )
    parser.add_argument(
        "--config-dir",
        default="./configs",
        help="Directory containing top-level JSON config files.",
    )
    parser.add_argument(
        "--index",
        type=int,
        help="0-based config index to run without prompting.",
    )
    parser.add_argument(
        "--output-dir",
        default="./runs",
        help="Directory where run outputs will be written.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List discovered configs and exit.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the selected config, payloads, and auth preflight without running the load test.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> str | None:
    if args.config and args.index is not None:
        return "--index cannot be used with --config"
    if args.config and args.list:
        return "--list cannot be used with --config"
    if args.list and args.validate_only:
        return "--validate-only cannot be used with --list"
    return None


def _resolve_selection(
    *,
    config_dir: Path,
    config_path: Path | None,
    index: int | None,
    list_only: bool,
) -> tuple[DiscoveredConfig | None, list[DiscoveredConfig]]:
    if config_path is not None:
        loaded = DiscoveredConfig(path=config_path, config=load_config(config_path))
        return loaded, [loaded]

    discovered, skipped = discover_configs(config_dir)
    _print_discovery(config_dir, discovered, skipped)
    if not discovered:
        raise ValueError("No valid configs found.")
    if list_only:
        return None, discovered
    return _select_config(discovered, index), discovered


def _print_discovery(
    config_dir: Path,
    discovered: list[DiscoveredConfig],
    skipped: list[tuple[Path, str]],
) -> None:
    print(f"Configs found in {config_dir}")
    print()
    for index, item in enumerate(discovered):
        print(f"[{index}] {item.path.name}   {item.config.service_name}")
    if skipped:
        print()
        print("Skipped:")
        for path, reason in skipped:
            print(f"- {path.name}: {reason}")


def _select_config(discovered: list[DiscoveredConfig], index: int | None) -> DiscoveredConfig:
    if index is not None:
        if 0 <= index < len(discovered):
            return discovered[index]
        raise ValueError(f"index {index} is out of range")

    while True:
        response = builtins.input("Select config index: ").strip()
        if not response:
            print("Enter a numeric index.")
            continue
        try:
            candidate = int(response)
        except ValueError:
            print("Enter a numeric index.")
            continue
        if 0 <= candidate < len(discovered):
            return discovered[candidate]
        print("Index out of range.")


async def _validate_selected_config(cfg: LoadTestConfig) -> bool:
    if cfg.auth is None:
        return False
    await fetch_auth_headers(cfg)
    return True


def _run_async(coro: Coroutine[object, object, T]) -> T:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(coro)
    try:
        return loop.run_until_complete(task)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _run_async_with_cancel(coro: Coroutine[object, object, T]) -> tuple[T | None, bool]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(coro)
    cancelled = False
    result: T | None = None
    try:
        result = loop.run_until_complete(task)
    except KeyboardInterrupt:
        cancelled = True
        if not task.done():
            task.cancel()
            try:
                result = loop.run_until_complete(task)
            except asyncio.CancelledError:
                result = None
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    return result, cancelled
