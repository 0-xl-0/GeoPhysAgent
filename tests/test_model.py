from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from geophysagent.config import ModelProfile
from geophysagent.model import (
    AnthropicCompatibleClient,
    ModelClientError,
    OpenAICompatibleClient,
    create_model_client,
)


class ModelClientTests(unittest.TestCase):
    def _profile(self) -> ModelProfile:
        return ModelProfile(
            provider="minimax-cn",
            model="MiniMax-M2.7",
            protocol="anthropic-compatible",
            base_url="https://api.minimaxi.com/anthropic",
            api_key_env="MINIMAX_CN_API_KEY",
            timeout_seconds=30,
            max_tokens=1000,
        )

    @patch("geophysagent.model.urlopen")
    def test_anthropic_compatible_request(self, mocked_urlopen: MagicMock) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "id": "msg-1",
                "model": "MiniMax-M2.7",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
            }
        ).encode("utf-8")
        mocked_urlopen.return_value.__enter__.return_value = response
        profile = ModelProfile(
            provider="minimax-cn",
            model="MiniMax-M2.7",
            protocol="anthropic-compatible",
            base_url="https://api.minimaxi.com/anthropic",
            api_key_env="MINIMAX_CN_API_KEY",
            timeout_seconds=30,
            max_tokens=1000,
        )
        client = AnthropicCompatibleClient(profile, "test-secret")

        result = client.complete(
            system="system",
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
        )

        request = mocked_urlopen.call_args.args[0]
        request_headers = {key.lower(): value for key, value in request.header_items()}
        request_payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.minimaxi.com/anthropic/v1/messages")
        self.assertEqual(request_headers["x-api-key"], "test-secret")
        self.assertEqual(request_payload["model"], "MiniMax-M2.7")
        self.assertNotIn("tools", request_payload)
        self.assertEqual(result.content[0]["text"], "ok")

    @patch("geophysagent.model.urlopen")
    def test_minimax_textual_tool_call_is_normalized_when_tool_is_allowed(
        self, mocked_urlopen: MagicMock
    ) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "id": "msg-tool",
                "model": "MiniMax-M2.7",
                "stop_reason": "end_turn",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '<minimax:tool_call><invoke name="run_python_analysis">'
                            '<parameter name="asset_id">primary</parameter>'
                            '<parameter name="code">RESULT = {"ok": True}</parameter>'
                            '<parameter name="purpose">继续分析</parameter>'
                            "</invoke></minimax:tool_call>"
                        ),
                    }
                ],
            }
        ).encode("utf-8")
        mocked_urlopen.return_value.__enter__.return_value = response
        profile = ModelProfile(
            provider="minimax-cn",
            model="MiniMax-M2.7",
            protocol="anthropic-compatible",
            base_url="https://api.minimaxi.com/anthropic",
            api_key_env="MINIMAX_CN_API_KEY",
            timeout_seconds=30,
            max_tokens=1000,
        )
        client = AnthropicCompatibleClient(profile, "test-secret")
        tool = {
            "name": "run_python_analysis",
            "input_schema": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "code": {"type": "string"},
                    "purpose": {"type": "string"},
                },
            },
        }

        result = client.complete(system="system", messages=[], tools=[tool])

        self.assertEqual(result.content[0]["type"], "tool_use")
        self.assertEqual(result.content[0]["name"], "run_python_analysis")
        self.assertEqual(result.content[0]["input"]["asset_id"], "primary")
        self.assertIn("RESULT", result.content[0]["input"]["code"])

    @patch("geophysagent.model.urlopen")
    def test_anthropic_stream_reconstructs_text_tool_and_usage(
        self, mocked_urlopen: MagicMock
    ) -> None:
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-stream",
                    "model": "MiniMax-M2.7",
                    "usage": {"input_tokens": 11},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "完成"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-stream",
                    "name": "inspect_asset",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"asset_id":"primary"}',
                },
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 7},
            },
            {"type": "message_stop"},
        ]
        response = MagicMock()
        response.__iter__.return_value = iter(
            [f"data: {json.dumps(event, ensure_ascii=False)}\n".encode("utf-8") for event in events]
        )
        mocked_urlopen.return_value.__enter__.return_value = response
        client = AnthropicCompatibleClient(self._profile(), "test-secret")
        deltas: list[str] = []

        result = client.stream_complete(
            system="system",
            messages=[],
            tools=[
                {
                    "name": "inspect_asset",
                    "input_schema": {
                        "type": "object",
                        "properties": {"asset_id": {"type": "string"}},
                    },
                }
            ],
            on_text_delta=deltas.append,
        )

        request = mocked_urlopen.call_args.args[0]
        self.assertTrue(json.loads(request.data)["stream"])
        self.assertEqual(deltas, ["完成"])
        self.assertEqual(result.content[0]["text"], "完成")
        self.assertEqual(result.content[1]["input"], {"asset_id": "primary"})
        self.assertEqual(result.usage, {"input_tokens": 11, "output_tokens": 7})
        self.assertEqual(result.response_id, "msg-stream")

    @patch("geophysagent.model.urlopen")
    def test_stream_preserves_thinking_and_accepts_cumulative_tool_json(
        self, mocked_urlopen: MagicMock
    ) -> None:
        events = [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "检查参数"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "signed"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-cumulative",
                    "name": "inspect_asset",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"asset_id":',
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"asset_id":"primary"}',
                },
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
            },
        ]
        response = MagicMock()
        response.__iter__.return_value = iter(
            [f"data: {json.dumps(event, ensure_ascii=False)}\n".encode() for event in events]
        )
        mocked_urlopen.return_value.__enter__.return_value = response
        client = AnthropicCompatibleClient(self._profile(), "test-secret")

        result = client.stream_complete(
            system="system",
            messages=[],
            tools=[{"name": "inspect_asset", "input_schema": {"type": "object"}}],
        )

        self.assertEqual(result.content[0]["thinking"], "检查参数")
        self.assertEqual(result.content[0]["signature"], "signed")
        self.assertEqual(result.content[1]["input"], {"asset_id": "primary"})

    @patch("geophysagent.model.urlopen")
    def test_truncated_stream_tool_json_is_retryable_output_limit(
        self, mocked_urlopen: MagicMock
    ) -> None:
        events = [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-truncated",
                    "name": "inspect_asset",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"asset_id":"primary"',
                },
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens"},
            },
        ]
        response = MagicMock()
        response.__iter__.return_value = iter(
            [f"data: {json.dumps(event)}\n".encode() for event in events]
        )
        mocked_urlopen.return_value.__enter__.return_value = response
        client = AnthropicCompatibleClient(self._profile(), "test-secret")

        with self.assertRaises(ModelClientError) as raised:
            client.stream_complete(
                system="system",
                messages=[],
                tools=[{"name": "inspect_asset", "input_schema": {"type": "object"}}],
            )

        self.assertEqual(raised.exception.category, "output_limit")
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("primary", str(raised.exception))

    @patch("geophysagent.model.urlopen")
    def test_openai_compatible_request_and_tool_call_are_normalized(
        self, mocked_urlopen: MagicMock
    ) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "id": "chat-1",
                "model": "kimi-k3",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_asset",
                                        "arguments": '{"asset_id":"primary"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            }
        ).encode("utf-8")
        mocked_urlopen.return_value.__enter__.return_value = response
        profile = ModelProfile(
            provider="opencode-go",
            model="kimi-k3",
            protocol="openai-compatible",
            base_url="https://opencode.ai/zen/go",
            api_key_env="OPENCODE_API_KEY",
        )
        client = OpenAICompatibleClient(profile, "test-secret")
        client.bind_session("task-openai-123")
        tools = [
            {
                "name": "inspect_asset",
                "description": "Inspect an asset",
                "input_schema": {
                    "type": "object",
                    "properties": {"asset_id": {"type": "string"}},
                    "required": ["asset_id"],
                },
            }
        ]

        result = client.complete(
            system="system",
            messages=[{"role": "user", "content": "hello"}],
            tools=tools,
        )

        request = mocked_urlopen.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://opencode.ai/zen/go/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer test-secret")
        self.assertEqual(headers["x-opencode-session"], "task-openai-123")
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "system"})
        self.assertEqual(payload["tools"][0]["function"]["name"], "inspect_asset")
        self.assertEqual(result.stop_reason, "tool_use")
        self.assertEqual(result.content[0]["type"], "tool_use")
        self.assertEqual(result.content[0]["input"], {"asset_id": "primary"})
        self.assertEqual(result.usage, {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17})

    @patch("geophysagent.model.urlopen")
    def test_opencode_anthropic_request_includes_bound_session(
        self, mocked_urlopen: MagicMock
    ) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "id": "msg-go",
                "model": "minimax-m3",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
            }
        ).encode("utf-8")
        mocked_urlopen.return_value.__enter__.return_value = response
        profile = ModelProfile(
            provider="opencode-go",
            model="minimax-m3",
            protocol="anthropic-compatible",
            base_url="https://opencode.ai/zen/go",
            api_key_env="OPENCODE_API_KEY",
        )
        client = AnthropicCompatibleClient(profile, "test-secret")
        client.bind_session("task-anthropic-456")

        client.complete(system="system", messages=[], tools=[])

        request = mocked_urlopen.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["x-opencode-session"], "task-anthropic-456")

    def test_model_session_rejects_header_injection(self) -> None:
        profile = ModelProfile(
            provider="opencode-go",
            model="kimi-k3",
            protocol="openai-compatible",
            base_url="https://opencode.ai/zen/go",
            api_key_env="OPENCODE_API_KEY",
        )
        client = OpenAICompatibleClient(profile, "test-secret")
        with self.assertRaises(ValueError):
            client.bind_session("bad\r\nx-injected: yes")

    @patch("geophysagent.model.urlopen")
    def test_openai_history_translates_tool_calls_and_results(
        self, mocked_urlopen: MagicMock
    ) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "完成"},
                    }
                ]
            }
        ).encode("utf-8")
        mocked_urlopen.return_value.__enter__.return_value = response
        profile = ModelProfile(
            provider="test",
            model="test-model",
            protocol="openai-compatible",
            base_url="https://example.invalid/v1",
            api_key_env="TEST_KEY",
        )
        client = create_model_client(profile, "test-secret")

        result = client.complete(
            system="system",
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "inspect_asset",
                            "input": {"asset_id": "primary"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-1",
                            "content": '{"ok":true}',
                        }
                    ],
                },
            ],
            tools=[],
        )

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
        self.assertEqual(payload["messages"][1]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(payload["messages"][2]["role"], "tool")
        self.assertEqual(payload["messages"][2]["tool_call_id"], "call-1")
        self.assertEqual(result.content, ({"type": "text", "text": "完成"},))


if __name__ == "__main__":
    unittest.main()
