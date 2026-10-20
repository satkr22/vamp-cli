from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from vamp_cli.persistence.event_log import EventLog
from vamp_cli.persistence.transcript import print_transcript
from vamp_cli.persistence.loader import (
    load_chain,
    list_user_messages,
    derive_display_state,
)
from vamp_cli.persistence.event_log import EventLog
from vamp_cli.persistence.fork import fork_at_event



async def fork(
    console: Console, 
    log: EventLog,
    session: PromptSession,
    sessions_dir: Path,
    session_id: str,
    session_config: Any,
    user_input: str,
    graph: Any,
) -> bool | tuple[bool, str, str, bool]:
    
    continue_flag = False
    
    chain = load_chain(sessions_dir, session_id)
    ums   = list_user_messages(chain)
    
    if not ums:
        console.print("[yellow]No user messages to fork to.[/yellow]")
        continue_flag = True
        return continue_flag
    
    shadowed_count = sum(1 for m in ums if m["shadowed"])
    title = "User Messages on this Branch"
    if shadowed_count:
        title += f" [dim]({shadowed_count} compacted)[/dim]"
    
    tbl = Table(title=title, border_style="cyan")
    tbl.add_column("#", width=4)
    tbl.add_column("Time", style="dim", width=10)
    tbl.add_column("Message Preview", style="white")
    # tbl.add_column("Status", style="bold yellow", width=12)
    
    for m in ums:
        ts = (m["timestamp"] or "")[11:19]
        preview = m["content"].replace("\n", " ")[:60]
        status = "[compacted]" if m["shadowed"] else ""
        tbl.add_row(str(m['index']), ts, preview)
    
    console.print(tbl)
    
    # footer explaining fork semantics for compacted branches
    any_compaction = any(e.get("type") == "compaction" for e in chain)
    if any_compaction:
        console.print(
            Panel(
                "Rewinding creates a new branch from that point. Because the compaction event currently sits at the tip of this branch, any fork goes to a point BEFORE it - the new branch will show the original (uncompacted) messages from that point on.",
                title="[dim]Note on Compacted Branches[/dim]",
                border_style="dim"
            )
        )
    
    if user_input.startswith("/fork "):
        pick = user_input[len("/fork "):].strip()
    else:
        with patch_stdout():
            pick = (await session.prompt_async("Rewind to BEFORE message # or 'cancel': ")).strip()
    
    if pick.lower() in ("", "cancel", "c"):
        continue_flag = True
        return continue_flag
    try:
        idx = int(pick)
    except ValueError:
        console.print("[red]Not a number.[/red]")
        continue_flag = True
        return continue_flag
    if idx < 1 or idx > len(ums):
        console.print("[red]Out of range.[/red]")
        continue_flag = True
        return continue_flag
    
    target = ums[idx - 1]
    
    if target["shadowed"]:
        console.print(
            Panel(
                f"Message [bold]{idx}[/bold] has been compacted away.\nRewinding to it will produce a new branch where that compaction does not apply - the original messages from that point will be restored.",
                title="[bold yellow]Warning[/bold yellow]",
                border_style="yellow"
            )
        )
        with patch_stdout():
            confirm = (await session.prompt_async("Proceed? [y/N]: ")).strip().lower()
        if confirm not in ("y", "yes"):
            console.print("[dim]Cancelled.[/dim]")
            continue_flag = True
            return continue_flag
    
    fork_point = target["parent_uuid"]
    if not fork_point:
        console.print("[red]Cannot fork before the first event.[/red]")
        continue_flag = True
        return continue_flag
    
    console.print(f"\n[dim]Rewinding before message [{idx}]:[/dim] [cyan]\"{target['content'][:60]}\"[/cyan]")
    console.print("[dim](The old branch is preserved; nothing is deleted.)[/dim]\n")
    
    try:
        with console.status("[bold cyan]Forking timeline...", spinner="dots"):
            result = await fork_at_event(
                log,
                session_id=session_id,
                fork_point_uuid=fork_point,
                graph=graph,
                reason=f"user_rewind_before_user_message_{idx}",
            )
    except Exception as e:
        console.print(f"[bold red][fork failed][/bold red] {e!r}")
        continue_flag = True
        return continue_flag
    
    THREAD_ID = result["thread_id"]
    session_config["configurable"]["thread_id"] = THREAD_ID
    last_state = result["state"]
    
    chain = load_chain(sessions_dir, session_id, leaf_uuid=result["fork_uuid"])
    disp = derive_display_state(chain)
    selected_mode = disp["selected_mode"]
    executed_mode = disp["executed_mode"]
    is_session_started = disp["is_session_started"]
    
    console.print(f"[green]Forked.[/green] New thread: [bold]{THREAD_ID}[/bold]")
    console.print(
        f"  [dim]State: {len(last_state['coder_messages'])} coder / "
        f"{len(last_state['planner_messages'])} planner messages, "
        f"iteration={last_state.get('iteration', 0)}[/dim]"
    )
    console.print("[italic]Next message you type starts the new branch.[/italic]\n")
    
    print_transcript(chain, header=f"Forked branch {result['fork_uuid'][:8]}")
    continue_flag = True
    return continue_flag, selected_mode, executed_mode, is_session_started