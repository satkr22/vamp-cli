from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from vamp_cli.persistence.event_log import EventLog
from vamp_cli.persistence.loader import load_chain
from vamp_cli.persistence.event_log import EventLog
from vamp_cli.persistence.compaction import compact_session, DEFAULT_KEEP_TURNS, plan_compaction_range

async def compact(
    console: Console, 
    log: EventLog,
    session: PromptSession,
    sessions_dir: Path,
    session_id: str,
    session_config: Any,
    selected_mode: str,
    coder_profile: Any,
    planner_profile: Any,
    graph: Any,
) -> bool:
    
    continue_flag = False
    
    chain = load_chain(sessions_dir, session_id)
    plan = plan_compaction_range(chain, keep_turns=DEFAULT_KEEP_TURNS)
    if not plan:
        console.print(f"[yellow]Branch too short to compact (≤ {DEFAULT_KEEP_TURNS} turns).[/yellow]")
        continue_flag = True
        return continue_flag
    
    console.print(
        Panel(
            f"Compacting will compress [bold yellow]{plan['event_count']}[/bold yellow] events "
            f"(~{plan['est_tokens']:,} tokens), preserving the last {DEFAULT_KEEP_TURNS} turns.",
            title="Context Compactor",
            border_style="yellow",
        )
    )
    with patch_stdout():
        confirm = (await session.prompt_async("Proceed? [y/N]: ")).strip().lower()
    if confirm in ("y", "yes"):
        with console.status("[bold yellow]Compacting context with LLM...", spinner="aesthetic"):
            result = await compact_session(
                log,
                chain=chain,
                graph=graph,
                session_config=session_config,
                model=coder_profile.model if selected_mode == "normal" else planner_profile.model,
                keep_turns=DEFAULT_KEEP_TURNS,
                channel="both",
            )
        console.print(f"[green]Reduced from ~{result['tokens_before']:,} to ~{result['tokens_after']:,} tokens.[/green]")
    continue_flag = True
    return continue_flag