from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from enum import Enum

from langchain_core.runnables import Runnable

from vamp_cli.config.schema import AppConfig, ProfileConfig
from vamp_cli.llm.router import ModelRouter
from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.prompts.prompts import PromptStore


class AgentRoles(Enum):
    DEFAULT = "default"
    PLANNER = "planner"
    EXECUTOR = "executor"
    

@dataclass(frozen=True)
class AgentProfile:
    role: str
    model: Runnable[Any, Any]
    tools: Sequence[Any]
    system_prompt: str
    

class AgentRuntime:

    def __init__(
        self,
        config: AppConfig,
        model_router: ModelRouter,
        tool_registry: ToolRegistry,
        prompt_store: PromptStore,
    ) -> None:
        self._config = config
        self._model_router = model_router
        self._tool_registry = tool_registry
        self._prompt_store = prompt_store
        
    def resolve(self, role: AgentRoles) -> AgentProfile:
        
        profile_cfg = self._resolve_profile(role)

        tools = self._tool_registry.get_tools(
            profile_cfg.tools
        )

        system_prompt = self._prompt_store.load(
            profile_cfg.prompt
        )

        model = self._model_router.get(
            profile_cfg.model,
            tools=tools,
            usage_context=role.value,
        )

        return AgentProfile(
            role=role.value,
            model=model,
            tools=tools,
            system_prompt=system_prompt,
        )
        
    def _resolve_profile(self, role: AgentRoles)-> ProfileConfig:
        
        profiles = self._config.profiles

        if role.value in profiles:
            return profiles[role.value]

        if "default" in profiles:
            return profiles["default"]

        raise KeyError(
            f"Role '{role}' is not configured and no 'default' profile exists"
        )