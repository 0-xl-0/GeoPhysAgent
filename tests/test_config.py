from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geophysagent.config import (
    ConfigurationError,
    load_model_configuration,
    load_model_profile,
)


VALID_CONFIG = """[model]
provider = "minimax-cn"
model = "MiniMax-M2.7"
protocol = "anthropic-compatible"
base_url = "https://api.minimaxi.com/anthropic"
api_key_env = "MINIMAX_CN_API_KEY"
timeout_seconds = 30
max_tokens = 1000
"""

NAMED_CONFIG = """[models]
default = "go-m3"

[models.profiles.go-m3]
provider = "opencode-go"
model = "minimax-m3"
protocol = "anthropic-compatible"
base_url = "https://opencode.ai/zen/go"
api_key_env = "OPENCODE_API_KEY"
timeout_seconds = 30
max_tokens = 2000

[models.profiles.go-kimi]
provider = "opencode-go"
model = "kimi-k3"
protocol = "openai-compatible"
base_url = "https://opencode.ai/zen/go"
api_key_env = "OPENCODE_API_KEY"
"""


class ConfigTests(unittest.TestCase):
    def test_load_and_resolve_key_without_persisting_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(VALID_CONFIG, encoding="utf-8")
            profile = load_model_profile(path)
        self.assertEqual(profile.provider, "minimax-cn")
        self.assertEqual(profile.model, "MiniMax-M2.7")
        self.assertEqual(profile.resolve_api_key({"MINIMAX_CN_API_KEY": "secret"}), "secret")
        self.assertNotIn("secret", repr(profile))

    def test_plain_http_endpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(VALID_CONFIG.replace("https://", "http://"), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_model_profile(path)

    def test_missing_key_is_reported_by_name_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(VALID_CONFIG, encoding="utf-8")
            profile = load_model_profile(path)
        with self.assertRaisesRegex(ConfigurationError, "MINIMAX_CN_API_KEY"):
            profile.resolve_api_key({})

    def test_named_profiles_select_default_and_one_run_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(NAMED_CONFIG, encoding="utf-8")
            configuration = load_model_configuration(path)
            default = load_model_profile(path)
            override = load_model_profile(path, "go-kimi")

        self.assertEqual(configuration.default_profile, "go-m3")
        self.assertEqual(tuple(configuration.profiles), ("go-m3", "go-kimi"))
        self.assertEqual(default.profile_name, "go-m3")
        self.assertEqual(default.model, "minimax-m3")
        self.assertEqual(override.profile_name, "go-kimi")
        self.assertEqual(override.protocol, "openai-compatible")

    def test_unknown_named_profile_lists_safe_choices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(NAMED_CONFIG, encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "go-kimi, go-m3"):
                load_model_profile(path, "missing")

    def test_unsupported_responses_protocol_fails_during_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                NAMED_CONFIG.replace(
                    'protocol = "openai-compatible"', 'protocol = "openai-responses"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigurationError, "unsupported protocol"):
                load_model_configuration(path)

    def test_unquoted_dotted_profile_name_has_actionable_error(self) -> None:
        dotted = NAMED_CONFIG.replace("[models.profiles.go-kimi]", "[models.profiles.vendor.model]")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(dotted, encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "TOML treats dots"):
                load_model_configuration(path)

    def test_api_key_value_is_rejected_without_echoing_it(self) -> None:
        unsafe_value = "sk-not-a-real-secret-value"
        unsafe = NAMED_CONFIG.replace(
            'api_key_env = "OPENCODE_API_KEY"', f'api_key_env = "{unsafe_value}"'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(unsafe, encoding="utf-8")
            with self.assertRaises(ConfigurationError) as raised:
                load_model_configuration(path)
        self.assertIn("never paste an API key", str(raised.exception))
        self.assertNotIn(unsafe_value, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
