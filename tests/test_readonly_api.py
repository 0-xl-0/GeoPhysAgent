from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from geophysagent.readonly_api import (
    POLICY_MODE_ENV,
    POLICY_ORIGINS_ENV,
    READ_ONLY_NETWORK_MODE,
    ReadOnlyApiClient,
    ReadOnlyApiError,
    normalize_http_origin,
)


class _ApiHandler(BaseHTTPRequestHandler):
    token = ""

    def do_GET(self) -> None:
        type(self).token = self.headers.get("Authorization", "")
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/data")
            self.end_headers()
            return
        if self.path != "/data?project=XX":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"project": "XX", "count": 2}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class ReadOnlyApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_get_json_enforces_origin_redirect_size_and_credentials(self) -> None:
        with mock.patch.dict(os.environ, {"SOFTWARE_A_API_TOKEN": "api-secret"}, clear=False):
            client = ReadOnlyApiClient(
                (self.origin,), credential_env="SOFTWARE_A_API_TOKEN", max_response_bytes=1024
            )
            response = client.get_json(f"{self.origin}/data", query={"project": "XX"})
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(_ApiHandler.token, "Bearer api-secret")
        with self.assertRaises(ReadOnlyApiError):
            client.get_json("https://example.invalid/data")
        with self.assertRaises(ReadOnlyApiError):
            client.get_json(f"{self.origin}/redirect")

    def test_environment_policy_and_origin_rules_fail_closed(self) -> None:
        policy = {
            POLICY_MODE_ENV: READ_ONLY_NETWORK_MODE,
            POLICY_ORIGINS_ENV: json.dumps([self.origin]),
        }
        with mock.patch.dict(os.environ, policy, clear=False):
            client = ReadOnlyApiClient.from_environment()
            self.assertEqual(client.allowed_origins, (self.origin,))
        with mock.patch.dict(os.environ, {POLICY_MODE_ENV: "unrestricted"}, clear=True):
            with self.assertRaises(ReadOnlyApiError):
                ReadOnlyApiClient.from_environment()
        with self.assertRaises(ReadOnlyApiError):
            normalize_http_origin("http://example.com")
        with self.assertRaises(ReadOnlyApiError):
            normalize_http_origin("https://example.com/private/path")


if __name__ == "__main__":
    unittest.main()
