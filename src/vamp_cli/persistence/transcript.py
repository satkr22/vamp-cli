# """
# Render a chain as a human-readable transcript for display on resume
# or branch switch. Honours compaction shadowing so the user sees what
# the model sees.
# """

# from __future__ import annotations

# from vamp_cli.persistence.loader import build_shadow_map

# # ANSI helpers
# _DIM   = "\033[2m"
# _BOLD  = "\033[1m"
# _CYAN  = "\033[36m"
# _GREEN = "\033[32m"
# _YELLOW = "\033[33m"
# _RED   = "\033[31m"
# _RESET = "\033[0m"


# def _truncate(text: str, limit: int = 400) -> str:
#     if len(text) <= limit:
#         return text
#     return text[:limit] + f" … [{len(text) - limit} chars truncated]"


# def render_transcript(
#     chain: list[dict],
#     *,
#     max_content_chars: int = 400,
#     max_reasoning_chars: int = 240,
#     use_color: bool = True,
# ) -> str:
#     """
#     Return the chain rendered as plain text (optionally ANSI-coloured).
#     Shadowed events are skipped; summary is shown in their place.
#     """
#     c = (lambda s, col: f"{col}{s}{_RESET}") if use_color else (lambda s, col: s)

#     shadowed, compaction_by_uuid = build_shadow_map(chain)

#     out: list[str] = []
#     pending_tool_calls: dict[str, dict] = {}

#     for ev in chain:
#         u = ev.get("uuid")
#         t = ev.get("type")
#         d = ev.get("data") or {}

#         if u in shadowed:
#             continue

#         if t in ("session_start", "session_end"):
#             continue

#         if t == "mode_switch":
#             out.append(c(
#                 f"-- mode: {d.get('from')} → {d.get('to')} --",
#                 _DIM,
#             ))
#             continue

#         if t == "compaction":
#             summary = (d.get("summary") or "").strip()
#             out.append(c(
#                 f"-- [compacted {d.get('event_count', '?')} messages] --",
#                 _YELLOW,
#             ))
#             if summary:
#                 out.append(c("   " + summary.replace("\n", "\n   "), _DIM))
#             continue

#         if t == "user_message":
#             content = _truncate(d.get("content", ""), max_content_chars)
#             out.append("")
#             out.append(c("You:", _BOLD + _CYAN))
#             out.append(content)
#             continue

#         if t == "assistant_message":
#             reasoning = d.get("reasoning_content") or ""
#             content   = d.get("content") or ""
#             tool_calls = d.get("tool_calls") or []

#             if reasoning:
#                 out.append(c(
#                     "  (thinking) " + _truncate(reasoning, max_reasoning_chars),
#                     _DIM,
#                 ))

#             if content:
#                 out.append("")
#                 out.append(c("Agent:", _BOLD + _GREEN))
#                 out.append(_truncate(content, max_content_chars))

#             for tc in tool_calls:
#                 pending_tool_calls[tc.get("id") or ""] = tc
#                 name = tc.get("name") or "?"
#                 out.append(c(f"  ▸ {name}(…)", _DIM))
#             continue

#         # if t == "tool_result":
#         #     name = d.get("tool_name") or "?"
#         #     status = d.get("status") or "success"
#         #     mark = "O" if status == "success" else "X"
#         #     col  = _GREEN if status == "success" else _RED
#         #     preview = _truncate(d.get("content") or "", 80).replace("\n", " ")
#         #     out.append(c(f"    {mark} {name}: {preview}", col))
#         #     pending_tool_calls.pop(d.get("tool_call_id") or "", None)
#         #     continue

#         if t == "interrupt_request":
#             kind = d.get("kind") or "?"
#             out.append(c(f"-- interrupt: {kind} requested --", _YELLOW))
#             continue

#         if t == "interrupt_response":
#             verdict = "approved" if d.get("approved") else "denied"
#             out.append(c(
#                 f"-- interrupt: {d.get('kind')} {verdict} --",
#                 _GREEN if d.get("approved") else _RED,
#             ))
#             continue

#         if t == "fork":
#             out.append(c(
#                 f"-- fork: {d.get('reason', '')} -> thread {str(d.get('thread_id'))} --",
#                 _DIM,
#             ))
#             continue

#     return "\n".join(out).strip()


# def print_transcript(chain: list[dict], *, header: str | None = None) -> None:
#     """Convenience wrapper for main.py."""
#     if header:
#         print(f"\n{header}\n")
#     body = render_transcript(chain)
#     if body:
#         print(body)
#     else:
#         print("(no prior messages)")



"""
Render a chain as a human-readable transcript using prompt_toolkit.
No ANSI escape sequences are used directly; all styling is handled by
prompt_toolkit formatted text fragments.
"""

from __future__ import annotations

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import FormattedText

from vamp_cli.persistence.loader import build_shadow_map


def _truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f" … [{len(text) - limit} chars truncated]"


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
    DIM = "dim"
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
                f"-- mode: {d.get('from')} → {d.get('to')} --",
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
            content = _truncate(d.get("content", ""), max_content_chars)
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
                add(_truncate(content, max_content_chars))
                add("\n")

            for tc in tool_calls:
                pending_tool_calls[tc.get("id") or ""] = tc
                name = tc.get("name") or "?"
                add(f"  ▸ {name}(…)", DIM)
                add("\n")
            continue

        # if t == "tool_result":
        #     name = d.get("tool_name") or "?"
        #     status = d.get("status") or "success"
        #     mark = "O" if status == "success" else "X"
        #     col = GREEN if status == "success" else RED
        #     preview = _truncate(d.get("content") or "", 80).replace("\n", " ")
        #     add(f"    {mark} {name}: {preview}", col)
        #     add("\n")
        #     pending_tool_calls.pop(d.get("tool_call_id") or "", None)
        #     continue

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

    # Remove trailing newlines, matching the old .strip() behavior closely.
    while out and out[0][1].strip() == "":
        out.pop(0)
    while out and out[-1][1].strip() == "":
        out.pop()

    return FormattedText(out)


def print_transcript(chain: list[dict], *, header: str | None = None) -> None:
    """Convenience wrapper for main.py."""
    if header:
        print_formatted_text(FormattedText([("", f"\n{header}\n")]))

    body = render_transcript(chain)
    if body:
        print_formatted_text(body)
    else:
        print_formatted_text(FormattedText([("", "(no prior messages)")]))
