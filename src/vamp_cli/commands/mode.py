from rich.console import Console
from vamp_cli.persistence.event_log import EventLog
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout


async def mode(console: Console, log: EventLog, executed_mode: str, session: PromptSession) -> str | None:
    with patch_stdout():
        picked = (await session.prompt_async("Select mode [normal/plan]: ")).strip().lower()
    if picked in ["plan", "normal"]:
        selected_mode: str = picked
        await log.append("mode_switch", {"from": executed_mode, "to": selected_mode})
        console.print(f"[green]Switched mode to [bold]{selected_mode}[/bold][/green]")
        return selected_mode
    else:
        console.print("[yellow]Invalid mode. Choose 'plan' or 'normal'.[/yellow]") 