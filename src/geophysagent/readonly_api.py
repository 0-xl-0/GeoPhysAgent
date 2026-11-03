from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


POLICY_ORIGINS_ENV = "GEOPHYSAGENT_ALLOWED_HTTP_ORIGINS"
POLICY_MODE_ENV = "GEOPHYSAGENT_NETWORK_MODE"
READ_ONLY_NETWORK_MODE = "read-only"
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class ReadOnlyApiError(RuntimeError):
    """A read-only API request violated policy or returned unusable JSON."""


@dataclass(frozen=True)
class ReadOnlyApiResponse:
    status: int
    content_type: str
    data: Any
    size_bytes: int


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class ReadOnlyApiClient:
    """Small Host-policy-aware JSON client for reviewed local MCP connectors.

    This helper constrains well-behaved integration code. It is not an OS network
    sandbox and cannot contain a deliberately malicious MCP Server.
    """

    def __init__(
        self,
        allowed_origins: tuple[str, ...],
        *,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        credential_env: str | None = None,
        credential_header: str = "Authorization",
        credential_prefix: str = "Bearer ",
    ) -> None:
        if not allowed_origins:
            raise ReadOnlyApiError("At least one API origin must be authorized")
        self.allowed_origins = tuple(normalize_http_origin(item) for item in allowed_origins)
        if len(self.allowed_origins) != len(set(self.allowed_origins)):
            raise ReadOnlyApiError("Authorized API origins must be unique")
        if not 0 < timeout_seconds <= 300:
            raise ReadOnlyApiError("API timeout must be between 0 and 300 seconds")
        if not 0 < max_response_bytes <= 16 * 1024 * 1024:
            raise ReadOnlyApiError("API response limit must be between 1 byte and 16 MiB")
        if not _HEADER_NAME.fullmatch(credential_header):
            raise ReadOnlyApiError("Credential header name is invalid")
        if "\r" in credential_prefix or "\n" in credential_prefix:
            raise ReadOnlyApiError("Credential prefix contains invalid characters")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.credential_env = credential_env
        self.credential_header = credential_header
        self.credential_prefix = credential_prefix
        self._opener = urllib.request.build_opener(_NoRedirect())

    @classmethod
    def from_environment(
        cls,
        *,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        credential_env: str | None = None,
        credential_header: str = "Authorization",
        credential_prefix: str = "Bearer ",
    ) -> "ReadOnlyApiClient":
        if os.environ.get(POLICY_MODE_ENV) != READ_ONLY_NETWORK_MODE:
            raise ReadOnlyApiError("GeoPhysAgent did not authorize read-only network access")
        raw_origins = os.environ.get(POLICY_ORIGINS_ENV, "")
        try:
            parsed = json.loads(raw_origins)
        except json.JSONDecodeError as exc:
            raise ReadOnlyApiError("Authorized API origins are missing or invalid") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ReadOnlyApiError("Authorized API origins must be a string array")
        return cls(
            tuple(parsed),
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            credential_env=credential_env,
            credential_header=credential_header,
            credential_prefix=credential_prefix,
        )

    def get_json(
        self,
        url: str,
        *,
        query: dict[str, str | int | float | bool] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ReadOnlyApiResponse:
        safe_url = self._authorized_url(url, query)
        request_headers = {"Accept": "application/json", "User-Agent": "GeoPhysAgent/0.1"}
        for name, value in (headers or {}).items():
            if not _HEADER_NAME.fullmatch(name) or "\r" in value or "\n" in value:
                raise ReadOnlyApiError("API request contains an invalid header")
            if name.lower() in {"host", "content-length", "transfer-encoding"}:
                raise ReadOnlyApiError(f"API request cannot override header: {name}")
            request_headers[name] = value
        if self.credential_env is not None:
            secret = os.environ.get(self.credential_env, "").strip()
            if not secret:
                raise ReadOnlyApiError(
                    f"Required API credential environment variable is not configured: "
                    f"{self.credential_env}"
                )
            request_headers[self.credential_header] = self.credential_prefix + secret
        request = urllib.request.Request(safe_url, headers=request_headers, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                content_type = str(response.headers.get_content_type()).lower()
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > self.max_response_bytes:
                    raise ReadOnlyApiError("API response exceeds the configured size limit")
                body = response.read(self.max_response_bytes + 1)
        except ReadOnlyApiError:
            raise
        except urllib.error.HTTPError as exc:
            raise ReadOnlyApiError(f"API returned HTTP status {exc.code}") from exc
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise ReadOnlyApiError(f"API request failed: {type(exc).__name__}") from exc
        if not 200 <= status < 300:
            raise ReadOnlyApiError(f"API returned HTTP status {status}")
        if len(body) > self.max_response_bytes:
            raise ReadOnlyApiError("API response exceeds the configured size limit")
        if content_type != "application/json" and not content_type.endswith("+json"):
            raise ReadOnlyApiError("API response is not JSON")
        try:
            data = json.loads(
                body.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON number: {value}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ReadOnlyApiError("API response contains invalid JSON") from exc
        return ReadOnlyApiResponse(status, content_type, data, len(body))

    def _authorized_url(
        self,
        url: str,
        query: dict[str, str | int | float | bool] | None,
    ) -> str:
        if not isinstance(url, str) or len(url) > 4096:
            raise ReadOnlyApiError("API URL is invalid or too long")
        try:
            parsed = urllib.parse.urlsplit(url)
            origin = normalize_http_origin(
                urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            )
        except (ValueError, UnicodeError) as exc:
            raise ReadOnlyApiError("API URL is invalid") from exc
        if origin not in self.allowed_origins:
            raise ReadOnlyApiError("API URL origin is not authorized")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ReadOnlyApiError("API URL cannot contain credentials or a fragment")
        existing_query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if query:
            existing_query.extend((str(name), str(value)) for name, value in query.items())
        encoded_query = urllib.parse.urlencode(existing_query)
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path or "/", encoded_query, "")
        )


def normalize_http_origin(value: str) -> str:
    """Return a strict canonical origin suitable for manifest allowlists."""

    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ReadOnlyApiError("API origin must be a bounded string")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise ReadOnlyApiError("API origin is invalid") from exc
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        raise ReadOnlyApiError("API origin must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ReadOnlyApiError("API origin cannot contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ReadOnlyApiError("API origin cannot contain a path, query or fragment")
    if not host.isascii() or any(char.isspace() for char in host):
        raise ReadOnlyApiError("API origin host is invalid")
    if scheme == "http" and not _is_loopback_host(host):
        raise ReadOnlyApiError("Plain HTTP API origins are restricted to loopback")
    default_port = 443 if scheme == "https" else 80
    port = port or default_port
    if not 1 <= port <= 65535:
        raise ReadOnlyApiError("API origin port is invalid")
    display_host = f"[{host}]" if ":" in host else host
    suffix = "" if port == default_port else f":{port}"
    return f"{scheme}://{display_host}{suffix}"


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
