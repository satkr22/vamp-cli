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

import os
import json
import time
from pathlib import Path
import concurrent.futures
from typing import Any
from dataclasses import dataclass, field
try:
    import orjson as fast_json
except ImportError:
    fast_json = json 

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from vamp_cli.agent.instructions import TODO_INSTRUCTIONS

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
            executed_mode = d.get("to", executed_mode)
            is_session_started = True
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
def reconstruct_agent_state(
    events: list[dict],
    system_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Rebuild an AgentState-shaped dict from the JSONL.
    Only used when the SQLite checkpointer is missing or empty. Lossy:
    - reasoning_content preserved; usage / metadata dropped.
    - Tool-call routing by node name (falls back to 'coder_messages').
    - _pending_* pipeline fields reset to safe defaults.
    """
    coder_msgs:   list[BaseMessage] = []
    planner_msgs: list[BaseMessage] = []
    
    if system_prompts:
        if system_prompts.get("coder"):
            coder_msgs.append(SystemMessage(
                content=system_prompts["coder"], id="coder_system_prompt",
            ))
            coder_msgs.append(
                SystemMessage(content=TODO_INSTRUCTIONS)
            )
        if system_prompts.get("planner"):
            planner_msgs.append(SystemMessage(
                content=system_prompts["planner"], id="planner_system_prompt",
            ))
    
    shadowed, _ = build_shadow_map(events)
    
    task: str | None = None
    mode = "normal"
    plan: str | None = None
    todo: list[dict] = []
    iteration = 0

    def _channel_for(node: str | None) -> list[BaseMessage]:
        return planner_msgs if node == "planner" else coder_msgs

    for ev in events:
        u = ev.get("uuid")
        t = ev.get("type")
        d = ev.get("data") or {}

        # compaction event: inject summary, never process as a message
        if t == "compaction":
            summary = (d.get("summary") or "").strip()
            if summary:
                marker = SystemMessage(
                    content=f"[COMPACTED HISTORY - summary of earlier turns]\n{summary}",
                    id=f"compaction_{u}",
                )
                channel = d.get("channel", "coder_messages")
                if channel == "planner_messages":
                    planner_msgs.append(marker)
                elif channel == "both":
                    coder_msgs.append(marker)
                    planner_msgs.append(marker)
                else:
                    coder_msgs.append(marker)
            continue
        
        # shadowed event: skip entirely (already in a summary)
        if u in shadowed:
            continue
        
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
    thread_id: str = ""

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
    prompts = (session_start_ev or {}).get("data", {}).get("system_prompts")

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
    resumed.thread_id = current_thread_id(
            events,
            default=session_config.get("configurable", {}).get("thread_id") or session_id,
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
            resumed.last_state = reconstruct_agent_state(events, system_prompts=prompts)
            resumed.checkpoint_source = "reconstructed"
        except Exception as e:
            resumed.warnings.append(f"reconstruction failed: {e!r}")
            resumed.last_state = None
            resumed.checkpoint_source = "missing"
    else:
        resumed.checkpoint_source = "missing"

    return resumed

def _extract_tail_timestamp(f, file_size: int, chunk_size: int = 4096) -> Any | None:
    """Seek to the end of the file and find the last timestamp without reading the whole file."""
    if file_size == 0:
        return None

    read_size = min(file_size, chunk_size)
    f.seek(file_size - read_size, os.SEEK_SET)
    chunk = f.read().splitlines()

    # Iterate backwards through lines in the tail chunk
    for line in reversed(chunk):
        if not line.strip() or b'"timestamp"' not in line:
            continue
        try:
            ev = fast_json.loads(line)
            if ts := ev.get("timestamp"):
                return ts
        except Exception:
            continue
    return None

def _parse_session_file(entry: os.DirEntry) -> dict[str, Any] | None:
    session_id = entry.name.removesuffix(".jsonl")
    size_bytes = entry.stat().st_size

    if size_bytes == 0:
        return {
            "session_id": session_id,
            "started_at": None,
            "last_event_at": None,
            "first_message": None,
            "size_bytes": 0,
        }

    first_user = None
    started_at = None
    last_ts = None

    try:
        # Read in binary mode to avoid text-decoding overhead during seeks
        with open(entry.path, "rb") as f:
            # 1. Head scan: read until we find started_at and first_user
            lines_checked = 0
            for raw_line in f:
                lines_checked += 1
                if not raw_line.strip():
                    continue

                # Fast string checks before running JSON parser
                is_start = b'"session_start"' in raw_line
                is_user = b'"user_message"' in raw_line

                if is_start or is_user:
                    try:
                        ev = fast_json.loads(raw_line)
                    except Exception:
                        continue

                    if is_start and started_at is None and ev.get("type") == "session_start":
                        started_at = ev.get("timestamp")

                    if is_user and first_user is None and ev.get("type") == "user_message":
                        first_user = (ev.get("data") or {}).get("content")

                # Early exit once both fields are located or threshold reached
                if started_at is not None and first_user is not None:
                    break
                if lines_checked >= 100:
                    break

            # 2. Tail scan: jump straight to the end for the last timestamp
            last_ts = _extract_tail_timestamp(f, size_bytes)

    except OSError:
        return None

    return {
        "session_id": session_id,
        "started_at": started_at,
        "last_event_at": last_ts,
        "first_message": first_user,
        "size_bytes": size_bytes,
    }


# CLI helper: list sessions in a workspace
def list_sessions(sessions_dir: Path, max_workers: int = 8) -> list[dict[str, Any]]:
    
    """Scan the workspace session directory efficiently using head/tail seeking and thread concurrency."""
    if not sessions_dir.exists():
        return []
    
    # os.scandir avoids creating heavy Path objects and caches stat results on Linux/macOS/Windows
    try:
        entries = [
            e for e in os.scandir(sessions_dir)
            if e.is_file() and e.name.endswith(".jsonl")
        ]
    except OSError:
        return []

    entries.sort(key=lambda e: e.name, reverse=True)
    if not entries:
        return []

    # Parallelize file reading across threads (pure I/O bound)
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(entries))) as executor:
        for res in executor.map(_parse_session_file, entries):
            if res is not None:
                results.append(res)

    return results

def current_thread_id(events: list[dict], default: str) -> str:
    """Walk events; the last fork event's thread_id wins."""
    tid = default
    for ev in events:
        if ev.get("type") == "fork":
            t = (ev.get("data") or {}).get("thread_id")
            if t:
                tid = t
    return tid

def list_user_messages(events: list[dict]) -> list[dict]:
    """
    Enumerate user messages in the chain, tagging each with a `shadowed`
    flag if it has been compacted away by a later compaction event.
    """
    shadowed, _ = build_shadow_map(events)

    out: list[dict] = []
    idx = 0
    for ev in events:
        if ev.get("type") != "user_message":
            continue
        idx += 1
        d = ev.get("data") or {}
        out.append({
            "index": idx,
            "uuid": ev.get("uuid"),
            "parent_uuid": ev.get("parentUuid"),
            "content": (d.get("content") or "").strip(),
            "mode": d.get("mode"),
            "timestamp": ev.get("timestamp"),
            "shadowed": ev.get("uuid") in shadowed,
        })
    return out


def list_leaves(events: list[dict]) -> list[str]:
    """All uuids that never appear as a parent - i.e. current branch tips."""
    used_as_parent = {ev.get("parentUuid") for ev in events if ev.get("parentUuid")}
    leaves = [ev["uuid"] for ev in events if ev.get("uuid") and ev["uuid"] not in used_as_parent]
    # uuid7 sorts lexicographically by creation time
    return sorted(leaves, reverse=True)

def load_all_events(sessions_dir: Path, session_id: str) -> list[dict]:
    """Read every event in the file (all branches), in file order."""
    path = sessions_dir / f"{session_id}.jsonl"
    if not path.exists():
        return []
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events

def build_shadow_map(events: list[dict]) -> tuple[set[str], dict[str, dict]]:
    """
    Return (shadowed_uuids, compaction_by_uuid).

    shadowed_uuids: every uuid covered by some compaction event on this chain.
    compaction_by_uuid: compaction event uuid -> its data payload.
    """
    shadowed: set[str] = set()
    compaction_by_uuid: dict[str, dict] = {}
    for ev in events:
        if ev.get("type") != "compaction":
            continue
        d = ev.get("data") or {}
        for u in d.get("range_uuids") or []:
            shadowed.add(u)
        compaction_by_uuid[ev["uuid"]] = d
    return shadowed, compaction_by_uuid



def _repair_orphaned_tool_calls(msgs: list[BaseMessage]) -> None:
    """Ensure every AIMessage.tool_calls entry has a matching ToolMessage."""
    call_ids = set()
    result_ids = set()
    for m in msgs:
        if isinstance(m, AIMessage):
            for tc in (getattr(m, "tool_calls", None) or []):
                if tc.get("id"):
                    call_ids.add(tc["id"])
        elif isinstance(m, ToolMessage):
            if getattr(m, "tool_call_id", None):
                result_ids.add(m.tool_call_id)

    orphaned = call_ids - result_ids
    if not orphaned:
        return

    rebuilt: list[BaseMessage] = []
    for m in msgs:
        rebuilt.append(m)
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                if tc.get("id") in orphaned:
                    rebuilt.append(ToolMessage(
                        content=(
                            "[Tool execution was interrupted before completion — "
                            "no result was recorded. Do not assume the tool ran.]"
                        ),
                        tool_call_id=tc["id"],
                        name=tc.get("name") or "unknown",
                        status="error",
                    ))
    msgs[:] = rebuilt