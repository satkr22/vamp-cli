from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable
from langchain_litellm import ChatLiteLLM

from vamp_cli.config.loader import get_api_key
from vamp_cli.config.schema import AppConfig, ModelCapabilities, ModelConfig

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


@dataclass(frozen=True)
class UsageSnapshot:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class UsageTracker:
    """Thread-safe in-process usage accumulator.

    It intentionally lives in router.py for now. It can later be extracted into
    llm/usage.py without changing ModelRouter's public interface.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: dict[str, UsageSnapshot] = {}

    def record(
        self,
        model_name: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        with self._lock:
            previous = self._totals.get(model_name, UsageSnapshot())
            self._totals[model_name] = UsageSnapshot(
                calls=previous.calls + 1,
                input_tokens=previous.input_tokens + input_tokens,
                output_tokens=previous.output_tokens + output_tokens,
                total_tokens=previous.total_tokens + total_tokens,
                cost_usd=previous.cost_usd + cost_usd,
            )

    def snapshot(self) -> dict[str, UsageSnapshot]:
        with self._lock:
            return dict(self._totals)


class _UsageCallback(BaseCallbackHandler):
    def __init__(
        self,
        tracker: UsageTracker,
        model_name: str,
        usage_context: str | None,
    ) -> None:
        self._tracker = tracker
        self._model_name = model_name
        self._usage_context = usage_context

    def on_llm_end(self, response: LLMResult, **_: Any) -> None:
        input_tokens, output_tokens, total_tokens, cost = _extract_usage(response)

        key = (
            f"{self._usage_context}::{self._model_name}"
            if self._usage_context
            else self._model_name
        )

        self._tracker.record(
            key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
        )


def _extract_usage(response: LLMResult) -> tuple[int, int, int, float]:
    """Best-effort extraction across common LangChain/LiteLLM response shapes."""

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cost = 0.0

    llm_output = response.llm_output or {}
    token_usage = (
        llm_output.get("token_usage")
        or llm_output.get("usage")
        or llm_output.get("usage_metadata")
        or {}
    )

    input_tokens = _as_int(
        token_usage.get("input_tokens")
        or token_usage.get("prompt_tokens")
        or token_usage.get("inputTokenCount")
    )
    output_tokens = _as_int(
        token_usage.get("output_tokens")
        or token_usage.get("completion_tokens")
        or token_usage.get("outputTokenCount")
    )
    total_tokens = _as_int(token_usage.get("total_tokens"))
    cost = _as_float(
        llm_output.get("response_cost")
        or llm_output.get("cost")
        or llm_output.get("total_cost")
    )

    # Some LangChain integrations place usage on the returned AIMessage.
    for generation_group in response.generations:
        for generation in generation_group:
            message = getattr(generation, "message", None)
            if message is None:
                continue

            usage = getattr(message, "usage_metadata", None) or {}
            if usage:
                input_tokens = input_tokens or _as_int(usage.get("input_tokens"))
                output_tokens = output_tokens or _as_int(usage.get("output_tokens"))
                total_tokens = total_tokens or _as_int(usage.get("total_tokens"))

            metadata = getattr(message, "response_metadata", None) or {}
            nested_usage = (
                metadata.get("token_usage")
                or metadata.get("usage")
                or metadata.get("usage_metadata")
                or {}
            )
            input_tokens = input_tokens or _as_int(
                nested_usage.get("input_tokens")
                or nested_usage.get("prompt_tokens")
            )
            output_tokens = output_tokens or _as_int(
                nested_usage.get("output_tokens")
                or nested_usage.get("completion_tokens")
            )
            total_tokens = total_tokens or _as_int(nested_usage.get("total_tokens"))
            cost = cost or _as_float(
                metadata.get("response_cost")
                or metadata.get("cost")
                or metadata.get("total_cost")
            )

    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens

    return input_tokens, output_tokens, total_tokens, cost


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


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

    def __init__(self, config: AppConfig) -> None:
        self._config = config
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
        primary = self._get_base_model(model_name)

        # IMPORTANT: bind tools before wrapping in with_fallbacks().
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
        primary_runnable: Runnable[Any, Any] = primary_bound.with_config(
            callbacks=[_UsageCallback(self._usage, model_name, usage_context)],
            tags=[f"llm-model:{model_name}"]
            + ([f"usage:{usage_context}"] if usage_context else []),
        )

        fallbacks: list[Runnable[Any, Any]] = []
        for fallback_name in cfg.fallbacks:
            fallback = self._assemble(
                fallback_name,
                tools,
                usage_context=usage_context,
                stack=(*stack, model_name),
            )
            fallbacks.append(fallback)

        if not fallbacks:
            return primary_runnable

        return primary_runnable.with_fallbacks(fallbacks)

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
        """Stable-enough cache identity for common LangChain tool objects."""

        name = getattr(tool, "name", None)
        if name:
            return str(name)

        qualname = getattr(tool, "__qualname__", None)
        module = getattr(tool, "__module__", None)
        if qualname:
            return f"{module or ''}:{qualname}"

        return repr(tool)
