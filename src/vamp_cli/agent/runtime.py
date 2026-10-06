from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from enum import Enum

from langchain_core.runnables import Runnable

from vamp_cli.config.schema import AppConfig, ProfileConfig
from vamp_cli.llm.router import ModelRouter
from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.prompts.prompts import PromptStore
from vamp_cli.agent.roles import AgentRole
from vamp_cli.tools.base import Tools



@dataclass(frozen=True)
class AgentProfile:
    role: str
    model: Runnable[Any, Any]
    tools: dict[str, Tools]          # {name: Tools} - for execution dispatch
    tool_schemas: list[dict]         # OpenAI-format - for giving tool list to llm model
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
        self._profile_cache: dict[str, AgentProfile] = {}
        
        
    def resolve(self, role: AgentRole) -> AgentProfile:
        
        # check cache hit
        if role.value in self._profile_cache:
            return self._profile_cache[role.value]
        
        profile = self._build_profile(role=role)
        
        # cache the profile
        self._profile_cache[role.value] = profile
        
        return profile
    
        
    def _build_profile(self, role: AgentRole) -> AgentProfile:
        
        profile_cfg = self._resolve_profile(role)
        
        # Tool objects, keyed by name — for execution.
        tools_by_name = self._tool_registry.get_tools(profile_cfg.tools)

        # OpenAI-format schemas — for binding into the router.
        tool_schemas = self._tool_registry.get_openai_tools(profile_cfg.tools)
        
        system_prompt = self._prompt_store.load(
            profile_cfg.prompt
        )
        
        model = self._model_router.get(
            profile_cfg.model,
            tools=tool_schemas,        # openai compatiable tool schema dict
            usage_context=role.value,
        )
        
        profile =  AgentProfile(
            role=role.value,
            model=model,
            tools=tools_by_name,
            tool_schemas=tool_schemas,
            system_prompt=system_prompt,
        )
        
        return profile
        
    
    def _resolve_profile(self, role: AgentRole)-> ProfileConfig:
        
        profiles = self._config.profiles

        if role.value in profiles:
            return profiles[role.value]

        if "default" in profiles:
            return profiles["default"]

        raise KeyError(
            f"Role '{role}' is not configured and no 'default' profile exists"
        )
    
    
'''
at call site:

tool_calls = response.tool_calls

for tc in tool_calls:

    # openai raw tool call
    name = tc["function"]["name"]
    args = json.loads(tc["function"]["arguments"])
    
    tool = profile.tools[name]     # Dict[str, Tools]
    result = tool.run(**args)


def normalize_tool_call(tc: dict) -> tuple[str, dict]:
    if "function" in tc:                          # OpenAI raw
        return tc["function"]["name"], json.loads(tc["function"]["arguments"])
    return tc["name"], tc["args"]                 # LangChain

'''
