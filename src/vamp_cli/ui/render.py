from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import json



def render_context_bar(console: Console, last_tokens: int, max_tokens: int, tok_usage: dict[str, int]|None) -> None:
    """Renders a compact token usage metric with percentage health."""
    if last_tokens <= 0 or max_tokens <= 0:
        return
    pct = (last_tokens / max_tokens) * 100
    color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
    
    
    console.print(f"\n  [dim]Context Window:[/dim] [{color}]{last_tokens:,}[/{color}]/[dim]{max_tokens:,} ({pct:.1f}%)[/dim]")
    
    if tok_usage is not None:
        console.print(f"  [dim]Session Tokens:[/dim] [{color}]{tok_usage['total_tokens']}[/{color}]\n")
        
def render_tool_interrupt(console: Console, checkpoint_data: dict) -> None:
    """Renders dangerous calls in a structured security verification table."""
    calls = checkpoint_data.get("dangerous_calls", [])
    safe_count = checkpoint_data.get("safe_call_count", 0)

    table = Table(title="[bold red]Security Verification Alert[/bold red]", border_style="red", expand=True)
    table.add_column("Tool", style="bold yellow", width=22)
    table.add_column("Arguments", style="white")

    for c in calls:
        table.add_row(c.get("tool_name", "unknown"), json.dumps(c.get("tool_args", {}), indent=2))

    console.print()
    console.print(table)
    if safe_count:
        console.print(f"  [dim]ℹ Plus {safe_count} safe call(s) will execute automatically upon authorization.[/dim]")
    if checkpoint_data.get("warning"):
        console.print(Panel(checkpoint_data.get("warning", ""), title="[bold yellow]Safety Policy Warning[/bold yellow]", border_style="yellow"))



