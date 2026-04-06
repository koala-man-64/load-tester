from __future__ import annotations

from typing import Any

import httpx

from .models import AuthConfig


class AuthError(RuntimeError):
    """Raised when auth preflight fails."""


def extract_token_value(document: Any, token_json_path: str) -> str:
    current = document
    for segment in token_json_path.split("."):
        if not segment:
            raise AuthError("token_json_path contains an empty segment")
        if not isinstance(current, dict) or segment not in current:
            raise AuthError(f"token_json_path segment not found: {segment}")
        current = current[segment]

    if isinstance(current, (dict, list)):
        raise AuthError("token value must be a scalar")

    token = str(current)
    if not token:
        raise AuthError("token value is empty")
    return token


async def fetch_bearer_headers(
    auth_cfg: AuthConfig,
    timeout_seconds: float,
    verify_tls: bool,
) -> dict[str, str]:
    request_kwargs = _build_auth_request_kwargs(auth_cfg)
    try:
        async with _create_auth_client(timeout_seconds, verify_tls) as client:
            response = await client.request(**request_kwargs)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AuthError(f"auth request failed with HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise AuthError(f"auth request failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AuthError("auth response was not valid JSON") from exc

    token = extract_token_value(payload, auth_cfg.token_json_path)
    return {
        auth_cfg.header_name: auth_cfg.header_template.format(token=token),
    }


def _build_auth_request_kwargs(auth_cfg: AuthConfig) -> dict[str, Any]:
    request_kwargs: dict[str, Any] = {
        "method": auth_cfg.method,
        "url": auth_cfg.token_url,
        "headers": dict(auth_cfg.headers),
    }
    if auth_cfg.body:
        if auth_cfg.body_mode == "json":
            request_kwargs["json"] = auth_cfg.body
        else:
            request_kwargs["data"] = auth_cfg.body
    return request_kwargs


def _create_auth_client(timeout_seconds: float, verify_tls: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        verify=verify_tls,
        http2=False,
    )
