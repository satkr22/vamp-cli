import os
import sys
from rich.console import Console, Group
console = Console(file=sys.__stdout__)

from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

import json
import asyncio
from pathlib import Path
from typing import Any

import click
import yaml
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import HTML

from vamp_cli.workspace.workspace import Workspace
from vamp_cli.config.loader import load_config
from vamp_cli.prompts.prompts import PromptStore
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.utils.token_tracker import Token_Tracker
from vamp_cli.utils.auto_complete import AtFileCompleter, expand_at_references
from vamp_cli.sandbox.terminal import Terminal
from vamp_cli.sandbox.docker import SandboxConfig
from vamp_cli.sandbox.manager import SandboxManager
from vamp_cli.tools.files import FileTools
from vamp_cli.tools.diagnostic import DiagnosticTools
from vamp_cli.tools.execute_cmd import ExecuteCommandTool
from vamp_cli.tools.tools_classes import *
from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.agent.runtime.runtime import AgentRuntime
from vamp_cli.agent.graph import create_graph
from vamp_cli.agent.roles import AgentRole
from vamp_cli.agent.status import FeedbackResponse
from vamp_cli.agent.context_sync import mode_switch_sync
from vamp_cli.agent.instructions import TODO_INSTRUCTIONS
from vamp_cli.persistence.emitter import EventEmitter
from vamp_cli.persistence.event_log import EventLog, uuid7
from vamp_cli.persistence.fork import fork_at_event
from vamp_cli.persistence.transcript import print_transcript
from vamp_cli.persistence.compaction import compact_session, DEFAULT_KEEP_TURNS, plan_compaction_range, auto_compact_if_needed, DEFAULT_COMPACT_THRESHOLD
from vamp_cli.persistence.loader import (
    load_session,
    list_sessions,
    ResumedSession,
    load_chain,
    list_user_messages,
    list_leaves,
    current_thread_id,
    reconstruct_agent_state,
    derive_display_state,
    load_all_events,
)
from langgraph.types import Command
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.messages import HumanMessage, SystemMessage

__VERSION__ = "0.1.6"
PROJECT_DIR = str(Path.cwd())
console = Console()


async def build_startup():

    # --- services ---------------------------------------------------------

    workspace = Workspace(root=PROJECT_DIR)
    ignore = IgnoreMatcher(repo_root=str(workspace.root))
    diagnostic = DiagnosticTools(workspace=workspace)
    sandbox_config = SandboxConfig()
    # d_sandbox = DockerSandbox(workspace, sandbox_config)
    d_sandbox = SandboxManager(sandbox_config)

    try:
        # d_sandbox.start()
        sandbox = await d_sandbox.create(workspace)
    except Exception as e:
        print(f"\n\nDocker not running. Please start docker.\n\n")
        sys.exit(0)

    terminal = Terminal(sandbox=sandbox)

    file_tools = FileTools(
        workspace=workspace,
        ignore=ignore,
        diagnostic=diagnostic,
    )
    exec_tool = ExecuteCommandTool(terminal=terminal)


    # tool registry and tool wrappers
    registry = ToolRegistry()
    
    tools = [
        ListDirTool(file_tools, registry),
        ReadFileTool(file_tools, registry),
        ReadManyFilesTool(file_tools, registry),
        WriteFileTool(file_tools, registry),
        ApplySearchReplaceTool(file_tools, registry),
        FileSearchTool(file_tools, registry),
        RipgrepSearchTool(file_tools, registry),
        ExecuteCommandToolDef(exec_tool, registry),
        SubmitPlanTool(registry),
        WriteTodosTool(registry),
    ]
    
    registry.register_many(tools)

    # log.info("Registered %d tools: %s", len(registry), registry.list_tool_names())

    return registry, d_sandbox


async def create_runtime():

    from vamp_cli.llm.router import ModelRouter
    from vamp_cli.llm.adapter_registry import AdapterRegistry
    
    config = load_config()

    adapter = AdapterRegistry()

    tool_registry, sandbox = await build_startup()

    prompt_store = PromptStore(hot_reload=True)
    

    model_router = ModelRouter(config=config, adapters=adapter)

    return (
        AgentRuntime(
            config=config,
            model_router=model_router,
            tool_registry=tool_registry,
            prompt_store=prompt_store,
        ),
        tool_registry,
        sandbox,
    )


def print_banner(session_id: str, is_resume: bool, coder_model: str, planner_model: str):
    """Renders a sleek, modern startup dashboard."""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")

    
    project_dir = PROJECT_DIR + "/"
    if os.name == 'nt':
        project_dir = PROJECT_DIR + "\\"
        
    grid.add_row(
        f"[bold bright_cyan]VAMP-CLI[/bold bright_cyan] [dim]v{__VERSION__}[/dim]",
        f"[dim]Working Dir:[/dim] [bright_blue]{Path(project_dir)}[/bright_blue]",
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


async def streaming(
    graph,
    payload: dict | Command,
    session_config: RunnableConfig,
    emitter: EventEmitter,
    tool_registry: ToolRegistry,
) -> dict[str, int]:
    
    call_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # 1. state buffers for fixed zones
    reasoning_buffer = ""
    content_buffer = ""

    # 2. Layout generator: Stacks Reasoning on top, Content on bottom
    THINKING_MAX_LINES = 8
    
    def generate_layout(is_final = False):
        renderables = []
        
        if reasoning_buffer:
            lines = reasoning_buffer.strip().split("\n")
            if not is_final and len(lines) > THINKING_MAX_LINES:
                display_lines = ["... (scrolling)"] + lines[-(THINKING_MAX_LINES - 1):]
                box_height = THINKING_MAX_LINES + 2
            else:
                display_lines = lines
                box_height = None  # Removes the height limit for the final render
                
            display_text = "\n".join(display_lines)
            renderables.append(
                Panel(
                    Text(display_text, style="dim"), 
                    title="[dim]Thinking[/dim]", 
                    title_align="left",
                    border_style="dim"
                )
            )
            
        if content_buffer or is_final:
            display_content = content_buffer if content_buffer else "**No response content or valid tool calls provided by agent.**"
            renderables.append(
                Panel(
                    Markdown(display_content), 
                    title="[bold green]Agent[/bold green]", 
                    title_align="left",
                    border_style="green"
                )
            )
            
        if not renderables:
            return Text("Starting...", style="dim italic")
            
        return Group(*renderables)

    # 3. Live Context Manager: Takes over rendering to update in-place
    try:
        with Live(generate_layout(), console=console, refresh_per_second=60, vertical_overflow="visible") as live:
            
            # UI CALLBACK
            def ui_tool_callback(tool_name: str, args: str,  output_text: str):
                nonlocal reasoning_buffer
                # Append the tool's actual output directly to the reasoning panel
                reasoning_buffer += f"\n>> {tool_name}: {args}\n\n"
                live.update(generate_layout())

            # INJECT IT TO TOOL REGISTRY 
            tool_registry.set_ui_callback(ui_tool_callback)
            
            async for event in graph.astream_events(payload, config=session_config, version="v2"):
                await emitter.consume(event)
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    
                    
                    # Extract reasoning
                    reasoning_chunk = getattr(chunk, "reasoning_content", None)
                    if not reasoning_chunk and "reasoning_content" in chunk.additional_kwargs:
                        reasoning_chunk = chunk.additional_kwargs["reasoning_content"]

                    # Route text to the correct buffer
                    if reasoning_chunk:
                        reasoning_buffer += str(reasoning_chunk)
                        live.update(generate_layout())

                    if chunk.content:
                        content_buffer += str(chunk.content)
                        live.update(generate_layout())

                elif kind == "on_chat_model_end":
                    output = event["data"].get("output")
                    um = getattr(output, "usage_metadata", None)
                    if um:
                        call_usage["input_tokens"] += um.get("input_tokens", 0)
                        call_usage["output_tokens"] += um.get("output_tokens", 0)
                        call_usage["total_tokens"] += um.get("total_tokens", 0)
                        
            live.update(generate_layout(is_final=True))
            
    except (asyncio.CancelledError, KeyboardInterrupt):
        console.print("\n[bold yellow]Action aborted by user.[/bold yellow]")
        return call_usage
    except Exception as e:
        console.print(f"\n[bold red]Error during run:[/bold red] {e}")
        return call_usage

    return call_usage


def render_tool_interrupt(checkpoint_data: dict) -> None:
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


def render_context_bar(last_tokens: int, max_tokens: int, tok_usage: dict[str, int]|None) -> None:
    """Renders a compact token usage metric with percentage health."""
    if last_tokens <= 0 or max_tokens <= 0:
        return
    pct = (last_tokens / max_tokens) * 100
    color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
    
    
    console.print(f"\n  [dim]Context Window:[/dim] [{color}]{last_tokens:,}[/{color}]/[dim]{max_tokens:,} ({pct:.1f}%)[/dim]")
    
    if tok_usage is not None:
        console.print(f"  [dim]Session Tokens:[/dim] [{color}]{tok_usage['total_tokens']}[/{color}]\n")
        
        
def edit_config(path: str | Path) -> str | None:
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    while True:
        try:
            edited = click.edit(text, extension=".yaml")  # blocks until editor closes
            if edited is None:  # user quit without saving
                return None
            
            try:
                data = yaml.safe_load(edited)
            except yaml.YAMLError as e:
                click.echo(f"Invalid YAML:\n{e}")
                if not click.confirm("Re-open editor?", default=True):
                    return None
                text = edited  # keep changes so can be fixed
                continue
            
            path.write_text(edited, encoding="utf-8")  # only save once valid
            return "ok"
        
        except click.ClickException as e:
            raise 
    


async def main(resume_id: str | None = None):
    token_tracker = Token_Tracker()
    agent_runtime, tool_registry, sandbox = await create_runtime()
    coder_profile = agent_runtime.resolve(AgentRole.CODER)
    planner_profile = agent_runtime.resolve(AgentRole.PLANNER)
    VERSION = __VERSION__

    is_resume = resume_id is not None
    SESSION_ID = resume_id if is_resume else str(uuid7())

    sessions_dir = Path.home() / ".vamp" / "sessions"
    checkpoint_dir = Path.home() / ".vamp" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_db = checkpoint_dir / f"{SESSION_ID}.sqlite"

    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_db)) as checkpointer:
        graph = create_graph(
            coder_model=coder_profile.model,
            planner_model=planner_profile.model,
            tool_registry=tool_registry,
            checkpointer=checkpointer,
        )

        log = await EventLog.open(
            session_id=SESSION_ID,
            workspace_root=PROJECT_DIR,
            version=VERSION,
        )
        emitter = EventEmitter(log)

        last_session = sessions_dir / "last"
        last_session.mkdir(parents=True, exist_ok=True)
        (last_session / "last.head").write_text(SESSION_ID, encoding="utf-8")

        THREAD_ID = SESSION_ID
        session_config: RunnableConfig = {
            "configurable": {"thread_id": THREAD_ID, "emitter": emitter},
            "recursion_limit": 200,
        }

        selected_mode: str = "normal"
        executed_mode: str = "normal"
        is_session_started = False
        pending_command = None
        last_state = None
        
        coder_name = getattr(coder_profile.model, "model_name", None) or getattr(coder_profile.model, "model", "default")
        planner_name = getattr(planner_profile.model, "model_name", None) or getattr(planner_profile.model, "model", "default")
                
        print_banner(SESSION_ID, is_resume, str(coder_name), str(planner_name))

        resumed: ResumedSession | None = None
        if is_resume:
            try:
                resumed = await load_session(
                    sessions_dir=sessions_dir,
                    session_id=SESSION_ID,
                    graph=graph,
                    session_config=session_config,
                    reconstruct_if_missing=True,
                )
                selected_mode = resumed.selected_mode
                executed_mode = resumed.executed_mode
                is_session_started = resumed.is_session_started
                last_state = resumed.last_state
                if resumed.thread_id and resumed.thread_id != THREAD_ID:
                    THREAD_ID = resumed.thread_id
                    session_config["configurable"]["thread_id"] = THREAD_ID
                
                print_transcript(resumed.events, header=f"Resumed Session {SESSION_ID[:8]}")
            except FileNotFoundError as e:
                await sandbox.destroy()
                console.print(f"[bold red][resume error][/bold red] {e}")
                return

        

        # Bottom toolbar dynamic status generator
        def get_bottom_toolbar():
            ctx_limit = planner_profile.context_window if selected_mode == "plan" else coder_profile.context_window
            used = emitter.last_input_tokens
            pct = f"{(used / ctx_limit * 100):.1f}%" if ctx_limit and used else "0%"
            return HTML(
                f" <b>Mode:</b> <style bg='ansimagenta'>{selected_mode.upper()}</style> | "
                f"<b>Thread:</b> {THREAD_ID} | "
                f"<b>Context:</b> {used:,}/{ctx_limit:,} ({pct}) | "
                f"<b>@file</b> for files"
            )

        session = PromptSession(
            completer=AtFileCompleter(root_dir=PROJECT_DIR),
            bottom_toolbar=get_bottom_toolbar,
        )
        
        
        if is_resume and resumed and resumed.pending_interrupt:
            pi = resumed.pending_interrupt
            checkpoint_data = pi.get("payload") or {}

            if checkpoint_data.get("implementation_permission"):
                interrupt_kind = "plan_approval"
                console.print(
                    Panel(
                        Markdown(checkpoint_data.get("plan", "")),
                        title="[bold yellow] Plan Implementation Review[/bold yellow]",
                        border_style="yellow",
                    )
                )
            elif checkpoint_data.get("requires_tool_approval"):
                interrupt_kind = "tool_approval"
                render_tool_interrupt(checkpoint_data)
            else:
                interrupt_kind = "unknown"
                console.print(Panel(str(checkpoint_data), title="Interrupt Review"))
            
            with patch_stdout():
                choice = (await session.prompt_async(HTML("<b>Authorize action? (y/n): </b>"))).strip().lower()
                
            if choice in ("y", "yes"):
                console.print("[green]Authorized. Resuming execution...[/green]")
                await log.append("interrupt_response", {"kind": interrupt_kind, "approved": True, "reason": None})
                pending_command = Command(resume=FeedbackResponse(approved=True))
            else:
                with patch_stdout():
                    reason = (await session.prompt_async(HTML("<b>Rejection reason: </b>"))).strip()
                await log.append("interrupt_response", {"kind": interrupt_kind, "approved": False, "reason": reason})
                console.print("[red]Blocked. Reporting reason back to agent...[/red]")
                pending_command = Command(resume=FeedbackResponse(approved=False, reason=reason))
        
        

        while True:
            try:
                    if pending_command is None:
                        with patch_stdout():
                            user_input = (
                                await session.prompt_async(HTML("<b><ansicyan>❯</ansicyan></b> "))
                            ).strip()

                        if not user_input:
                            continue
                        
                        
                        user_input, attach = expand_at_references(user_input, PROJECT_DIR)

                        # /exit or /quit
                        if user_input.lower() in ["/exit", "/quit"]:
                            with console.status("[bold red]Shutting down sandbox environment...", spinner="dots"):
                                await log.close(reason="user_exit")
                                await sandbox.destroy()
                            console.print("[dim]Session closed. Goodbye![/dim]")
                            return

                        # /mode
                        if user_input == "/mode":
                            picked = (await session.prompt_async("Select mode [normal/plan]: ")).strip().lower()
                            if picked in ["plan", "normal"]:
                                selected_mode = picked
                                await log.append("mode_switch", {"from": executed_mode, "to": selected_mode})
                                console.print(f"[green]Switched mode to [bold]{selected_mode}[/bold][/green]")
                            else:
                                console.print("[yellow]Invalid mode. Choose 'plan' or 'normal'.[/yellow]")
                            continue
                        
                        # /config
                        if user_input == "/config":
                            
                            try:
                                # open ~/.vamp/config.yaml
                                config = edit_config(Path.home() / ".vamp/config.yaml")
                                if config is None:
                                    print("\nNo changes made.")
                                    continue
                                print("\nRestart 'vamp' to load changed configuration.")
                                continue
                            
                            except yaml.YAMLError as e:
                                print(f"Invalid YAML:\n{e}")
                                continue
                            
                            except click.ClickException as e:
                                click.echo(f"Could not open an editor: {e}")
                                click.echo("Set the EDITOR environment variable, e.g. EDITOR=nano")
                                continue
                                
                            except Exception as e:
                                print("\nConfiguration changes failed.\n\n")
                                continue

                        # /branches
                        if user_input == "/branches":
                            chain = load_all_events(sessions_dir, SESSION_ID)
                            leaves = list_leaves(chain)
                            if not leaves:
                                console.print("[yellow]No alternate branches in this session.[/yellow]")
                                continue

                            tbl = Table(title="Session Branches", border_style="cyan")
                            tbl.add_column("#", width=4)
                            tbl.add_column("Branch ID", style="bold yellow", width=12)
                            tbl.add_column("Last User Query", style="white")
                            tbl.add_column("Status", style="green", width=12)

                            for i, leaf in enumerate(leaves, 1):
                                leaf_chain = load_chain(sessions_dir, SESSION_ID, leaf_uuid=leaf)
                                last_user = next((e for e in reversed(leaf_chain) if e.get("type") == "user_message"), None)
                                preview = ""
                                if last_user:
                                    preview = ((last_user.get("data") or {}).get("content") or "")[:60].replace("\n", " ")
                                status = "[bold green]CURRENT[/bold green]" if leaf == log.head_uuid else ""
                                tbl.add_row(str(i), leaf[:8], preview, status)

                            console.print(tbl)
                            pick = (await session.prompt_async("Switch to branch # or 'cancel': ")).strip()
                            if pick.lower() in ("", "cancel", "c"):
                                continue
                            try:
                                idx = int(pick)
                                target_leaf = leaves[idx - 1]
                                await log.set_head(target_leaf)
                                chain = load_chain(sessions_dir, SESSION_ID, leaf_uuid=target_leaf)
                                THREAD_ID = current_thread_id(chain, default=SESSION_ID)
                                session_config["configurable"]["thread_id"] = THREAD_ID
                                print_transcript(chain, header=f"Switched to Branch {target_leaf[:8]}")
                            except Exception:
                                console.print("[red]Invalid branch selection.[/red]")
                            continue
                        
                        # /fork
                        if user_input == "/fork" or user_input.startswith("/fork "):
                            chain = load_chain(sessions_dir, SESSION_ID)
                            ums   = list_user_messages(chain)

                            if not ums:
                                console.print("[yellow]No user messages to fork to.[/yellow]")
                                continue

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
                                pick = (await session.prompt_async("Rewind to BEFORE message # or 'cancel': ")).strip()

                            if pick.lower() in ("", "cancel", "c"):
                                continue
                            try:
                                idx = int(pick)
                            except ValueError:
                                console.print("[red]Not a number.[/red]")
                                continue
                            if idx < 1 or idx > len(ums):
                                console.print("[red]Out of range.[/red]")
                                continue

                            target = ums[idx - 1]

                            if target["shadowed"]:
                                console.print(
                                    Panel(
                                        f"Message [bold]{idx}[/bold] has been compacted away.\nRewinding to it will produce a new branch where that compaction does not apply - the original messages from that point will be restored.",
                                        title="[bold yellow]Warning[/bold yellow]",
                                        border_style="yellow"
                                    )
                                )
                                confirm = (await session.prompt_async("Proceed? [y/N]: ")).strip().lower()
                                if confirm not in ("y", "yes"):
                                    console.print("[dim]Cancelled.[/dim]")
                                    continue

                            fork_point = target["parent_uuid"]
                            if not fork_point:
                                console.print("[red]Cannot fork before the first event.[/red]")
                                continue

                            console.print(f"\n[dim]Rewinding before message [{idx}]:[/dim] [cyan]\"{target['content'][:60]}\"[/cyan]")
                            console.print("[dim](The old branch is preserved; nothing is deleted.)[/dim]\n")

                            try:
                                with console.status("[bold cyan]Forking timeline...", spinner="dots"):
                                    result = await fork_at_event(
                                        log,
                                        session_id=SESSION_ID,
                                        fork_point_uuid=fork_point,
                                        graph=graph,
                                        reason=f"user_rewind_before_user_message_{idx}",
                                    )
                            except Exception as e:
                                console.print(f"[bold red][fork failed][/bold red] {e!r}")
                                continue

                            THREAD_ID = result["thread_id"]
                            session_config["configurable"]["thread_id"] = THREAD_ID
                            last_state = result["state"]

                            chain = load_chain(sessions_dir, SESSION_ID, leaf_uuid=result["fork_uuid"])
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
                            continue

                        # /compact
                        if user_input.startswith("/compact"):
                            chain = load_chain(sessions_dir, SESSION_ID)
                            plan = plan_compaction_range(chain, keep_turns=DEFAULT_KEEP_TURNS)
                            if not plan:
                                console.print(f"[yellow]Branch too short to compact (≤ {DEFAULT_KEEP_TURNS} turns).[/yellow]")
                                continue

                            console.print(
                                Panel(
                                    f"Compacting will compress [bold yellow]{plan['event_count']}[/bold yellow] events "
                                    f"(~{plan['est_tokens']:,} tokens), preserving the last {DEFAULT_KEEP_TURNS} turns.",
                                    title="Context Compactor",
                                    border_style="yellow",
                                )
                            )
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
                            continue

                        token_tracker.start()
                        new_mode = selected_mode
                        await log.append("user_message", {"content": user_input, "mode": new_mode})

                        # Auto-compaction hook
                        chain = load_chain(sessions_dir, SESSION_ID)
                        auto = await auto_compact_if_needed(
                            log,
                            chain=chain,
                            graph=graph,
                            session_config=session_config,
                            model=coder_profile.model if new_mode == "normal" else planner_profile.model,
                            last_input_tokens=emitter.last_input_tokens,
                            context_window=coder_profile.context_window if new_mode == "normal" else planner_profile.context_window,
                            threshold=DEFAULT_COMPACT_THRESHOLD,
                            keep_turns=DEFAULT_KEEP_TURNS,
                        )
                        if auto:
                            last_state = auto["new_state"]
                            emitter.last_input_tokens = 0
                            console.print("[dim yellow] Auto-compacted earlier turns to preserve token headroom.[/dim yellow]")

                        if not is_session_started:
                            payload = {
                                "planner_messages": [
                                    SystemMessage(content=planner_profile.system_prompt, id="planner_system_prompt"),
                                    HumanMessage(content=user_input),
                                ],
                                "coder_messages": [
                                    SystemMessage(content=coder_profile.system_prompt, id="coder_system_prompt"),
                                    SystemMessage(content=TODO_INSTRUCTIONS),
                                    HumanMessage(content=user_input),
                                ],
                                "task": user_input,
                                "mode": new_mode,
                                "plan": None,
                                "requires_execution": False,
                                "todo": [],
                                "iteration": 0,
                                "_pending_tool_channel": None,
                                "_pending_tool_calls": [],
                                "_dangerous_tool_call_ids": [],
                                "_approved_tool_call_ids": [],
                                "_denied_tool_call_ids": [],
                            }
                            is_session_started = True
                        else:
                            sync = mode_switch_sync(prev_mode=executed_mode, new_mode=new_mode, last_state=last_state)
                            msg_type = "planner_messages" if new_mode == "plan" else "coder_messages"
                            payload = {
                                msg_type: sync.get(msg_type, []) + [HumanMessage(content=user_input)], 
                                "mode": new_mode,
                                "iteration": 0,
                            }
                            if new_mode == "plan":
                                payload["is_plan_injected"] = False
                            if new_mode == "normal" and executed_mode == "plan":
                                payload["plan"] = ""

                        executed_mode = new_mode

                    else:
                        payload = pending_command
                        pending_command = None

                    # Run graph stream
                    usage = await streaming(graph, payload, session_config, emitter, tool_registry)
                    token_tracker.add(usage)

                    # Interruption verification
                    state_snapshot = await graph.aget_state(session_config)
                    last_state = state_snapshot.values
                    active_interrupts = getattr(state_snapshot, "interrupts", [])
                    if not active_interrupts and state_snapshot.tasks:
                        for task in state_snapshot.tasks:
                            if getattr(task, "interrupts", None):
                                active_interrupts = task.interrupts
                                break

                    if active_interrupts:
                        raw_interrupt = active_interrupts[0]
                        checkpoint_data = getattr(raw_interrupt, "value", raw_interrupt)

                        if checkpoint_data.get("implementation_permission"): # type: ignore
                            interrupt_kind = "plan_approval"
                            console.print(
                                Panel(
                                    Markdown(checkpoint_data.get("plan", "")), # type: ignore
                                    title="[bold yellow] Plan Implementation Review[/bold yellow]",
                                    border_style="yellow",
                                )
                            )
                        elif checkpoint_data.get("requires_tool_approval"): # type: ignore
                            interrupt_kind = "tool_approval"
                            render_tool_interrupt(checkpoint_data) # type: ignore
                        else:
                            interrupt_kind = "unknown"
                            console.print(Panel(str(checkpoint_data), title="Interrupt Review"))
                        with patch_stdout():
                            choice = (await session.prompt_async(HTML("<b>Authorize action? (y/n): </b>"))).strip().lower()
                            
                        if choice in ("y", "yes"):
                            console.print("[green]Authorized. Resuming execution...[/green]")
                            await log.append("interrupt_response", {"kind": interrupt_kind, "approved": True, "reason": None})
                            pending_command = Command(resume=FeedbackResponse(approved=True))
                            continue
                        else:
                            with patch_stdout():
                                reason = (await session.prompt_async(HTML("<b>Rejection reason: </b>"))).strip()
                            await log.append("interrupt_response", {"kind": interrupt_kind, "approved": False, "reason": reason})
                            console.print("[red]Blocked. Reporting reason back to agent...[/red]")
                            pending_command = Command(resume=FeedbackResponse(approved=False, reason=reason))
                            continue

                    tok_usage = token_tracker.close()
                    max_ctx = planner_profile.context_window if selected_mode == "plan" else coder_profile.context_window
                    render_context_bar(emitter.last_input_tokens, max_ctx, tok_usage)

            except KeyboardInterrupt:
                console.print("\n[dim]Action cancelled. Ready for input.[/dim]")
                pending_command = None
                continue
            except Exception as e:
                await log.close(reason="error")
                await sandbox.destroy()
                console.print(f"[bold red][runtime error][/bold red] {e}")
                return


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

    asyncio.run(main(resume_id=resume_id))


if __name__ == "__main__":
    start()