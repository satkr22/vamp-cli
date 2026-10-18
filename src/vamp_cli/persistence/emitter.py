"""
Translate LangChain `astream_events(version='v2')` frames into events
appended to an EventLog.

Covers:
    assistant_message   from on_chat_model_start/stream/end
    tool_result         from on_tool_start/end/error

Design rules:
    - The emitter is a passive translator. It never mutates LangGraph state,
      never raises past its own boundary, and never blocks the display path.
    - All appends go through EventLog.append, which is already lock-serialized.
    - Streaming chunks are accumulated locally using AIMessageChunk.__add__,
      which correctly merges content, tool_call_chunks, usage, and
      additional_kwargs (incl. reasoning_content for reasoning models).
"""

from __future__ import annotations

import sys
import time
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
)

from vamp_cli.persistence.event_log import EventLog


# helpers
def _content_to_str(content: Any) -> str:
    """Normalize message content to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # Anthropic / OpenAI content blocks
                if block.get("type") in ("text", "output_text") and "text" in block:
                    parts.append(str(block["text"]))
                elif block.get("type") == "thinking" and "thinking" in block:
                    parts.append(str(block["thinking"]))
                # other block types (image, tool_use, etc) skipped
        return "".join(parts)
    return str(content)


def _extract_reasoning(msg: Any) -> str:
    """Pull reasoning text out of whatever field the provider used."""
    if msg is None:
        return ""
    direct = getattr(msg, "reasoning_content", None)
    if isinstance(direct, str) and direct:
        return direct
    extra = getattr(msg, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "reasoning", "thinking"):
        val = extra.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _extract_tool_calls(msg: Any) -> list[dict]:
    """
    Return normalized tool_calls as plain dicts.

    LangChain normalizes them into {name, args, id, type} on AIMessage.
    Chunks may instead expose `tool_call_chunks`, which merge into the
    same shape as a best-effort.
    """
    if msg is None:
        return []

    calls = getattr(msg, "tool_calls", None)
    if calls:
        out: list[dict] = []
        for tc in calls:
            if isinstance(tc, dict):
                out.append({
                    "id":   tc.get("id"),
                    "name": tc.get("name"),
                    "args": tc.get("args") or {},
                })
            else:
                out.append({
                    "id":   getattr(tc, "id", None),
                    "name": getattr(tc, "name", None),
                    "args": getattr(tc, "args", {}) or {},
                })
        return out

    chunks = getattr(msg, "tool_call_chunks", None) or []
    merged: dict[int, dict] = {}
    for i, c in enumerate(chunks):
        idx = c.get("index", i) if isinstance(c, dict) else i
        bucket = merged.setdefault(idx, {"id": None, "name": None, "args_str": ""})
        if isinstance(c, dict):
            bucket["id"] = bucket["id"] or c.get("id")
            bucket["name"] = bucket["name"] or c.get("name")
            if c.get("args"):
                bucket["args_str"] += str(c["args"])
    out2: list[dict] = []
    for idx in sorted(merged):
        b = merged[idx]
        args: Any = {}
        if b["args_str"]:
            try:
                import json
                args = json.loads(b["args_str"])
            except Exception:
                args = {"_raw": b["args_str"]}
        out2.append({"id": b["id"], "name": b["name"], "args": args})
    return out2


def _unwrap_chat_output(output: Any) -> BaseMessage | None:
    """
    on_chat_model_end.data.output is either an AIMessage/AIMessageChunk
    or an LLMResult wrapping one. Unwrap both.
    """
    if output is None:
        return None
    if hasattr(output, "generations"):
        gens = getattr(output, "generations") or []
        if gens and gens[0]:
            return getattr(gens[0][0], "message", None)
        return None
    if isinstance(output, (AIMessage, AIMessageChunk)):
        return output
    return output if isinstance(output, BaseMessage) else None


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    # keep head and tail so both the context and the error message survive
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n...[{len(text) - limit} bytes elided]...\n{text[-tail:]}", True


# EventEmitter
class EventEmitter:
    """
    Consumes astream_events frames and appends persistence events.

    Usage:
        emitter = EventEmitter(log)
        async for event in graph.astream_events(payload, config=cfg, version="v2"):
            await emitter.consume(event)
                display logic unchanged
    """

    def __init__(self, log: EventLog, *, tool_output_limit: int = 16_000) -> None:
        self.log = log
        self._tool_output_limit = tool_output_limit

        # run_id -> in-flight chat state
        self._chat: dict[str, dict[str, Any]] = {}

        # run_id -> in-flight tool state
        self._tools: dict[str, dict[str, Any]] = {}

        # tool_call_id -> originating node ("coder" | "planner" | "tool")
        # Populated when an assistant_message carrying tool_calls is emitted,
        # consumed when the matching tool_result is emitted.
        self._tool_call_to_node: dict[str, str | None] = {}
        
        # last input_tokens reported by any model call in this turn
        self.last_input_tokens: int = 0

    # public entry point 
    async def consume(self, event: dict) -> None:
        """Route one astream_events frame. Never raises."""
        kind = event.get("event", "")
        handler = _HANDLERS.get(kind)
        if handler is None:
            return
        try:
            await handler(self, event)
        except Exception as e:
            # Persistence must never break the run. Report and move on.
            print(f"[persistence] failed to emit '{kind}': {e!r}", file=sys.stderr)

    # chat model 
    async def _on_chat_start(self, event: dict) -> None:
        run_id = event["run_id"]
        self._chat[run_id] = {
            "chunk": None,          # accumulated AIMessageChunk
            "meta": event.get("metadata") or {},
            "started_at": time.time(),
        }

    async def _on_chat_stream(self, event: dict) -> None:
        run_id = event.get("run_id")
        buf = self._chat.get(run_id) # type: ignore
        if buf is None:
            return
        chunk = (event.get("data") or {}).get("chunk")
        if chunk is None:
            return
        try:
            buf["chunk"] = chunk if buf["chunk"] is None else buf["chunk"] + chunk
        except Exception:
            # merge failure is rare and non-fatal - keep what we have
            pass

    async def _on_chat_end(self, event: dict) -> None:
        run_id = event.get("run_id")
        buf = self._chat.pop(run_id, None) #type: ignore
        if buf is None:
            return

        accumulated: AIMessageChunk | None = buf["chunk"]
        final_msg = _unwrap_chat_output((event.get("data") or {}).get("output"))

        # Prefer the accumulated stream (it has reasoning_content that the
        # final message often drops). Supplement with final_msg when needed.
        content = _content_to_str(getattr(accumulated, "content", "")) if accumulated else ""
        reasoning = _extract_reasoning(accumulated) if accumulated else ""
        tool_calls = _extract_tool_calls(accumulated) if accumulated else []

        if final_msg is not None:
            if not content:
                content = _content_to_str(getattr(final_msg, "content", ""))
            if not reasoning:
                reasoning = _extract_reasoning(final_msg)
            if not tool_calls:
                tool_calls = _extract_tool_calls(final_msg)

        usage = None
        if final_msg is not None:
            um = getattr(final_msg, "usage_metadata", None)
            if um:
                usage = dict(um)
        
        if usage and isinstance(usage.get("input_tokens"), int):
            self.last_input_tokens = usage["input_tokens"]

        meta = buf.get("meta") or {}
        node = meta.get("langgraph_node")

        # Remember which node each tool call came from so tool_result
        # events can carry the same attribution.
        for tc in tool_calls:
            tid = tc.get("id")
            if tid:
                self._tool_call_to_node[tid] = node

        self.log.emit("assistant_message", {
            "node": node,
            "content": content,
            "reasoning_content": reasoning or None,
            "tool_calls": tool_calls,
            "usage": usage,
            "model": meta.get("ls_model_name"),
            "duration_ms": int((time.time() - buf["started_at"]) * 1000),
        })

    # tools ---------------------------------------------------------
    async def _on_tool_start(self, event: dict) -> None:
        run_id = event.get("run_id")
        data = event.get("data") or {}
        meta = event.get("metadata") or {}

        # LangChain names the tool in `event["name"]`, the args in data["input"].
        self._tools[run_id] = { # type: ignore
            "tool_name": event.get("name"),
            "tool_call_id": data.get("tool_call_id"),
            "input": data.get("input"),
            "node_hint": meta.get("langgraph_node"),
            "started_at": time.time(),
        }

    async def _on_tool_end(self, event: dict) -> None:
        run_id = event.get("run_id")
        meta = self._tools.pop(run_id, {}) # type: ignore
        output = (event.get("data") or {}).get("output")

        tool_call_id = meta.get("tool_call_id")
        tool_name = meta.get("tool_name")
        status = "success"
        content = ""

        if isinstance(output, ToolMessage):
            tool_call_id = getattr(output, "tool_call_id", None) or tool_call_id
            tool_name = getattr(output, "name", None) or tool_name
            content = _content_to_str(getattr(output, "content", ""))
            status = getattr(output, "status", None) or "success"
        else:
            content = _content_to_str(output)

        text, truncated = _truncate(content, self._tool_output_limit)

        # Prefer the model-node when the assistant emitted the call. 
        # Fall back to langgraph_node from the tool event,
        # which will usually be "execute_tools" and thus less useful.
        node = self._tool_call_to_node.get(tool_call_id) or meta.get("node_hint")

        self.log.emit("tool_result", {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "node": node,
            "status": status,
            "content": text,
            "truncated": truncated,
            "content_len": len(content),
            "duration_ms": int((time.time() - meta.get("started_at", time.time())) * 1000),
        })

    async def _on_tool_error(self, event: dict) -> None:
        run_id = event.get("run_id")
        meta = self._tools.pop(run_id, {}) # type: ignore
        err = (event.get("data") or {}).get("error")

        tool_call_id = meta.get("tool_call_id")
        node = self._tool_call_to_node.get(tool_call_id) or meta.get("node_hint")

        self.log.emit("tool_result", {
            "tool_call_id": tool_call_id,
            "tool_name":    meta.get("tool_name"),
            "node":         node,
            "status":       "error",
            "content":      f"{type(err).__name__}: {err}" if err else "unknown error",
            "truncated":    False,
            "content_len":  0,
            "duration_ms":  int((time.time() - meta.get("started_at", time.time())) * 1000),
        })
        
    def emit_tool_result(
        self,
        *,
        tool_call_id: str,
        tool_name:    str,
        node:         str | None,
        status:       str,
        content:      str,
        duration_ms:  int,
    ) -> None:
        """Public hook for callers that bypass LangChain's callback stream."""
        text, truncated = _truncate(content or "", self._tool_output_limit)
        self.log.emit("tool_result", {
            "tool_call_id": tool_call_id,
            "tool_name":    tool_name,
            "node":         node,
            "status":       status,
            "content":      text,
            "truncated":    truncated,
            "content_len":  len(content or ""),
            "duration_ms":  duration_ms,
        })


# dispatch table - populated after the class to keep the definition clean
_HANDLERS = {
    "on_chat_model_start":  EventEmitter._on_chat_start,
    "on_chat_model_stream": EventEmitter._on_chat_stream,
    "on_chat_model_end":    EventEmitter._on_chat_end,
    "on_tool_start":        EventEmitter._on_tool_start,
    "on_tool_end":          EventEmitter._on_tool_end,
    "on_tool_error":        EventEmitter._on_tool_error,
}