from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from loadtester.auth import AuthError, extract_token_value, fetch_bearer_headers
from loadtester.models import AuthConfig


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_token_value_supports_dotted_paths(self) -> None:
        payload = {"data": {"token": "abc123"}}
        self.assertEqual(extract_token_value(payload, "data.token"), "abc123")

    def test_extract_token_value_rejects_missing_segment(self) -> None:
        with self.assertRaises(AuthError):
            extract_token_value({"data": {}}, "data.token")

    async def test_fetch_bearer_headers_supports_json_body(self) -> None:
        recorded: dict[str, str] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            recorded["content_type"] = request.headers["Content-Type"]
            recorded["body"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "token-123"})

        client = httpx.AsyncClient(transport=RecordingTransport(handler))
        auth_cfg = AuthConfig(
            token_url="https://auth.test/token",
            token_json_path="access_token",
            headers={"Content-Type": "application/json"},
            body={"client_id": "abc"},
        )

        with patch("loadtester.auth._create_auth_client", return_value=client):
            headers = await fetch_bearer_headers(auth_cfg, timeout_seconds=5, verify_tls=False)

        self.assertEqual(headers, {"Authorization": "Bearer token-123"})
        self.assertEqual(recorded["content_type"], "application/json")
        self.assertEqual(json.loads(recorded["body"]), {"client_id": "abc"})

    async def test_fetch_bearer_headers_supports_form_body(self) -> None:
        recorded: dict[str, str] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            recorded["content_type"] = request.headers["Content-Type"]
            recorded["body"] = request.content.decode()
            return httpx.Response(200, json={"data": {"token": "form-token"}})

        client = httpx.AsyncClient(transport=RecordingTransport(handler))
        auth_cfg = AuthConfig(
            token_url="https://auth.test/token",
            token_json_path="data.token",
            body_mode="form",
            body={"client_id": "abc", "client_secret": "def"},
        )

        with patch("loadtester.auth._create_auth_client", return_value=client):
            headers = await fetch_bearer_headers(auth_cfg, timeout_seconds=5, verify_tls=False)

        self.assertEqual(headers, {"Authorization": "Bearer form-token"})
        self.assertIn("application/x-www-form-urlencoded", recorded["content_type"])
        self.assertIn("client_id=abc", recorded["body"])
        self.assertIn("client_secret=def", recorded["body"])
