from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Sequence

from langchain_core.runnables import Runnable
from langchain_litellm import ChatLiteLLM

from vamp_cli.config.loader import get_api_key
from vamp_cli.config.schema import AppConfig, ModelCapabilities, ModelConfig
from vamp_cli.llm.adapter_registry import AdapterRegistry
from vamp_cli.llm.usage import UsageTracker, UsageSnapshot, _UsageCallback
from vamp_cli.llm.debug_callbacks import DumpPayloadCallback

log = logging.getLogger(__name__)


# Arguments that ChatLiteLLM accepts directly. Everything else in params is
# passed through as model_kwargs for LiteLLM/provider-specific parameters.
_DIRECT_PARAMS = {
    "temperature",
    "max_tokens",
    "top_p",
    "request_timeout",
    "max_retries",
    "streaming",
}


class ModelRouter:
    """Builds, binds, composes, and caches configured chat model runnables.

    Public flow:

        router.get("main", tools=[...])

    returns one runnable ready for the next layer. Internally the router:

      1. builds/caches each raw ChatLiteLLM once;
      2. binds the same tools to primary and fallback models;
      3. composes the bound models with LangChain fallbacks;
      4. attaches usage tracking;
      5. caches the assembled runnable for the same model/toolset/context.

    The provider remains hidden behind ChatLiteLLM/LiteLLM.
    """

    def __init__(
        self, 
        config: AppConfig,
        adapters: AdapterRegistry | None = None,
    ) -> None:
        self._config = config
        self._adapters = adapters or AdapterRegistry()
        self._model_cache: dict[str, ChatLiteLLM] = {}
        self._runnable_cache: dict[tuple[str, tuple[str, ...], str | None], Runnable[Any, Any]] = {}
        self._usage = UsageTracker()
        self._lock = threading.RLock()

    def get(
        self,
        model_name: str,
        tools: Sequence[Any] | None = None,
        *,
        usage_context: str | None = None,
    ) -> Runnable[Any, Any]:
        """Return a configured, tool-bound, fallback-enabled runnable.

        `tools` may be empty. When fallbacks exist, each fallback is bound to the
        same tool set BEFORE the fallback wrapper is created.
        """

        self._model_cfg(model_name)  # fail fast on unknown model name
        tool_list = tuple(tools or ())
        tool_key = tuple(self._tool_key(tool) for tool in tool_list)
        cache_key = (model_name, tool_key, usage_context)

        with self._lock:
            cached = self._runnable_cache.get(cache_key)
            if cached is not None:
                return cached

            runnable = self._assemble(
                model_name,
                tool_list,
                usage_context=usage_context,
                stack=(),
            )

            self._runnable_cache[cache_key] = runnable
            return runnable

    def capabilities(self, model_name: str) -> ModelCapabilities:
        return self._model_cfg(model_name).capabilities

    def usage(self) -> dict[str, UsageSnapshot]:
        return self._usage.snapshot()

    def _assemble(
        self,
        model_name: str,
        tools: Sequence[Any],
        *,
        usage_context: str | None,
        stack: tuple[str, ...],
    ) -> Runnable[Any, Any]:
        if model_name in stack:
            cycle = " -> ".join((*stack, model_name))
            raise RuntimeError(f"Fallback cycle detected while building: {cycle}")

        cfg = self._model_cfg(model_name)
        
        # 1. get raw ChatLiteLLM
        primary = self._get_base_model(model_name)

        # IMPORTANT: bind tools before wrapping in with_fallbacks().
        
        # 2. Bind tools to primary
        if tools:
            if not cfg.capabilities.supports_tools:
                raise ValueError(
                    f"Model '{model_name}' is configured with supports_tools=false "
                    "but tools were requested."
                )
            primary_bound = primary.bind_tools(list(tools))
        else:
            primary_bound = primary

        # Attach usage tracking to each individual model branch. This is
        # deliberately done BEFORE with_fallbacks(), so a fallback call is
        # attributed to the fallback model rather than to the primary model.
        
        # 3. Attach usage tracking to the primary
        primary_runnable: Runnable[Any, Any] = primary_bound.with_config(
            callbacks=[
                _UsageCallback(self._usage, model_name, usage_context),
                # DumpPayloadCallback(),
            ],
            tags=[f"llm-model:{model_name}"]
            + ([f"usage:{usage_context}"] if usage_context else []),
        )
        
        # 4. Adapt this primary_runnable as per selected model quirks
        adapter = self._adapters.get(cfg.adapter)

        primary_runnable = adapter.adapt(
            primary_runnable,
            model_name=model_name,
            config=cfg,
        )

        # 5. Build fallbacks recursively
        fallbacks: list[Runnable[Any, Any]] = []
        for fallback_name in cfg.fallbacks:
            fallback = self._assemble(
                fallback_name,
                tools,
                usage_context=usage_context,
                stack=(*stack, model_name),
            )
            fallbacks.append(fallback)

        # 6. Compose the already-prepared branches
        if fallbacks:
            return primary_runnable.with_fallbacks(fallbacks)
        
        return primary_runnable

    def _get_base_model(self, model_name: str) -> ChatLiteLLM:
        with self._lock:
            cached = self._model_cache.get(model_name)
            if cached is not None:
                return cached

            model = self._build(model_name)
            self._model_cache[model_name] = model
            return model

    def _model_cfg(self, model_name: str) -> ModelConfig:
        try:
            return self._config.models[model_name]
        except KeyError:
            raise KeyError(
                f"Unknown model '{model_name}'. Defined models: {sorted(self._config.models)}"
            ) from None

    def _build(self, model_name: str) -> ChatLiteLLM:
        cfg = self._model_cfg(model_name)

        direct = {k: v for k, v in cfg.params.items() if k in _DIRECT_PARAMS}
        extra = {k: v for k, v in cfg.params.items() if k not in _DIRECT_PARAMS}

        kwargs: dict[str, Any] = {
            "model": cfg.model,
            **direct,
        }

        api_key = get_api_key(cfg)
        if api_key:
            kwargs["api_key"] = api_key

        if cfg.api_base:
            kwargs["api_base"] = cfg.api_base

        if extra:
            kwargs["model_kwargs"] = extra

        log.info(
            "Building ChatLiteLLM '%s' -> %s",
            model_name,
            cfg.model,
        )

        return ChatLiteLLM(**kwargs)


    @staticmethod
    def _tool_key(tool: Any) -> str:
        # OpenAI-format dict
        if isinstance(tool, dict):
            fn = tool.get("function") or {}
            name = fn.get("name") or tool.get("name")
            if name:
                return str(name)
            return repr(tool)

        # Tools object / BaseTool / callable
        name = getattr(tool, "name", None)
        if name:
            return str(name)

        qualname = getattr(tool, "__qualname__", None)
        module = getattr(tool, "__module__", None)
        if qualname:
            return f"{module or ''}:{qualname}"

        return repr(tool)


