from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import AuthConfig, DiscoveredConfig, LoadTestConfig, SamplePayload

ALLOWED_METHODS = {"POST", "PUT", "PATCH"}
ALLOWED_AUTH_BODY_MODES = {"json", "form"}


class ConfigError(ValueError):
    """Raised for invalid user config."""


def discover_configs(config_dir: Path) -> tuple[list[DiscoveredConfig], list[tuple[Path, str]]]:
    resolved_dir = config_dir.resolve()
    if not resolved_dir.exists():
        raise ConfigError(f"config directory does not exist: {resolved_dir}")
    if not resolved_dir.is_dir():
        raise ConfigError(f"config path is not a directory: {resolved_dir}")

    discovered: list[DiscoveredConfig] = []
    skipped: list[tuple[Path, str]] = []
    for path in sorted(resolved_dir.glob("*.json")):
        try:
            discovered.append(DiscoveredConfig(path=path.resolve(), config=load_config(path)))
        except ConfigError as exc:
            skipped.append((path.resolve(), str(exc)))
    return discovered, skipped


def load_config(config_path: Path) -> LoadTestConfig:
    resolved_path = config_path.resolve()
    raw = _read_json_file(resolved_path, label="config")
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a JSON object")

    service_name = _require_non_empty_string(raw, "service_name")
    method = _normalize_method(raw.get("method", "POST"), field_name="method")
    url = _require_non_empty_string(raw, "url")
    headers = _validate_headers(raw.get("headers", {}), field_name="headers")
    metadata = _validate_metadata(raw.get("metadata", {}))
    auth = _validate_auth(raw.get("auth"))
    sample_request_files = _resolve_sample_paths(raw.get("sample_request_files"), resolved_path.parent)
    duration_seconds = _require_positive_number(raw, "duration_seconds")
    parallel_users = _require_positive_int(raw, "parallel_users")
    ramp_up_seconds = _require_non_negative_number(raw, "ramp_up_seconds")
    if ramp_up_seconds > duration_seconds:
        raise ConfigError("ramp_up_seconds must be less than or equal to duration_seconds")
    timeout_seconds = _optional_positive_number(raw.get("timeout_seconds", 30), "timeout_seconds")
    verify_tls = _optional_bool(raw.get("verify_tls", True), "verify_tls")

    return LoadTestConfig(
        service_name=service_name,
        method=method,
        url=url,
        headers=headers,
        metadata=metadata,
        auth=auth,
        sample_request_files=sample_request_files,
        duration_seconds=duration_seconds,
        parallel_users=parallel_users,
        ramp_up_seconds=ramp_up_seconds,
        timeout_seconds=timeout_seconds,
        verify_tls=verify_tls,
    )


def preload_payloads(cfg: LoadTestConfig) -> list[SamplePayload]:
    payloads: list[SamplePayload] = []
    for sample_path in cfg.sample_request_files:
        if not sample_path.exists():
            raise ConfigError(f"sample_request_file does not exist: {sample_path}")
        if not sample_path.is_file():
            raise ConfigError(f"sample_request_file is not a file: {sample_path}")
        try:
            with sample_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid sample JSON in {sample_path.name}: {exc.msg}") from exc
        payloads.append(SamplePayload(source_file=sample_path, payload=payload))

    if not payloads:
        raise ConfigError("sample_request_files must contain at least one file")
    return payloads


def _read_json_file(path: Path, *, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"{label} file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path.name}: {exc.msg}") from exc


def _require_non_empty_string(raw: dict[str, Any], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"missing {field_name}")
    return value.strip()


def _normalize_method(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"missing {field_name}")
    method = value.strip().upper()
    if method not in ALLOWED_METHODS:
        allowed = ", ".join(sorted(ALLOWED_METHODS))
        raise ConfigError(f"{field_name} must be one of: {allowed}")
    return method


def _validate_headers(value: Any, *, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be an object")
    headers: dict[str, str] = {}
    for key, header_value in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(f"{field_name} keys must be non-empty strings")
        if not isinstance(header_value, str):
            raise ConfigError(f"{field_name} values must be strings")
        headers[key] = header_value
    return headers


def _validate_auth(value: Any) -> AuthConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError("auth must be an object")

    token_url = _require_non_empty_string(value, "token_url")
    token_json_path = _require_non_empty_string(value, "token_json_path")
    method = _normalize_method(value.get("method", "POST"), field_name="auth.method")
    body_mode_raw = value.get("body_mode", "json")
    if not isinstance(body_mode_raw, str) or not body_mode_raw.strip():
        raise ConfigError("auth.body_mode must be a non-empty string")
    body_mode = body_mode_raw.strip().lower()
    if body_mode not in ALLOWED_AUTH_BODY_MODES:
        allowed = ", ".join(sorted(ALLOWED_AUTH_BODY_MODES))
        raise ConfigError(f"auth.body_mode must be one of: {allowed}")
    headers = _validate_headers(value.get("headers", {}), field_name="auth.headers")
    body = value.get("body")
    if body is not None and not isinstance(body, dict):
        raise ConfigError("auth.body must be an object")
    header_name = value.get("header_name", "Authorization")
    if not isinstance(header_name, str) or not header_name.strip():
        raise ConfigError("auth.header_name must be a non-empty string")
    header_template = value.get("header_template", "Bearer {token}")
    if not isinstance(header_template, str) or "{token}" not in header_template:
        raise ConfigError("auth.header_template must be a string containing {token}")

    return AuthConfig(
        token_url=token_url,
        token_json_path=token_json_path,
        method=method,
        body_mode=body_mode,
        headers=headers,
        body=body,
        header_name=header_name.strip(),
        header_template=header_template,
    )


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError("metadata must be an object")
    metadata: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("metadata keys must be non-empty strings")
        metadata[key] = item
    return metadata


def _resolve_sample_paths(value: Any, base_dir: Path) -> list[Path]:
    if not isinstance(value, list) or not value:
        raise ConfigError("sample_request_files must be a non-empty array")
    resolved_paths: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("sample_request_files entries must be non-empty strings")
        resolved_paths.append((base_dir / item).resolve())
    return resolved_paths


def _require_positive_number(raw: dict[str, Any], field_name: str) -> float:
    value = raw.get(field_name)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{field_name} must be greater than 0")
    return float(value)


def _optional_positive_number(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{field_name} must be greater than 0")
    return float(value)


def _require_positive_int(raw: dict[str, Any], field_name: str) -> int:
    value = raw.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{field_name} must be a positive integer")
    return value


def _require_non_negative_number(raw: dict[str, Any], field_name: str) -> float:
    value = raw.get(field_name)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"{field_name} must be greater than or equal to 0")
    return float(value)


def _optional_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a boolean")
    return value
