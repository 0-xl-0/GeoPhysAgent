from __future__ import annotations

import hashlib
import html
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from geophysagent.config import ModelProfile


MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ModelClientError(RuntimeError):
    """A safe, credential-free model client failure."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "model_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass(frozen=True)
class ModelResponse:
    content: tuple[dict[str, Any], ...]
    stop_reason: str | None
    model: str
    response_id: str | None = None
    usage: dict[str, int] | None = None
    latency_ms: int = 0


class ModelClient(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def protocol(self) -> str: ...

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelResponse: ...


class AnthropicCompatibleClient:
    """Minimal Anthropic Messages compatible client for the v0.1 agent loop."""

    def __init__(self, profile: ModelProfile, api_key: str):
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._profile = profile
        self._api_key = api_key
        self._session_id = str(uuid.uuid4())

    def bind_session(self, session_id: str) -> None:
        self._session_id = _validated_session_id(session_id)

    @property
    def provider(self) -> str:
        return self._profile.provider

    @property
    def model(self) -> str:
        return self._profile.model

    @property
    def protocol(self) -> str:
        return self._profile.protocol

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelResponse:
        started = time.monotonic()
        payload = {
            "model": self._profile.model,
            "max_tokens": self._profile.max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            _model_endpoint(self._profile.base_url, "messages"),
            data=encoded,
            method="POST",
            headers=_with_provider_headers(
                self._profile,
                self._session_id,
                {
                    "content-type": "application/json",
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "user-agent": "GeoPhysAgent/0.1",
                },
            ),
        )
        try:
            with urlopen(request, timeout=self._profile.timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ModelClientError("Model API response exceeds the 8 MiB safety limit")
        except HTTPError as exc:
            detail = _safe_http_detail(exc).replace(self._api_key, "[REDACTED]")
            category = (
                "credentials"
                if exc.code in {401, 403}
                else "rate_limited"
                if exc.code == 429
                else "server_error"
                if exc.code >= 500
                else "http_error"
            )
            raise ModelClientError(
                f"Model API returned HTTP {exc.code}: {detail}",
                category=category,
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except URLError as exc:
            reason = str(exc.reason)[:300]
            raise ModelClientError(
                f"Cannot connect to model API: {reason}",
                category="network",
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ModelClientError(
                "Model API request timed out",
                category="timeout",
                retryable=True,
            ) from exc

        try:
            result = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelClientError("Model API returned invalid JSON", category="protocol") from exc
        if not isinstance(result, dict) or not isinstance(result.get("content"), list):
            raise ModelClientError(
                "Model API response has no valid content list", category="protocol"
            )
        content = tuple(block for block in result["content"] if isinstance(block, dict))
        content = _normalize_textual_tool_calls(content, tools)
        if not content:
            raise ModelClientError("Model API returned empty content", category="protocol")
        usage_payload = result.get("usage")
        usage = None
        if isinstance(usage_payload, dict):
            usage = {
                name: int(value)
                for name, value in usage_payload.items()
                if isinstance(name, str) and isinstance(value, int) and value >= 0
            }
        return ModelResponse(
            content=content,
            stop_reason=result.get("stop_reason"),
            model=str(result.get("model", self._profile.model)),
            response_id=str(result["id"]) if result.get("id") else None,
            usage=usage,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )

    def stream_complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        """Consume Anthropic-compatible SSE while reconstructing one safe response."""
        started = time.monotonic()
        payload: dict[str, Any] = {
            "model": self._profile.model,
            "max_tokens": self._profile.max_tokens,
            "system": system,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        request = Request(
            _model_endpoint(self._profile.base_url, "messages"),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=_with_provider_headers(
                self._profile,
                self._session_id,
                {
                    "content-type": "application/json",
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "accept": "text/event-stream",
                    "user-agent": "GeoPhysAgent/0.1",
                },
            ),
        )
        blocks: dict[int, dict[str, Any]] = {}
        json_fragments: dict[int, list[str]] = {}
        stopped_blocks: set[int] = set()
        response_id: str | None = None
        response_model = self._profile.model
        stop_reason: str | None = None
        usage: dict[str, int] = {}
        received = 0
        try:
            with urlopen(request, timeout=self._profile.timeout_seconds) as response:
                for raw_line in response:
                    received += len(raw_line)
                    if received > MAX_RESPONSE_BYTES:
                        raise ModelClientError(
                            "Model API stream exceeds the 8 MiB safety limit",
                            category="protocol",
                        )
                    try:
                        line = raw_line.decode("utf-8").strip()
                    except UnicodeDecodeError as exc:
                        raise ModelClientError(
                            "Model API stream contains invalid UTF-8",
                            category="protocol",
                        ) from exc
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise ModelClientError(
                            "Model API stream contains invalid JSON",
                            category="protocol",
                        ) from exc
                    if not isinstance(event, dict):
                        continue
                    event_type = event.get("type")
                    if event_type == "error":
                        error = event.get("error", {})
                        detail = (
                            str(error.get("message", "stream failed"))
                            if isinstance(error, dict)
                            else "stream failed"
                        )
                        raise ModelClientError(
                            detail[:300], category="server_error", retryable=True
                        )
                    if event_type == "message_start":
                        message = event.get("message", {})
                        if isinstance(message, dict):
                            response_id = str(message["id"]) if message.get("id") else None
                            response_model = str(message.get("model", self._profile.model))
                            _merge_usage(usage, message.get("usage"))
                    elif event_type == "content_block_start":
                        index = int(event.get("index", len(blocks)))
                        block = event.get("content_block", {})
                        if isinstance(block, dict):
                            blocks[index] = dict(block)
                            if block.get("type") == "tool_use":
                                json_fragments[index] = []
                    elif event_type == "content_block_delta":
                        index = int(event.get("index", 0))
                        delta = event.get("delta", {})
                        if not isinstance(delta, dict):
                            continue
                        if delta.get("type") == "text_delta":
                            text_delta = str(delta.get("text", ""))
                            block = blocks.setdefault(index, {"type": "text", "text": ""})
                            block["text"] = str(block.get("text", "")) + text_delta
                            if text_delta and on_text_delta is not None:
                                on_text_delta(text_delta)
                        elif delta.get("type") == "input_json_delta":
                            json_fragments.setdefault(index, []).append(
                                str(delta.get("partial_json", ""))
                            )
                        elif delta.get("type") == "thinking_delta":
                            thinking_delta = str(delta.get("thinking", ""))
                            block = blocks.setdefault(index, {"type": "thinking", "thinking": ""})
                            block["thinking"] = str(block.get("thinking", "")) + thinking_delta
                        elif delta.get("type") == "signature_delta":
                            signature_delta = str(delta.get("signature", ""))
                            block = blocks.setdefault(index, {"type": "thinking", "thinking": ""})
                            block["signature"] = str(block.get("signature", "")) + signature_delta
                    elif event_type == "content_block_stop":
                        index = int(event.get("index", 0))
                        stopped_blocks.add(index)
                    elif event_type == "message_delta":
                        delta = event.get("delta", {})
                        if isinstance(delta, dict) and delta.get("stop_reason") is not None:
                            stop_reason = str(delta["stop_reason"])
                        _merge_usage(usage, event.get("usage"))
        except HTTPError as exc:
            detail = _safe_http_detail(exc).replace(self._api_key, "[REDACTED]")
            category = (
                "credentials"
                if exc.code in {401, 403}
                else "rate_limited"
                if exc.code == 429
                else "server_error"
                if exc.code >= 500
                else "http_error"
            )
            raise ModelClientError(
                f"Model API returned HTTP {exc.code}: {detail}",
                category=category,
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except URLError as exc:
            raise ModelClientError(
                f"Cannot connect to model API: {str(exc.reason)[:300]}",
                category="network",
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ModelClientError(
                "Model API request timed out", category="timeout", retryable=True
            ) from exc

        _finalize_stream_tool_inputs(
            blocks,
            json_fragments,
            stopped_blocks=stopped_blocks,
            stop_reason=stop_reason,
        )
        if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
            raise ModelClientError(
                "Model stream reached its output limit before a complete Agent turn; "
                "retry with a more concise response or a larger configured max_tokens",
                category="output_limit",
                retryable=True,
            )
        content = tuple(blocks[index] for index in sorted(blocks))
        content = _normalize_textual_tool_calls(content, tools)
        if not content:
            raise ModelClientError("Model API returned an empty stream", category="protocol")
        return ModelResponse(
            content=content,
            stop_reason=stop_reason,
            model=response_model,
            response_id=response_id,
            usage=usage,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )


class OpenAICompatibleClient:
    """Minimal OpenAI Chat Completions adapter normalized to Agent content blocks."""

    def __init__(self, profile: ModelProfile, api_key: str):
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._profile = profile
        self._api_key = api_key
        self._session_id = str(uuid.uuid4())

    def bind_session(self, session_id: str) -> None:
        self._session_id = _validated_session_id(session_id)

    @property
    def provider(self) -> str:
        return self._profile.provider

    @property
    def model(self) -> str:
        return self._profile.model

    @property
    def protocol(self) -> str:
        return self._profile.protocol

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelResponse:
        started = time.monotonic()
        openai_messages = [{"role": "system", "content": system}]
        openai_messages.extend(_to_openai_messages(messages))
        payload: dict[str, Any] = {
            "model": self._profile.model,
            "max_tokens": self._profile.max_tokens,
            "messages": openai_messages,
        }
        if tools:
            payload["tools"] = [_to_openai_tool(tool) for tool in tools]
        request = Request(
            _model_endpoint(self._profile.base_url, "chat/completions"),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=_with_provider_headers(
                self._profile,
                self._session_id,
                {
                    "content-type": "application/json",
                    "authorization": f"Bearer {self._api_key}",
                    "user-agent": "GeoPhysAgent/0.1",
                },
            ),
        )
        try:
            with urlopen(request, timeout=self._profile.timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ModelClientError("Model API response exceeds the 8 MiB safety limit")
        except HTTPError as exc:
            detail = _safe_http_detail(exc).replace(self._api_key, "[REDACTED]")
            category = (
                "credentials"
                if exc.code in {401, 403}
                else "rate_limited"
                if exc.code == 429
                else "server_error"
                if exc.code >= 500
                else "http_error"
            )
            raise ModelClientError(
                f"Model API returned HTTP {exc.code}: {detail}",
                category=category,
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except URLError as exc:
            raise ModelClientError(
                f"Cannot connect to model API: {str(exc.reason)[:300]}",
                category="network",
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ModelClientError(
                "Model API request timed out", category="timeout", retryable=True
            ) from exc

        try:
            result = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelClientError("Model API returned invalid JSON", category="protocol") from exc
        if not isinstance(result, dict):
            raise ModelClientError("Model API response must be an object", category="protocol")
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelClientError("Model API response has no valid choices", category="protocol")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelClientError("Model API choice has no valid message", category="protocol")
        content = _from_openai_message(message)
        if not content:
            raise ModelClientError("Model API returned empty content", category="protocol")
        finish_reason = choice.get("finish_reason")
        stop_reason = (
            "tool_use"
            if finish_reason == "tool_calls"
            else "max_tokens"
            if finish_reason == "length"
            else str(finish_reason)
            if finish_reason is not None
            else None
        )
        usage = _from_openai_usage(result.get("usage"))
        return ModelResponse(
            content=content,
            stop_reason=stop_reason,
            model=str(result.get("model", self._profile.model)),
            response_id=str(result["id"]) if result.get("id") else None,
            usage=usage,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )


def create_model_client(profile: ModelProfile, api_key: str | None = None) -> ModelClient:
    """Create the protocol adapter selected by a validated model profile."""

    credential = profile.resolve_api_key() if api_key is None else api_key
    if profile.protocol == "anthropic-compatible":
        return AnthropicCompatibleClient(profile, credential)
    if profile.protocol == "openai-compatible":
        return OpenAICompatibleClient(profile, credential)
    raise ValueError(f"Unsupported model protocol: {profile.protocol}")


def bind_model_session(client: ModelClient, session_id: str) -> None:
    """Bind providers that support conversation routing to the Harness task identity."""

    binder = getattr(client, "bind_session", None)
    if callable(binder):
        binder(session_id)


def _validated_session_id(session_id: str) -> str:
    value = str(session_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
        raise ValueError("model session_id must be 1-128 safe ASCII characters")
    return value


def _with_provider_headers(
    profile: ModelProfile,
    session_id: str,
    headers: dict[str, str],
) -> dict[str, str]:
    endpoint = urlparse(profile.base_url)
    if profile.provider == "opencode-go" or (
        endpoint.hostname == "opencode.ai" and endpoint.path.rstrip("/") == "/zen/go"
    ):
        return {**headers, "x-opencode-session": session_id}
    return headers


def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    name = tool.get("name")
    schema = tool.get("input_schema")
    if not isinstance(name, str) or not name or not isinstance(schema, dict):
        raise ModelClientError("Agent Tool definition is invalid", category="protocol")
    function: dict[str, Any] = {"name": name, "parameters": schema}
    if isinstance(tool.get("description"), str):
        function["description"] = tool["description"]
    return {"type": "function", "function": function}


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "assistant" and isinstance(content, list):
            text_parts = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            calls = []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                arguments = block.get("input", {})
                if not isinstance(arguments, dict):
                    raise ModelClientError(
                        "Agent Tool Call arguments must be an object", category="protocol"
                    )
                calls.append(
                    {
                        "id": str(block.get("id", "")),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name", "")),
                            "arguments": json.dumps(
                                arguments, ensure_ascii=False, separators=(",", ":")
                            ),
                        },
                    }
                )
            converted_message: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(part for part in text_parts if part) or None,
            }
            if calls:
                converted_message["tool_calls"] = calls
            converted.append(converted_message)
            continue
        if role == "user" and isinstance(content, list):
            tool_results = [
                block
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            if len(tool_results) == len(content):
                for block in tool_results:
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(block.get("tool_use_id", "")),
                            "content": str(block.get("content", "")),
                        }
                    )
                continue
            content = "\n".join(
                str(block.get("text", block.get("content", "")))
                for block in content
                if isinstance(block, dict)
            )
        if role not in {"user", "assistant"}:
            raise ModelClientError("Agent message role is invalid", category="protocol")
        converted.append({"role": role, "content": str(content)})
    return converted


def _from_openai_message(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    blocks: list[dict[str, Any]] = []
    raw_content = message.get("content")
    if isinstance(raw_content, str) and raw_content:
        blocks.append({"type": "text", "text": raw_content})
    elif isinstance(raw_content, list):
        text = "\n".join(
            str(item.get("text", ""))
            for item in raw_content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        )
        if text:
            blocks.append({"type": "text", "text": text})
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                raise ModelClientError("Model Tool Call is malformed", category="model_output")
            function = call["function"]
            raw_arguments = function.get("arguments", "{}")
            try:
                arguments = (
                    raw_arguments
                    if isinstance(raw_arguments, dict)
                    else json.loads(str(raw_arguments) or "{}")
                )
            except json.JSONDecodeError as exc:
                raise ModelClientError(
                    "Model Tool Call contains invalid arguments JSON",
                    category="model_output",
                    retryable=True,
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelClientError(
                    "Model Tool Call arguments must be an object",
                    category="model_output",
                    retryable=True,
                )
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(call.get("id", "")),
                    "name": str(function.get("name", "")),
                    "input": arguments,
                }
            )
    return tuple(blocks)


def _from_openai_usage(payload: Any) -> dict[str, int] | None:
    if not isinstance(payload, dict):
        return None
    usage: dict[str, int] = {}
    mappings = {
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
        "total_tokens": "total_tokens",
    }
    for source, target in mappings.items():
        value = payload.get(source)
        if isinstance(value, int) and value >= 0:
            usage[target] = value
    return usage or None


def _model_endpoint(base_url: str, operation: str) -> str:
    """Accept provider roots both with and without their conventional /v1 suffix."""

    return f"{base_url}/{operation}" if base_url.endswith("/v1") else f"{base_url}/v1/{operation}"


def _finalize_stream_tool_inputs(
    blocks: dict[int, dict[str, Any]],
    json_fragments: dict[int, list[str]],
    *,
    stopped_blocks: set[int],
    stop_reason: str | None,
) -> None:
    """Finalize Tool inputs only after the stream's stop reason is known."""

    for index, fragments in json_fragments.items():
        raw = "".join(fragments)
        if index not in stopped_blocks:
            _raise_incomplete_tool_input(raw, stop_reason, "content block did not stop")
        parsed: Any
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as first_error:
            compatible = _merge_compatible_json_fragments(fragments)
            if compatible != raw:
                try:
                    parsed = json.loads(compatible) if compatible else {}
                except json.JSONDecodeError:
                    _raise_incomplete_tool_input(
                        raw,
                        stop_reason,
                        f"invalid JSON at character {first_error.pos}",
                    )
            else:
                _raise_incomplete_tool_input(
                    raw,
                    stop_reason,
                    f"invalid JSON at character {first_error.pos}",
                )
        if not isinstance(parsed, dict):
            raise ModelClientError(
                "Model Tool Call arguments must be an object",
                category="model_output",
                retryable=True,
            )
        blocks.setdefault(index, {"type": "tool_use"})["input"] = parsed


def _merge_compatible_json_fragments(fragments: list[str]) -> str:
    """Accept cumulative/overlapping deltas from compatible API implementations.

    Anthropic deltas are append-only fragments. Some compatible gateways have
    emitted cumulative snapshots or retransmitted an overlapping suffix. The
    fallback is used only when standard concatenation is not valid JSON.
    """

    merged = ""
    for fragment in fragments:
        if not merged:
            merged = fragment
        elif fragment.startswith(merged):
            merged = fragment
        elif merged.endswith(fragment):
            continue
        else:
            overlap = _longest_safe_overlap(merged, fragment)
            merged += fragment[overlap:]
    return merged


def _longest_safe_overlap(left: str, right: str) -> int:
    maximum = min(len(left), len(right))
    for length in range(maximum, 15, -1):
        if left.endswith(right[:length]):
            return length
    return 0


def _raise_incomplete_tool_input(
    raw: str,
    stop_reason: str | None,
    detail: str,
) -> None:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
        raise ModelClientError(
            "Model Tool Call arguments were truncated at the configured output limit "
            f"({len(raw)} characters, sha256={digest}); {detail}",
            category="output_limit",
            retryable=True,
        )
    raise ModelClientError(
        "Model Tool Call stream ended with malformed arguments "
        f"({len(raw)} characters, sha256={digest}); {detail}",
        category="model_output",
        retryable=True,
    )


def _merge_usage(target: dict[str, int], payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    for name, value in payload.items():
        if isinstance(name, str) and isinstance(value, int) and value >= 0:
            target[name] = value


def _normalize_textual_tool_calls(
    content: tuple[dict[str, Any], ...],
    tools: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Convert MiniMax's textual tool envelope only when it names an allowed tool."""
    definitions = {
        str(tool.get("name")): tool
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    if not definitions:
        return content
    normalized: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") != "text" or not isinstance(block.get("text"), str):
            normalized.append(block)
            continue
        parsed = _parse_minimax_tool_call(block["text"], definitions)
        normalized.append(parsed if parsed is not None else block)
    return tuple(normalized)


def _parse_minimax_tool_call(
    text: str,
    definitions: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    envelope = re.fullmatch(
        r"\s*<minimax:tool_call>\s*<invoke\s+name=\"([^\"]+)\">(.*?)"
        r"</invoke>\s*</minimax:tool_call>\s*",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if envelope is None:
        return None
    name, body = envelope.groups()
    definition = definitions.get(name)
    if definition is None:
        return None
    raw_parameters = re.findall(
        r"<parameter\s+name=\"([^\"]+)\">(.*?)</parameter>",
        body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not raw_parameters:
        return None
    properties = definition.get("input_schema", {}).get("properties", {})
    arguments: dict[str, Any] = {}
    for parameter_name, raw_value in raw_parameters:
        value = html.unescape(raw_value).strip()
        expected = properties.get(parameter_name, {}).get("type")
        if expected == "string" or expected is None:
            arguments[parameter_name] = value
            continue
        try:
            arguments[parameter_name] = json.loads(value)
        except json.JSONDecodeError:
            arguments[parameter_name] = value
    call_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return {
        "type": "tool_use",
        "id": f"text-tool-{call_hash}",
        "name": name,
        "input": arguments,
    }


def _safe_http_detail(error: HTTPError) -> str:
    try:
        body = error.read(2048).decode("utf-8", errors="replace")
        payload = json.loads(body)
        if isinstance(payload, dict):
            candidate = payload.get("error", payload.get("message", "request failed"))
            if isinstance(candidate, dict):
                candidate = candidate.get("message", "request failed")
            return str(candidate)[:300]
    except (OSError, json.JSONDecodeError):
        pass
    return "request failed"
