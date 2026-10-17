"""
Compaction: summarize a middle range of the current branch into a single
`compaction` event. Nothing is deleted; the range is shadowed on read.

Only one compaction is expected to be "live" at a time: each new
compaction's range starts at the first post-prompt user message and ends
just before the keep boundary, so it naturally subsumes prior
compactions that fall inside the range.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain_core.runnables import RunnableConfig

from vamp_cli.persistence.event_log import EventLog
from vamp_cli.persistence.loader import (
    reconstruct_agent_state, build_shadow_map,
)

DEFAULT_KEEP_TURNS = 4



# Range planning
def plan_compaction_range(
    chain: list[dict],
    *,
    keep_turns: int = DEFAULT_KEEP_TURNS,
) -> dict[str, Any] | None:
    """
    Decide which contiguous slice of the chain to summarize.

    Returns None if there is nothing compactable.
    """
    user_events = [e for e in chain if e.get("type") == "user_message"]
    if len(user_events) <= keep_turns:
        return None

    first_keep_index = len(user_events) - keep_turns
    first_to_compact = user_events[0]
    boundary_user    = user_events[first_keep_index]

    start_uuid = first_to_compact["uuid"]
    end_uuid   = boundary_user["parentUuid"]
    if not end_uuid:
        return None

    # Collect uuids in chain order between start and end (inclusive).
    collecting = False
    range_uuids: list[str] = []
    for e in chain:
        u = e.get("uuid")
        if u == start_uuid:
            collecting = True
        if collecting and u:
            range_uuids.append(u)
        if u == end_uuid:
            break

    if not range_uuids:
        return None

    range_set = set(range_uuids)
    range_events = [e for e in chain if e.get("uuid") in range_set]
    shadowed, _ = build_shadow_map(range_events)

    # Approximate token cost of the visible (non-shadowed) events.
    est_chars = 0
    for e in range_events:
        if e.get("uuid") in shadowed:
            continue
        d = e.get("data") or {}
        est_chars += len(d.get("content") or "")
        for tc in d.get("tool_calls") or []:
            est_chars += len(json.dumps(tc.get("args") or {}))
    est_tokens = max(1, est_chars // 4)

    return {
        "start_uuid":   start_uuid,
        "end_uuid":     end_uuid,
        "range_uuids":  range_uuids,
        "event_count":  len(range_events),
        "est_tokens":   est_tokens,
    }



# Summarization input + call
def build_summary_transcript(chain: list[dict], range_uuids: list[str]) -> str:
    """Render the range as a compact, LLM-friendly transcript."""
    range_set = set(range_uuids)
    lines: list[str] = []
    for e in chain:
        if e.get("uuid") not in range_set:
            continue
        t = e.get("type")
        d = e.get("data") or {}
        if t == "user_message":
            lines.append(f"USER: {d.get('content', '').strip()}")
        elif t == "assistant_message":
            content = (d.get("content") or "").strip()
            if content:
                lines.append(f"ASSISTANT: {content}")
            for tc in d.get("tool_calls") or []:
                args = json.dumps(tc.get("args") or {}, separators=(",", ":"))[:300]
                lines.append(f"  [tool_call {tc.get('name')} {args}]")
        # elif t == "tool_result":
        #     content = (d.get("content") or "")[:500].replace("\n", " ")
        #     lines.append(
        #         f"  [tool_result {d.get('tool_name')} "
        #         f"status={d.get('status')}] {content}"
        #     )
        elif t == "mode_switch":
            lines.append(f"[mode: {d.get('from')} -> {d.get('to')}]")
        elif t == "interrupt_request":
            lines.append(f"[interrupt requested: {d.get('kind')}]")
        elif t == "interrupt_response":
            verdict = "approved" if d.get("approved") else "denied"
            lines.append(f"[user {verdict} {d.get('kind')}]")
        elif t == "compaction":
            # prior compaction inside this range: include its summary
            summary = (d.get("summary") or "").strip()
            if summary:
                lines.append(f"[earlier summary] {summary}")
    return "\n".join(lines)


_SUMMARY_PROMPT = """\
You are a conversation summarizer for an AI coding assistant session.

Read the transcript and produce a summary that another AI coding agent
will use in place of the original messages. Capture:

- What the user asked for and what was accomplished
- Key decisions and concrete code changes (files, functions touched)
- Technologies, constraints, and any user preferences expressed
- Current state and open questions / next steps

Be concise but information-dense. Use short bullet points grouped by
topic. Do not editorialize, do not add interpretation. Preserve any
concrete identifiers (file paths, function names, command names,
error strings) verbatim.
"""


async def summarize_transcript(model, transcript: str) -> str:
    msgs = [
        SystemMessage(content=_SUMMARY_PROMPT),
        HumanMessage(content=f"TRANSCRIPT:\n\n{transcript}\n\nSUMMARY:"),
    ]
    response = await model.ainvoke(msgs)
    content = response.content
    return content if isinstance(content, str) else str(content)


# Checkpointer refresh
async def apply_compaction_to_checkpointer(
    graph,
    session_config: dict | RunnableConfig,
    new_state: dict,
) -> None:
    """
    Replace the message channels on the checkpointer with the compacted
    view. Uses REMOVE_ALL_MESSAGES to override add_messages' append
    reducer, so we end up with exactly the new lists.
    """
    updates = {
        "coder_messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)] + list(new_state["coder_messages"]),
        "planner_messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)] + list(new_state["planner_messages"]),
        "plan": new_state.get("plan"),
        "todo": new_state.get("todo") or [],
    }
    await graph.aupdate_state(session_config, updates)


# Public entry
async def compact_session(
    log: EventLog,
    *,
    chain: list[dict],
    graph,
    session_config: dict | RunnableConfig,
    model,
    keep_turns: int = DEFAULT_KEEP_TURNS,
    channel: str = "both",
) -> dict[str, Any]:
    """
    Compact the middle of the current branch.

    Returns a result dict; raises ValueError if there is nothing to do.
    """
    plan = plan_compaction_range(chain, keep_turns=keep_turns)
    if plan is None:
        raise ValueError(
            f"nothing to compact (need > {keep_turns} user turns)"
        )

    transcript = build_summary_transcript(chain, plan["range_uuids"])
    if not transcript.strip():
        raise ValueError("compaction range produced empty transcript")

    summary = await summarize_transcript(model, transcript)
    if not summary.strip():
        raise ValueError("summarizer returned empty summary")

    est_after = max(1, len(summary) // 4)

    await log.append("compaction", {
        "range_uuids": plan["range_uuids"],
        "start_uuid": plan["start_uuid"],
        "end_uuid": plan["end_uuid"],
        "channel": channel,
        "summary": summary,
        "event_count": plan["event_count"],
        "tokens_before": plan["est_tokens"],
        "tokens_after": est_after,
    })

    # Rebuild state from the updated chain (now shadowed) and push it
    # into the checkpointer so the next turn sees the compacted view.
    updated_chain = chain + [{
        "type": "compaction",
        "uuid": log.head_uuid,
        "parentUuid": chain[-1].get("uuid") if chain else None,
        "data": {
            "range_uuids": plan["range_uuids"],
            "summary": summary,
            "channel": channel,
        },
    }]
    start_ev = next(
        (e for e in chain if e.get("type") == "session_start"), None
    )
    prompts = (start_ev or {}).get("data", {}).get("system_prompts")
    new_state = reconstruct_agent_state(updated_chain, system_prompts=prompts)

    await apply_compaction_to_checkpointer(graph, session_config, new_state)

    return {
        "event_count":   plan["event_count"],
        "tokens_before": plan["est_tokens"],
        "tokens_after":  est_after,
        "summary":       summary,
        "new_state":     new_state,
    }



# Auto-compaction threshold
DEFAULT_COMPACT_THRESHOLD = 0.90


def get_model_context_window(model) -> int:
    """
    Best-effort context window for a LangChain chat model.

    Order of preference:
      1. model.profile['max_input_tokens']  (langchain-model-profiles)
      2. model.profile['context_window']
      3. model.get_context_window()          (newer LC)
      4. hardcoded fallback by model name
    """
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        for key in ("max_input_tokens", "context_window", "max_tokens"):
            val = profile.get(key)
            if isinstance(val, int) and val > 0:
                return val

    getter = getattr(model, "get_context_window", None)
    if callable(getter):
        try:
            val = getter()
            if isinstance(val, int) and val > 0:
                return val
        except Exception:
            pass

    name = (
        getattr(model, "model_name", None)
        or getattr(model, "model", None)
        or ""
    ).lower()

    fallbacks = {
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-4": 8_192,
        "gpt-3.5": 4_096,
        "claude-3-5-sonnet": 200_000,
        "claude-3-opus": 200_000,
        "claude-3-haiku": 200_000,
        "gemini-1.5-pro": 1_048_576,
        "gemini-1.5-flash":1_048_576,
        "deepseek": 64_000,
    }
    for prefix, size in fallbacks.items():
        if prefix in name:
            return size

    return 128_000  # conservative default


def should_compact(
    last_input_tokens: int,
    context_window: int,
    threshold: float = DEFAULT_COMPACT_THRESHOLD,
) -> bool:
    if context_window <= 0:
        return False
    return last_input_tokens / context_window >= threshold


async def auto_compact_if_needed(
    log: EventLog,
    *,
    chain: list[dict],
    graph,
    session_config: dict | RunnableConfig,
    model,
    last_input_tokens: int,
    context_window: int | None = None,
    threshold: float = DEFAULT_COMPACT_THRESHOLD,
    keep_turns: int = DEFAULT_KEEP_TURNS,
    channel: str = "both",
) -> dict | None:
    """
    Run compaction if the last prompt is at/above the threshold.

    Returns the compaction result dict, or None if no compaction was run.
    Call this synchronously from the turn boundary, before invoking the graph.
    """
    context_window = context_window or get_model_context_window(model)

    if not should_compact(last_input_tokens, context_window, threshold):
        return None

    print(
        f"\n[auto-compact] context at "
        f"{last_input_tokens:,}/{context_window:,} tokens "
        f"({last_input_tokens / context_window:.1%}) — summarizing…"
    )

    try:
        result = await compact_session(
            log,
            chain=chain,
            graph=graph,
            session_config=session_config,
            model=model,
            keep_turns=keep_turns,
            channel=channel,
        )
    except ValueError as e:
        # Nothing to compact (e.g. only 2 turns exist) — that's fine,
        # the user will hit the limit on a later turn.
        print(f"[auto-compact] skipped: {e}")
        return None
    except Exception as e:
        print(f"[auto-compact] failed: {e!r}")
        return None

    print(
        f"[auto-compact] done: {result['event_count']} events, "
        f"~{result['tokens_before']:,} → ~{result['tokens_after']:,} tokens\n"
    )
    return result