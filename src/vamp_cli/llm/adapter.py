from __future__ import annotations

from typing import Any, Protocol

from langchain_core.runnables import Runnable

from vamp_cli.config.schema import ModelConfig



class ModelAdapter(Protocol):
    def adapt(
        self,
        runnable: Runnable[Any, Any],
        *,
        model_name: str,
        config: ModelConfig,
    ) -> Runnable[Any, Any]:
        ...


class PassThroughAdapter(ModelAdapter):
    """Default adapter for models with no special handling."""

    def adapt(
        self,
        runnable: Runnable[Any, Any],
        *,
        model_name: str,
        config: ModelConfig,
    ) -> Runnable[Any, Any]:
        return runnable


class ReasoningAdapter(ModelAdapter):
    """Injects provider-specific reasoning/thinking parameters into the runnable."""

    def adapt(
        self,
        runnable: Runnable[Any, Any],
        *,
        model_name: str,
        config: ModelConfig,
    ) -> Runnable[Any, Any]:
        caps = config.capabilities
        
        # If the model configuration has reasoning enabled and specifies parameter structures
        if caps.supports_reasoning and caps.reasoning_param:
            kwargs = {caps.reasoning_param: caps.reasoning_value}
            return runnable.bind(**kwargs)
            
        return runnable
