from pathlib import Path
from typing import Any
from rich.console import Console
from rich.table import Table

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from vamp_cli.persistence.event_log import EventLog
from vamp_cli.persistence.transcript import print_transcript
from vamp_cli.persistence.loader import (
    load_chain,
    list_leaves,
    current_thread_id,
    load_all_events,
)


async def branches(
    console: Console, 
    log: EventLog,
    session: PromptSession,
    sessions_dir: Path,
    session_id: str,
    session_config: Any
) -> bool:
    
    continue_flag = False
    
    chain = load_all_events(sessions_dir, session_id)
    leaves = list_leaves(chain)
    if not leaves:
        console.print("[yellow]No alternate branches in this session.[/yellow]")
        continue_flag = True
        return continue_flag
    
    tbl = Table(title="Session Branches", border_style="cyan")
    tbl.add_column("#", width=4)
    tbl.add_column("Branch ID", style="bold yellow", width=12)
    tbl.add_column("Last User Query", style="white")
    tbl.add_column("Status", style="green", width=12)
    for i, leaf in enumerate(leaves, 1):
        leaf_chain = load_chain(sessions_dir, session_id, leaf_uuid=leaf)
        last_user = next((e for e in reversed(leaf_chain) if e.get("type") == "user_message"), None)
        preview = ""
        if last_user:
            preview = ((last_user.get("data") or {}).get("content") or "")[:60].replace("\n", " ")
        status = "[bold green]CURRENT[/bold green]" if leaf == log.head_uuid else ""
        tbl.add_row(str(i), leaf[:8], preview, status)
    console.print(tbl)
    with patch_stdout():
        pick = (await session.prompt_async("Switch to branch # or 'cancel': ")).strip()
    if pick.lower() in ("", "cancel", "c"):
        continue_flag = True
        return continue_flag
    try:
        idx = int(pick)
        target_leaf = leaves[idx - 1]
        await log.set_head(target_leaf)
        chain = load_chain(sessions_dir, session_id, leaf_uuid=target_leaf)
        THREAD_ID = current_thread_id(chain, default=session_id)
        session_config["configurable"]["thread_id"] = THREAD_ID
        print_transcript(chain, header=f"Switched to Branch {target_leaf[:8]}")
    except Exception:
        console.print("[red]Invalid branch selection.[/red]")
    continue_flag = True
    return continue_flag