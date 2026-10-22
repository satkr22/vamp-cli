from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import HTML

from vamp_cli.utils.token_tracker import Token_Tracker
from vamp_cli.utils.auto_complete import AtFileCompleter, expand_at_references
from vamp_cli.ui.banners import print_banner
from vamp_cli.ui.render import render_context_bar, render_tool_interrupt
from vamp_cli.boot.bootstrap import create_runtime
from vamp_cli.boot.streaming import streaming

from vamp_cli.commands.exit import exit_cli
from vamp_cli.commands.mode import mode
from vamp_cli.commands.config import config
from vamp_cli.commands.branches import branches
from vamp_cli.commands.fork import fork
from vamp_cli.commands.compact import compact

from vamp_cli.agent.graph import create_graph
from vamp_cli.agent.roles import AgentRole
from vamp_cli.agent.status import FeedbackResponse
from vamp_cli.agent.context_sync import mode_switch_sync
from vamp_cli.agent.instructions import TODO_INSTRUCTIONS
from vamp_cli.persistence.emitter import EventEmitter
from vamp_cli.persistence.event_log import EventLog, uuid7
from vamp_cli.persistence.transcript import print_transcript
from vamp_cli.persistence.compaction import DEFAULT_KEEP_TURNS, auto_compact_if_needed, DEFAULT_COMPACT_THRESHOLD
from vamp_cli.persistence.loader import (
    load_session,
    ResumedSession,
    load_chain,
)

from langgraph.types import Command
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.messages import HumanMessage, SystemMessage



async def main(
    version: str,
    console: Console, 
    project_dir: str, 
    resume_id: str | None = None
):
    
    VERSION = version
    
    token_tracker = Token_Tracker()
    agent_runtime, tool_registry, sandbox = await create_runtime(project_dir)
    coder_profile = agent_runtime.resolve(AgentRole.CODER)
    planner_profile = agent_runtime.resolve(AgentRole.PLANNER)

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
            workspace_root=project_dir,
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
        
        # fresh session
        if not is_resume:
            await log.append("session_start", {
                "workspace_root": project_dir,
                "models": {
                    "coder":   getattr(coder_profile.model,   "model_name", None)
                                or getattr(coder_profile.model,   "model", None),
                    "planner": getattr(planner_profile.model, "model_name", None)
                                or getattr(planner_profile.model, "model", None),
                },
                "roles": {"coder": AgentRole.CODER.value, "planner": AgentRole.PLANNER.value},
                "tools": tool_registry.list_tool_names(),
                "checkpoint_thread_id": THREAD_ID,   # step 4 will make this per-session
                "checkpoint_backend":   "sqlite",
                "system_prompts": {
                    "coder":   coder_profile.system_prompt,
                    "planner": planner_profile.system_prompt,
                },
            })
        

        selected_mode: str = "normal"
        executed_mode: str = "normal"
        is_session_started = False
        pending_command = None
        last_state = None
        permission_prompt_active = False
        
        coder_name = getattr(coder_profile.model, "model_name", None) or getattr(coder_profile.model, "model", "default")
        planner_name = getattr(planner_profile.model, "model_name", None) or getattr(planner_profile.model, "model", "default")
                
        print_banner(console, SESSION_ID, is_resume, str(coder_name), str(planner_name), version, project_dir)

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
        def get_bottom_statusbar():
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
            completer=AtFileCompleter(root_dir=project_dir),
            bottom_toolbar=get_bottom_statusbar,
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
                render_tool_interrupt(console, checkpoint_data)
            else:
                interrupt_kind = "unknown"
                console.print(Panel(str(checkpoint_data), title="Interrupt Review"))
            
            try:
                permission_prompt_active = True
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
                permission_prompt_active = False
            except KeyboardInterrupt:
                permission_prompt_active = False
                reason = "cancelled by user"
                await log.append("interrupt_response", {
                    "kind": interrupt_kind,
                    "approved": False,
                    "reason": reason,
                })
                console.print("\n[dim]Permission request cancelled. Ready for input.[/dim]")
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
                        
                        user_input = expand_at_references(user_input, project_dir)

                        # /exit or /quit
                        if user_input.lower() in ["/exit", "/quit"]:
                            await exit_cli(console, log, sandbox)
                            return

                        # /mode
                        if user_input == "/mode":
                            output = await mode(console, log, executed_mode, session)
                            if isinstance(output, str):
                                selected_mode = output
                            continue
                        
                        # /config
                        if user_input == "/config":
                            # open ~/.vamp/config.yaml
                            config()
                            continue

                        # /branches
                        if user_input == "/branches":
                            continue_flag = await branches(
                                console,
                                log,
                                session,
                                sessions_dir,
                                SESSION_ID,
                                session_config
                            )
                            if continue_flag:
                                continue
                        
                        # /fork
                        if user_input == "/fork" or user_input.startswith("/fork "):
                            
                            output = await fork(
                                console,
                                log,
                                session,
                                sessions_dir,
                                SESSION_ID,
                                session_config,
                                user_input,
                                graph
                            )
                            
                            if isinstance(output, bool):
                                if output:
                                    continue
                            elif isinstance(output, tuple):
                                fg, selected_mode, executed_mode, is_session_started = output
                                if fg:
                                    continue

                        # /compact
                        if user_input.startswith("/compact"):
                            cont_flag = await compact(
                                console,
                                log, 
                                session,
                                sessions_dir,
                                SESSION_ID,
                                session_config,
                                selected_mode,
                                coder_profile,
                                planner_profile,
                                graph
                            )
                            if cont_flag:
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
                    usage = await streaming(console, graph, payload, session_config, emitter, tool_registry)
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
                            render_tool_interrupt(console, checkpoint_data) # type: ignore
                        else:
                            interrupt_kind = "unknown"
                            console.print(Panel(str(checkpoint_data), title="Interrupt Review"))

                        # Persist the request before asking for input. If the
                        # process exits while the prompt is unanswered, resume
                        # can rediscover it from the event log.
                        await log.append("interrupt_request", {
                            "kind": interrupt_kind,
                            "payload": checkpoint_data,
                        })

                        try:
                            permission_prompt_active = True
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
                            permission_prompt_active = False
                        except KeyboardInterrupt:
                            permission_prompt_active = False
                            reason = "cancelled by user"
                            await log.append("interrupt_response", {
                                "kind": interrupt_kind,
                                "approved": False,
                                "reason": reason,
                            })
                            console.print("\n[dim]Permission request cancelled. Ready for input.[/dim]")
                            pending_command = Command(resume=FeedbackResponse(approved=False, reason=reason))
                        continue

                    tok_usage = token_tracker.close()
                    max_ctx = planner_profile.context_window if selected_mode == "plan" else coder_profile.context_window
                    render_context_bar(console, emitter.last_input_tokens, max_ctx, tok_usage)

            except KeyboardInterrupt:
                console.print("\n[dim]Action cancelled. Ready for input.[/dim]")
                if permission_prompt_active:
                    # A Ctrl+C during approval is an explicit cancellation.
                    # Resolve the graph interrupt so it cannot be resumed later.
                    permission_prompt_active = False
                    pending_command = Command(
                        resume=FeedbackResponse(
                            approved=False,
                            reason="cancelled by user",
                        )
                    )
                else:
                    pending_command = None
                continue
            except Exception as e:
                await log.close(reason="error")
                await sandbox.destroy()
                console.print(f"[bold red][runtime error][/bold red] {e}")
                return
