from langchain_core.messages import SystemMessage, BaseMessage

def mode_switch_sync(
    prev_mode: str,
    new_mode: str,
    last_state: dict|None = None,
) -> dict:
    """Return payload additions to inject when switching modes.
    Returns {} if there is nothing to sync.
    """
    if prev_mode == new_mode or last_state is None:
        return {}

    # plan -> normal : hand the plan to the coder as context
    if prev_mode == "plan" and new_mode == "normal":
        plan = last_state.get("plan") or "(no plan was produced)"
        # todos = last_state.get("todo") or []
        # todo_lines = "\n".join(
        #     f"  [{t['status']}] {t['id']}. {t['description']}" for t in todos
        # ) or "  (empty)"

        sync_msg = SystemMessage(
            content=(
                "MODE SWITCH: The user has switched you from PLAN mode to NORMAL mode.\n"
                "You are no longer bound to a planner. You are free to act directly.\n"
                "For your reference, this is the plan that was produced earlier:\n\n"
                f"{plan}\n\n"
                # "Current todo list at the moment of the switch:\n"
                # f"{todo_lines}\n\n"
                "Treat this as context only; do not re-inject the plan verbatim."
                "NOTE: IF YOU HAVE ALREADY EXECUTED THIS PLAN THEN DO NOT CONSIDER THIS."
            )
        )
        return {"coder_messages": [sync_msg]}

    # normal -> plan : summarise coder activity for the planner
    if prev_mode == "normal" and new_mode == "plan":
        coder_msgs: list[BaseMessage] = last_state.get("coder_messages", [])
        summary = _summarise_coder_activity(coder_msgs)

        sync_msg = SystemMessage(
            content=(
                "MODE SWITCH: The user has switched you from NORMAL mode to PLAN mode.\n"
                "Below is a compact summary of what the coding agent already did in "
                "the previous (normal) mode. Build your plan on top of this — do not "
                "repeat finished work.\n\n"
                f"{summary}"
            )
        )
        return {"planner_messages": [sync_msg]}

    return {}


def _summarise_coder_activity(msgs: list[BaseMessage]) -> str:
    """
    Cheap deterministic summary: last N AI text contents + the names of any
    tools called.
    """
    lines = []
    for m in msgs[-10:]:                     # last N turns only
        # AI text
        content = getattr(m, "content", "")
        if content:
            text = content if isinstance(content, str) else str(content)
            lines.append(f"AI: {text[:400]}")

        # tool calls
        for tc in (getattr(m, "tool_calls", None) or []):
            lines.append(f"  -> tool: {tc['name']}")

    return "\n".join(lines) if lines else "(no prior activity recorded)"