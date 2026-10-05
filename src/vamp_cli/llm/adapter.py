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


class PassThroughAdapter:
    """Default adapter for models with no special handling."""

    def adapt(
        self,
        runnable: Runnable[Any, Any],
        *,
        model_name: str,
        config: ModelConfig,
    ) -> Runnable[Any, Any]:
        return runnable