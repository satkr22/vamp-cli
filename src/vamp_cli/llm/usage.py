from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


log = logging.getLogger(__name__)


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
