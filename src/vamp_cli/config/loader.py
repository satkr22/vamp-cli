from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from vamp_cli.config.schema import AppConfig, ModelConfig

DEFAULT_CONFIG_PATH = "config.yaml"

class ConfigError(ValueError):
    """Raised when configuration is structurally invalid or incomplete."""


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load, validate, resolve secrets, and cross-check the YAML config.

    This is the only place in the application that reads config.yaml.
    """

    config_path = Path(path).expanduser().resolve()

    if not config_path.is_file():
        raise ConfigError(f"Config file does not exist: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Unable to read config {config_path}: {exc}") from exc

    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {config_path}:\n{exc}") from exc

    _resolve_api_keys(config)
    _validate_references(config)
    return config


def get_api_key(cfg: ModelConfig) -> str | None:
    """Return a model's already-resolved API key."""

    return cfg.api_key


def _resolve_api_keys(config: AppConfig) -> None:
    """Resolve API key environment variables into the in-memory config."""

    for name, cfg in config.models.items():
        if not cfg.api_key_env:
            continue

        value = os.getenv(cfg.api_key_env)
        if not value:
            raise ConfigError(
                f"Model '{name}' requires environment variable "
                f"'{cfg.api_key_env}', but it is not set."
            )

        cfg.api_key = value


def _validate_references(config: AppConfig) -> None:
    """Validate model references, profile references, and fallback cycles."""

    model_names = set(config.models)

    if not model_names:
        raise ConfigError("At least one model must be configured.")

    if config.profiles and config.default_profile not in config.profiles:
        raise ConfigError(
            f"default_profile '{config.default_profile}' is not defined in profiles."
        )

    for profile_name, profile in config.profiles.items():
        if profile.model not in model_names:
            raise ConfigError(
                f"Profile '{profile_name}' references unknown model "
                f"'{profile.model}'."
            )

    for model_name, model in config.models.items():
        for fallback in model.fallbacks:
            if fallback not in model_names:
                raise ConfigError(
                    f"Model '{model_name}' references unknown fallback '{fallback}'."
                )
            if fallback == model_name:
                raise ConfigError(
                    f"Model '{model_name}' cannot use itself as a fallback."
                )

    _check_fallback_cycles(config)


def _check_fallback_cycles(config: AppConfig) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(model_name: str) -> None:
        if model_name in visiting:
            raise ConfigError(
                f"Fallback cycle detected involving model '{model_name}'."
            )
        if model_name in visited:
            return

        visiting.add(model_name)
        for fallback in config.models[model_name].fallbacks:
            visit(fallback)
        visiting.remove(model_name)
        visited.add(model_name)

    for model_name in config.models:
        visit(model_name)
