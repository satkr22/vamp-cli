from rich.console import Console
from vamp_cli.persistence.event_log import EventLog
from vamp_cli.sandbox.manager import SandboxManager


async def exit_cli(
    console: Console, 
    log: EventLog, 
    sandbox: SandboxManager
):
    
    with console.status("[bold red]Shutting down sandbox environment...", spinner="dots"):
        await log.close(reason="user_exit")
        await sandbox.destroy()
        console.print("[dim]Session closed. Goodbye![/dim]")
        return