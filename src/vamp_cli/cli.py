import sys
import asyncio
from pathlib import Path
from rich.console import Console
from rich.table import Table
from vamp_cli.persistence.loader import list_sessions
from vamp_cli.boot.main import main

__VERSION__ = "0.1.9"
PROJECT_DIR = str(Path.cwd())
console = Console() 


def start():
    
    args = sys.argv[1:]
    resume_id = None
    list_flag = False

    if "--list" in args or "-l" in args:
        list_flag = True
    if "--resume" in args or "-r" in args:
        i = args.index("--resume" if "--resume" in args else "-r")
        if i + 1 < len(args):
            resume_id = args[i + 1]

    if "--previous" in args or "-p" in args:
        last_session = Path.home() / ".vamp" / "sessions" / "last" / "last.head"
        if last_session.exists() and last_session.is_file():
            resume_id = last_session.read_text().strip()

    if list_flag:
        sessions_dir = Path.home() / ".vamp" / "sessions"
        rows = list_sessions(sessions_dir)
        if not rows:
            console.print("[yellow]No previous sessions found.[/yellow]")
            return

        table = Table(title=f"Saved Sessions ({sessions_dir})", border_style="cyan")
        table.add_column("Session ID", style="bold yellow", width=38)
        table.add_column("Created At", style="dim", width=20)
        table.add_column("Initial Prompt", style="white")

        for r in rows:
            started = (r["started_at"] or "")[:19]
            msg = (r["first_message"] or "")[:70].replace("\n", " ")
            table.add_row(r["session_id"], started, msg)

        console.print(table)
        return
        
    async def run_main():
        await main(
            version=__VERSION__,
            console=console,
            project_dir=PROJECT_DIR,
            resume_id=resume_id
        )
    
    asyncio.run(run_main())


if __name__ == "__main__":
    start()