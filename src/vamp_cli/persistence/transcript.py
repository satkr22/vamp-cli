"""
Render a chain as a human-readable transcript using prompt_toolkit.
"""

from __future__ import annotations

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import FormattedText

from vamp_cli.persistence.loader import build_shadow_map


def _truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f" ... [{len(text) - limit} chars truncated]"


def render_transcript(
    chain: list[dict],
    *,
    max_content_chars: int = 400,
    max_reasoning_chars: int = 240,
    use_color: bool = True,
) -> FormattedText:
    """
    Return the chain as prompt_toolkit formatted text.

    Shadowed events are skipped; summary is shown in their place.
    All styling is represented by prompt_toolkit style fragments rather
    than raw ANSI escape sequences.
    """
    shadowed, compaction_by_uuid = build_shadow_map(chain)

    # prompt_toolkit style names.
    DIM = "gray"
    BOLD_CYAN = "bold cyan"
    BOLD_GREEN = "bold green"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

    out: list[tuple[str, str]] = []

    def add(text: str, style: str = "") -> None:
        if use_color and style:
            out.append((style, text))
        else:
            out.append(("", text))

    pending_tool_calls: dict[str, dict] = {}

    for ev in chain:
        u = ev.get("uuid")
        t = ev.get("type")
        d = ev.get("data") or {}

        if u in shadowed:
            continue

        if t in ("session_start", "session_end"):
            continue

        if t == "mode_switch":
            add(
                f"-- mode: {d.get('from')} -> {d.get('to')} --",
                DIM,
            )
            add("\n")
            continue

        if t == "compaction":
            summary = (d.get("summary") or "").strip()
            add(
                f"-- [compacted {d.get('event_count', '?')} messages] --",
                YELLOW,
            )
            add("\n")
            if summary:
                add("   " + summary.replace("\n", "\n   "), DIM)
                add("\n")
            continue

        if t == "user_message":
            content = d.get("content", "")
            add("\n")
            add("You:", BOLD_CYAN)
            add("\n")
            add(content)
            add("\n")
            continue

        if t == "assistant_message":
            reasoning = d.get("reasoning_content") or ""
            content = d.get("content") or ""
            tool_calls = d.get("tool_calls") or []

            if reasoning:
                add(
                    "  (thinking) " + _truncate(reasoning, max_reasoning_chars),
                    DIM,
                )
                add("\n")

            if content:
                add("\n")
                add("Agent:", BOLD_GREEN)
                add("\n")
                add(content)
                add("\n")

            for tc in tool_calls:
                pending_tool_calls[tc.get("id") or ""] = tc
                name = tc.get("name") or "?"
                add(f"  >> {name}(...)", DIM)
                add("\n")
            continue

        if t == "interrupt_request":
            kind = d.get("kind") or "?"
            add(f"-- interrupt: {kind} requested --", YELLOW)
            add("\n")
            continue

        if t == "interrupt_response":
            verdict = "approved" if d.get("approved") else "denied"
            add(
                f"-- interrupt: {d.get('kind')} {verdict} --",
                GREEN if d.get("approved") else RED,
            )
            add("\n")
            continue

        if t == "fork":
            add(
                f"-- fork: {d.get('reason', '')} -> thread {d.get('thread_id')} --",
                DIM,
            )
            add("\n")
            continue

    # Remove trailing newlines
    while out and out[0][1].strip() == "":
        out.pop(0)
    while out and out[-1][1].strip() == "":
        out.pop()

    return FormattedText(out)


def print_transcript(chain: list[dict], *, header: str | None = None) -> None:
    """wrapper for main.py."""
    if header:
        print_formatted_text(FormattedText([("", f"\n{header}\n")]))

    body = render_transcript(chain)
    if body:
        print_formatted_text(body)
    else:
        print_formatted_text(FormattedText([("", "(no prior messages)")]))
