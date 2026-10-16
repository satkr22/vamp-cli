"""
Rehydrate a session from its JSONL event log + LangGraph checkpointer.

Responsibilities:
    - Walk the append-only chain from session_start to the current leaf.
    - Derive CLI-side display state (mode, "have we run yet").
    - Detect a parked interrupt (request without response).
    - Load authoritative graph state from the checkpointer.
    - Fall back to reconstructing graph state from the log if the
      checkpointer is missing or empty.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig


# Chain loading
def load_chain(
    sessions_dir: Path,
    session_id: str,
    *,
    leaf_uuid: str | None = None,
) -> list[dict]:
    """
    Return events in chain order (oldest first).

    If leaf_uuid is None, read the .head sidecar; if that's missing, find
    the leaf by scanning. Walk parentUuid backward from the leaf to the
    session_start event, then reverse.

    Malformed lines are skipped silently - the log is never assumed clean.
    """
    path = sessions_dir / f"{session_id}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"session log not found: {path}")

    by_uuid: dict[str, dict] = {}
    order: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            u = ev.get("uuid")
            if u:
                by_uuid[u] = ev
                order.append(u)

    if not order:
        return []

    if leaf_uuid is None:
        head_path = sessions_dir / f"{session_id}.head"
        if head_path.exists():
            leaf_uuid = head_path.read_text(encoding="utf-8").strip() or None

    if leaf_uuid is None or leaf_uuid not in by_uuid:
        # No sidecar / stale sidecar - pick newest leaf.
        parents = {ev.get("parentUuid") for ev in by_uuid.values() if ev.get("parentUuid")}
        leaves = [u for u in order if u not in parents]
        if not leaves:
            return []
        leaf_uuid = max(leaves)

    chain: list[dict] = []
    cursor: str | None = leaf_uuid
    while cursor is not None:
        ev = by_uuid.get(cursor)
        if ev is None:
            break
        chain.append(ev)
        cursor = ev.get("parentUuid")
    chain.reverse()
    return chain



# Display-state derivation
def derive_display_state(events: list[dict]) -> dict[str, Any]:
    """
    Replay events into the small amount of state the CLI loop cares about.
    - selected_mode: last mode_switch.to, default 'normal'
    - executed_mode: the mode of the most recent user_message
    - is_session_started: True once any turn has been sent
    """
    selected_mode = "normal"
    executed_mode = "normal"
    is_session_started = False

    for ev in events:
        t = ev.get("type")
        d = ev.get("data") or {}
        if t == "mode_switch":
            selected_mode = d.get("to", selected_mode)
        elif t == "user_message":
            executed_mode = d.get("mode", executed_mode)
            is_session_started = True

    return {
        "selected_mode":      selected_mode,
        "executed_mode":      executed_mode,
        "is_session_started": is_session_started,
    }


def detect_pending_interrupt(events: list[dict]) -> dict | None:
    """
    Return the payload of an interrupt_request that has no matching
    interrupt_response after it. Returns None if the session is not
    parked at an interrupt.
    """
    pending: dict | None = None
    for ev in events:
        t = ev.get("type")
        if t == "interrupt_request":
            d = ev.get("data") or {}
            pending = {
                "kind":    d.get("kind"),
                "payload": d.get("payload") or {},
                "event_uuid": ev.get("uuid"),
            }
        elif t == "interrupt_response":
            pending = None
        elif t in ("session_end",):
            # A cleanly-ended session can't be parked at an interrupt.
            pending = None
    return pending


# Best-effort AgentState reconstruction
def reconstruct_agent_state(events: list[dict]) -> dict[str, Any]:
    """
    Rebuild an AgentState-shaped dict from the JSONL.
    Only used when the SQLite checkpointer is missing or empty. Lossy:
    - reasoning_content preserved; usage / metadata dropped.
    - Tool-call routing by node name (falls back to 'coder_messages').
    - _pending_* pipeline fields reset to safe defaults.
    """
    coder_msgs:   list[BaseMessage] = []
    planner_msgs: list[BaseMessage] = []
    task: str | None = None
    mode = "normal"
    plan: str | None = None
    todo: list[dict] = []
    iteration = 0

    def _channel_for(node: str | None) -> list[BaseMessage]:
        return planner_msgs if node == "planner" else coder_msgs

    for ev in events:
        t = ev.get("type")
        d = ev.get("data") or {}

        if t == "user_message":
            mode = d.get("mode", mode)
            msg = HumanMessage(content=d.get("content", ""))
            if mode == "plan":
                planner_msgs.append(msg)
            else:
                coder_msgs.append(msg)
            task = d.get("content", task)

        elif t == "assistant_message":
            node = d.get("node")
            tool_calls: list[dict] = []
            for tc in d.get("tool_calls") or []:
                tool_calls.append({
                    "id":   tc.get("id") or "",
                    "name": tc.get("name") or "",
                    "args": tc.get("args") or {},
                    "type": "tool_call",
                })
            ai = AIMessage(
                content=d.get("content", "") or "",
                tool_calls=tool_calls,
            )
            if d.get("reasoning_content"):
                ai.additional_kwargs = {"reasoning_content": d["reasoning_content"]}
            _channel_for(node).append(ai)
            iteration += 1

        elif t == "tool_result":
            node = d.get("node")
            tm = ToolMessage(
                content=d.get("content", "") or "",
                tool_call_id=d.get("tool_call_id") or "",
                name=d.get("tool_name") or "",
                status=d.get("status") or "success",
            )
            _channel_for(node).append(tm)

            # submit_plan produces a plan text we can recover from the
            # tool_result payload - this is what makes plan mode work on
            # a reconstructed session.
            if d.get("tool_name") == "submit_plan":
                raw = d.get("content") or ""
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(parsed, dict) and "plan" in parsed:
                        plan = parsed["plan"]
                except (json.JSONDecodeError, TypeError):
                    pass

        elif t == "todo_update":
            todo = d.get("todos") or d.get("todo") or todo

        elif t == "mode_switch":
            mode = d.get("to", mode)

        elif t == "plan_submitted":
            plan = d.get("plan", plan)

    return {
        "coder_messages": coder_msgs,
        "planner_messages": planner_msgs,
        "task": task,
        "mode": mode,
        "plan": plan,
        "todo": todo,
        "iteration": iteration,
        "requires_execution": False,
        "_pending_tool_channel": None,
        "_pending_tool_calls": [],
        "_dangerous_tool_call_ids": [],
        "_approved_tool_call_ids": [],
        "_denied_tool_call_ids": [],
    }

# ResumedSession
@dataclass
class ResumedSession:
    session_id: str
    events: list[dict]
    session_start: dict | None

    # CLI display state
    selected_mode: str = "normal"
    executed_mode: str = "normal"
    is_session_started: bool = False

    # Graph state
    last_state: dict | None = None
    checkpoint_thread_id: str | None = None
    checkpoint_source: str = "sqlite"     # "sqlite" | "reconstructed" | "missing"

    # Parked interrupt
    pending_interrupt: dict | None = None

    # Any warnings worth surfacing to the user
    warnings: list[str] = field(default_factory=list)


# Public entry
async def load_session(
    *,
    sessions_dir: Path,
    session_id: str,
    graph,
    session_config: dict | RunnableConfig,
    reconstruct_if_missing: bool = True,
) -> ResumedSession:
    """
    Load a session: event chain + CLI state + graph state + interrupt.
    """
    events = load_chain(sessions_dir, session_id)
    if not events:
        raise ValueError(f"session {session_id} has no events")

    session_start_ev = next(
        (e for e in events if e.get("type") == "session_start"), None
    )

    display = derive_display_state(events)
    pending = detect_pending_interrupt(events)

    resumed = ResumedSession(
        session_id=session_id,
        events=events,
        session_start=session_start_ev,
        selected_mode=display["selected_mode"],
        executed_mode=display["executed_mode"],
        is_session_started=display["is_session_started"],
        pending_interrupt=pending,
        checkpoint_thread_id=session_config.get("configurable", {}).get("thread_id"),
    )

    # graph state via checkpointer (authoritative)
    try:
        snapshot = await graph.aget_state(session_config)
        values = snapshot.values if snapshot is not None else None
        # LangGraph returns an empty snapshot object rather than None when
        # the thread exists but has no checkpoint; treat empty as missing.
        if values and any(values.get(k) for k in ("coder_messages", "planner_messages")):
            resumed.last_state = values
            resumed.checkpoint_source = "sqlite"
            return resumed
    except Exception as e:
        resumed.warnings.append(f"checkpointer read failed: {e!r}")

    # fallback: rebuild from log
    if reconstruct_if_missing:
        resumed.warnings.append(
            "checkpointer had no state for this thread; "
            "reconstructed from event log (best-effort, lossy)."
        )
        try:
            resumed.last_state = reconstruct_agent_state(events)
            resumed.checkpoint_source = "reconstructed"
        except Exception as e:
            resumed.warnings.append(f"reconstruction failed: {e!r}")
            resumed.last_state = None
            resumed.checkpoint_source = "missing"
    else:
        resumed.checkpoint_source = "missing"

    return resumed



# CLI helper: list sessions in a workspace
def list_sessions(sessions_dir: Path) -> list[dict[str, Any]]:
    """
    Scan the workspace's session directory for a resume picker.
    Cheap: reads the first user_message from each file.
    """
    out: list[dict[str, Any]] = []
    if not sessions_dir.exists():
        return out

    for path in sorted(sessions_dir.glob("*.jsonl"), reverse=True):
        session_id = path.stem
        first_user = None
        started_at = None
        last_ts = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    last_ts = ev.get("timestamp") or last_ts
                    if ev.get("type") == "session_start" and started_at is None:
                        started_at = ev.get("timestamp")
                    if ev.get("type") == "user_message" and first_user is None:
                        first_user = (ev.get("data") or {}).get("content")
        except OSError:
            continue

        out.append({
            "session_id":   session_id,
            "started_at":   started_at,
            "last_event_at": last_ts,
            "first_message": first_user,
            "size_bytes":   path.stat().st_size,
        })
    return out