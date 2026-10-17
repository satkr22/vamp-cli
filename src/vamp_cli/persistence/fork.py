"""
Fork a session: write a `fork` event, seed a new checkpointer thread with the reconstructed state at the fork point.

The parent branch is untouched. No file is rewritten. The fork event's parentUuid is the fork point, so walking back from any future head naturally includes only the current branch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import START

from vamp_cli.persistence.event_log import EventLog, uuid7
from vamp_cli.persistence.loader import load_chain, reconstruct_agent_state


async def fork_at_event(
    log: EventLog,
    *,
    session_id: str,
    fork_point_uuid: str,
    graph,
    reason: str = "user_rewind",
) -> dict[str, Any]:
    """
    Rewind the head to fork_point_uuid and seed a new checkpointer thread.

    Returns:
        {
            "fork_uuid":    the uuid of the new fork event (new head),
            "thread_id":    the new checkpointer thread id,
            "state":        the reconstructed AgentState at the fork point,
            "events_kept":  number of events in the fork-point chain,
        }
    """
    # 1. Drain pending writes so we know exactly what's on disk.
    await log.flush()

    old_head = log.head_uuid
    new_thread_id = f"{session_id}:{uuid7()}"

    # 2. Reconstruct state at the fork point BEFORE we write the fork event.
    #    (After the fork, the fork-point chain is a strict prefix of the
    #    current branch, so this is the correct snapshot.)
    sessions_dir = log.path.parent
    chain = load_chain(sessions_dir, session_id, leaf_uuid=fork_point_uuid)
    
    start_ev = next((e for e in chain if e.get("type") == "session_start"), None)
    prompts  = (start_ev or {}).get("data", {}).get("system_prompts")
    
    state = reconstruct_agent_state(chain, system_prompts=prompts)

    # 3. Write the fork event. Its parentUuid is the fork point, so it
    #    becomes a sibling of whatever the old branch continued to.
    fork_uuid = await log.append(
        "fork",
        {
            "fork_point": fork_point_uuid,
            "from_head":  old_head,
            "thread_id":  new_thread_id,
            "reason":     reason,
        },
        parent_uuid=fork_point_uuid,
    )

    # 4. Seed a fresh checkpointer thread with the reconstructed state.
    #    as_node=START tells LangGraph to compute `next` from START's
    #    outgoing edges - i.e. re-enter the graph from the top.
    new_config = {
        "configurable": {"thread_id": new_thread_id},
        "recursion_limit": 200,
    }
    await graph.aupdate_state(new_config, state, as_node=START)

    return {
        "fork_uuid":   fork_uuid,
        "thread_id":   new_thread_id,
        "state":       state,
        "events_kept": len(chain),
    }