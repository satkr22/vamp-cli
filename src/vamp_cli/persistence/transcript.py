"""
Render a chain as a human-readable transcript using Rich components.
"""

from __future__ import annotations

import sys

from rich.console import Console
console = Console(file=sys.__stdout__)

from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown

from vamp_cli.persistence.loader import build_shadow_map

console = Console()

def _truncate(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"  ... ({len(text) - limit} chars truncated)"


def print_transcript(chain: list[dict], *, header: str | None = None) -> None:
    """Renders the execution history into styled terminal elements."""
    if header:
        console.rule(f"[bold cyan]{header}[/bold cyan]")
    else:
        console.rule("[bold cyan]Session Transcript[/bold cyan]")

    shadowed, _ = build_shadow_map(chain)

    for ev in chain:
        u = ev.get("uuid")
        t = ev.get("type")
        d = ev.get("data") or {}

        if u in shadowed or t in ("session_start", "session_end"):
            continue

        if t == "mode_switch":
            console.print(
                f"  [dim italic]Mode switched:[/dim italic] [magenta]{d.get('from')}[/magenta] [dim]→[/dim] [bold magenta]{d.get('to')}[/bold magenta]"
            )

        elif t == "compaction":
            summary = (d.get("summary") or "").strip()
            panel_text = Text()
            panel_text.append(f"Compacted {d.get('event_count', '?')} turns to save context\n", style="bold yellow")
            if summary:
                panel_text.append(summary, style="dim")
            console.print(Panel(panel_text, border_style="yellow", expand=False))

        elif t == "user_message":
            text:str = d.get("content", "")
            
            before, _, _ = text.partition("--- FILE:")
            
            console.print(
                Panel(
                    before,
                    title="[bold cyan]You[/bold cyan]",
                    title_align="left",
                    border_style="cyan",
                    padding=(0, 1),
                )
            )

        elif t == "assistant_message":
            reasoning = d.get("reasoning_content") or ""
            content = d.get("content") or ""
            tool_calls = d.get("tool_calls") or []

            if reasoning:
                console.print(
                    Panel(
                        Text(_truncate(reasoning, 240), style="italic dim"),
                        title="[dim]Thought Process[/dim]",
                        title_align="left",
                        border_style="dim",
                        padding=(0, 1),
                    )
                )

            if content:
                console.print(
                    Panel(
                        Markdown(content),
                        title="[bold green]Agent[/bold green]",
                        title_align="left",
                        border_style="green",
                        padding=(0, 1),
                    )
                )

            for tc in tool_calls:
                name = tc.get("name") or "?"
                args = tc.get("args") or tc.get("arguments") or ""
                console.print(f"  [dim cyan] Tool Execution:[/dim cyan] [bold yellow]{name}[/bold yellow]([dim]{_truncate(str(args), 80)}[/dim])")

        elif t == "interrupt_request":
            kind = d.get("kind") or "Action"
            console.print(f"  [bold yellow]⚠ Interrupt Request:[/bold yellow] [dim]{kind}[/dim]")

        elif t == "interrupt_response":
            approved = d.get("approved")
            status = "[green] Approved[/green]" if approved else "[red] Denied[/red]"
            console.print(f"  [bold]Interrupt Decision:[/bold] {status} {d.get('reason') if not approved else ''}")

        elif t == "fork":
            console.print(f"  [dim] Forked at {d.get('fork_point_uuid', '')[:8]} -> Thread: {d.get('thread_id')}[/dim]")

    console.rule("[dim]End of History[/dim]")


