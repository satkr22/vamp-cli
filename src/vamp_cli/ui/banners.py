import sys
from rich.console import Console

from rich.panel import Panel
from rich.table import Table


def print_banner(console: Console, session_id: str, is_resume: bool, coder_model: str, planner_model: str, version: str, project_dir: str):
    """Renders a sleek, modern startup dashboard."""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")

        
    grid.add_row(
        f"[bold bright_cyan]VAMP-CLI[/bold bright_cyan] [dim]v{version}[/dim]",
        f"[dim]Working Dir:[/dim] [bright_blue]{project_dir}[/bright_blue]",
    )
    grid.add_row(
        f"[dim]Session:[/dim] [yellow]{session_id}[/yellow]" + (" [bold green](Resumed)[/bold green]" if is_resume else ""),
        f"[dim]Coder:[/dim] [green]{coder_model}[/green] | [dim]Planner:[/dim] [magenta]{planner_model}[/magenta]",
    )

    commands_help = (
        "[cyan]/mode[/cyan] Select normal/plan  "
        "[cyan]/fork[/cyan] Rewind history  "
        "[cyan]/branches[/cyan] Switch tips  "
        "[cyan]/compact[/cyan] Compress tokens  "
        "[cyan]/config[/cyan] Edit config  "
        "[cyan]/exit[/cyan] Quit"
    )

    console.print(
        Panel(
            grid,
            subtitle=commands_help,
            subtitle_align="center",
            border_style="bright_blue",
            padding=(1, 2),
        )
    )