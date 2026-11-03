from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, cast
from urllib.parse import urlparse


ModelProtocol = Literal["anthropic-compatible", "openai-compatible"]
SUPPORTED_MODEL_PROTOCOLS: tuple[ModelProtocol, ...] = (
    "anthropic-compatible",
    "openai-compatible",
)


class ConfigurationError(ValueError):
    """Raised when application configuration is missing or unsafe."""


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model: str
    protocol: ModelProtocol
    base_url: str
    api_key_env: str
    timeout_seconds: float = 120.0
    max_tokens: int = 2000
    profile_name: str = "default"

    def resolve_api_key(self, environ: Mapping[str, str] | None = None) -> str:
        source = os.environ if environ is None else environ
        api_key = source.get(self.api_key_env, "").strip()
        if not api_key:
            raise ConfigurationError(f"Environment variable {self.api_key_env} is not configured")
        return api_key


@dataclass(frozen=True)
class ModelConfiguration:
    """Named model profiles loaded from one user-editable TOML file."""

    default_profile: str
    profiles: Mapping[str, ModelProfile]
    legacy_format: bool = False

    def select(self, profile_name: str | None = None) -> ModelProfile:
        selected = (profile_name or self.default_profile).strip()
        if not selected:
            raise ConfigurationError("Model profile name must not be empty")
        try:
            return self.profiles[selected]
        except KeyError as exc:
            choices = ", ".join(sorted(self.profiles))
            raise ConfigurationError(
                f"Unknown model profile '{selected}'. Configured profiles: {choices}"
            ) from exc


def load_model_configuration(path: str | Path) -> ModelConfiguration:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise ConfigurationError(f"Configuration file does not exist: {config_path}")
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Cannot read configuration: {exc}") from exc

    models = payload.get("models")
    if isinstance(models, dict):
        default_profile = str(models.get("default", "")).strip()
        raw_profiles = models.get("profiles")
        if not default_profile:
            raise ConfigurationError("Configuration requires models.default")
        if not isinstance(raw_profiles, dict) or not raw_profiles:
            raise ConfigurationError(
                "Configuration requires at least one [models.profiles.*] section"
            )
        profiles: dict[str, ModelProfile] = {}
        for name, settings in raw_profiles.items():
            profile_name = str(name).strip()
            if not profile_name or not isinstance(settings, dict):
                raise ConfigurationError("Each models.profiles entry must be a named TOML table")
            if any(isinstance(value, dict) for value in settings.values()):
                raise ConfigurationError(
                    f"Model profile '{profile_name}' is nested. TOML treats dots in bare table "
                    "names as separators; quote the profile name, for example "
                    f'[models.profiles."{profile_name}.model-name"]'
                )
            profiles[profile_name] = _parse_model_profile(settings, profile_name=profile_name)
        configuration = ModelConfiguration(
            default_profile=default_profile,
            profiles=MappingProxyType(profiles),
        )
        configuration.select()
        return configuration

    # Backward compatibility lets existing installations migrate without an abrupt break.
    legacy = payload.get("model")
    if isinstance(legacy, dict):
        profile = _parse_model_profile(legacy, profile_name="default")
        return ModelConfiguration(
            default_profile="default",
            profiles=MappingProxyType({"default": profile}),
            legacy_format=True,
        )
    raise ConfigurationError(
        "Configuration requires [models] with [models.profiles.*] sections "
        "(legacy [model] is still accepted)"
    )


def load_model_profile(path: str | Path, profile_name: str | None = None) -> ModelProfile:
    """Load the configured default profile or a one-run named override."""

    return load_model_configuration(path).select(profile_name)


def _parse_model_profile(model: Mapping[str, object], *, profile_name: str) -> ModelProfile:
    required = ("provider", "model", "protocol", "base_url", "api_key_env")
    missing = [name for name in required if not str(model.get(name, "")).strip()]
    if missing:
        raise ConfigurationError(
            f"Missing settings in model profile '{profile_name}': {', '.join(missing)}"
        )

    protocol_value = str(model["protocol"])
    if protocol_value not in SUPPORTED_MODEL_PROTOCOLS:
        supported = ", ".join(repr(item) for item in SUPPORTED_MODEL_PROTOCOLS)
        raise ConfigurationError(
            f"Model profile '{profile_name}' has unsupported protocol '{protocol_value}'; "
            f"supported protocols: {supported}"
        )
    protocol = cast(ModelProtocol, protocol_value)

    base_url = str(model["base_url"]).rstrip("/")
    parsed_url = urlparse(base_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ConfigurationError(
            f"Model profile '{profile_name}' base_url must be an absolute HTTPS URL"
        )
    if parsed_url.query or parsed_url.fragment or parsed_url.username or parsed_url.password:
        raise ConfigurationError(
            f"Model profile '{profile_name}' base_url must not contain credentials, query, or fragment"
        )

    api_key_env = str(model["api_key_env"])
    if (
        not api_key_env
        or not api_key_env.replace("_", "").isalnum()
        or not api_key_env[0].isalpha()
    ):
        raise ConfigurationError(
            f"Model profile '{profile_name}' api_key_env must be an environment variable name "
            "such as OPENCODE_API_KEY; never paste an API key into the TOML file"
        )

    try:
        timeout_seconds = float(model.get("timeout_seconds", 120))
        max_tokens = int(model.get("max_tokens", 2000))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"Model profile '{profile_name}' timeout_seconds and max_tokens must be numeric"
        ) from exc
    if not 1 <= timeout_seconds <= 600:
        raise ConfigurationError(
            f"Model profile '{profile_name}' timeout_seconds must be between 1 and 600"
        )
    if not 1 <= max_tokens <= 32768:
        raise ConfigurationError(
            f"Model profile '{profile_name}' max_tokens must be between 1 and 32768"
        )

    return ModelProfile(
        provider=str(model["provider"]),
        model=str(model["model"]),
        protocol=protocol,
        base_url=base_url,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        profile_name=profile_name,
    )
