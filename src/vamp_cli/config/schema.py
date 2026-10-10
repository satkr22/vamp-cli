from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelCapabilities(BaseModel):
    """Declarative capabilities used by higher layers before invocation."""

    model_config = ConfigDict(extra="allow")

    supports_tools: bool = True
    supports_parallel_tools: bool = True
    supports_streaming: bool = True
    supports_reasoning: bool = False
    reasoning_param: str | None = None
    context_window: int = 128_000


class ModelConfig(BaseModel):
    """Configuration for one logical model name."""

    model_config = ConfigDict(extra="forbid")

    model: str
    api_base: str | None = None
    api_key_env: str | None = None

    # Resolved by config.loader.py from api_key_env.
    # It is excluded from normal serialization and displayed masked by SecretStr.
    api_key: str | None = Field(default=None, repr=False)

    params: dict[str, Any] = Field(default_factory=dict)
    fallbacks: list[str] = Field(default_factory=list)
    adapter: str = "default"
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)


class ProfileConfig(BaseModel):
    """Agent profile configuration consumed later by AgentRuntime."""

    model_config = ConfigDict(extra="forbid")

    model: str
    tools: list[str] = Field(default_factory=list)
    prompt: str


class AppConfig(BaseModel):
    """Validated application configuration loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    models: dict[str, ModelConfig]
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    default_profile: list[str] = ["default_coder", "default_planner"]
    
    @model_validator(mode="after")
    def _check_references(self) -> "AppConfig":
        
        # Ensure default_profile list is not empty
        if not self.default_profile:
            raise ValueError("config must define at least one item in default_profile")
        
        # Check that every item in default_profile list actually exists under profiles
        for p in self.default_profile:
            if p not in self.profiles:
                raise ValueError(f"default_profile item '{p}' is not defined in profiles.")

        # Check profile models match defined models
        for name, profile in self.profiles.items():
            if profile.model not in self.models:
                raise ValueError(
                    f"profile '{name}' uses unknown model '{profile.model}'. "
                    f"Defined models: {sorted(self.models)}"
                )
    
        return self
