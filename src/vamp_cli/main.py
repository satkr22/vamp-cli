import sys
import asyncio
from pathlib import Path
from typing import Any
import click  
import yaml  
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.formatted_text import HTML, FormattedText
from prompt_toolkit.patch_stdout import patch_stdout

from vamp_cli.workspace.workspace import Workspace
from vamp_cli.config.loader import load_config
from vamp_cli.prompts.prompts import PromptStore

from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.utils.token_tracker import Token_Tracker

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
    _repair_orphaned_tool_calls
)

from vamp_cli.logging_setup.logging_setup import setup

from langgraph.types import Command
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.messages import HumanMessage, SystemMessage


__VERSION__ = "0.1.0"

# log = logging.getLogger(__name__)

PROJECT_DIR = str(Path.cwd())


def create(event_type: str, data: dict[str, Any]) -> str:
    """Serializes events into structured strings"""
    return json.dumps({"type": event_type, **data})


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

    # --- tool wrappers ----------------------------------------------------
    tools = [
        ListDirTool(file_tools),
        ReadFileTool(file_tools),
        ReadManyFilesTool(file_tools),
        WriteFileTool(file_tools),
        ApplySearchReplaceTool(file_tools),
        FileSearchTool(file_tools),
        RipgrepSearchTool(file_tools),
        ExecuteCommandToolDef(exec_tool),
        SubmitPlanTool(),
        WriteTodosTool(),
    ]

    # --- tool registry ---------------------------------------------------------
    registry = ToolRegistry()
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


async def streaming(
    graph, 
    payload: dict | Command, 
    session_config: RunnableConfig,
    emitter: EventEmitter,
) -> Any:

    call_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    header_printed = False
    thinking_open = False

    if isinstance(payload, dict):
        print_formatted_text(
            FormattedText([(
                "dim",
                F"\nMode: {payload["mode"]}"
            )]),
            end="\n"
        )
    
    try:
        async for event in graph.astream_events(
            payload, config=session_config, version="v2"
        ):
            await emitter.consume(event)
            
            kind = event["event"]

            if kind == "on_chat_model_start":
                # First time we know a model will run, print the header.
                if not header_printed:
                    print_formatted_text(
                        HTML("<b><ansigreen>\n\nAgent:</ansigreen></b> "),
                        end=""
                    )
                    header_printed = True

            # capture chat model stream for final output and reasoning
            elif kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]

                # reasoning chunks
                reasoning_chunk = None
                if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
                    reasoning_chunk = chunk.reasoning_content
                elif "reasoning_content" in chunk.additional_kwargs:
                    reasoning_chunk = chunk.additional_kwargs["reasoning_content"]

                if reasoning_chunk:
                    if not thinking_open:
                        # print("Thinking:\n", end="", flush=True)
                        print_formatted_text(
                            FormattedText([(
                                "dim",
                                f"\nThinking:\n"
                            )]),
                            end=""
                        )
                        thinking_open = True
                    # print(reasoning_chunk, end="", flush=True)
                    print_formatted_text(
                        FormattedText([(
                            "dim",
                            reasoning_chunk
                        )]),
                        end=""
                    )

                # 2. capture final human text
                elif chunk.content:
                    if thinking_open:
                        print("\n\n", end="", flush=True)
                        thinking_open = False
                    print(chunk.content, end="", flush=True)

            elif kind == "on_chat_model_end":
                output = event["data"].get("output")
                um = getattr(output, "usage_metadata", None)
                if um:
                    call_usage["input_tokens"] += um.get("input_tokens", 0)
                    call_usage["output_tokens"] += um.get("output_tokens", 0)
                    call_usage["total_tokens"] += um.get("total_tokens", 0)

    except (asyncio.CancelledError, KeyboardInterrupt) as e:
        print("\n[aborted by user]")
        return call_usage
    except Exception as e:
        print("\n[Some error happened]: ", e)
        return call_usage

    print("\n")
    return call_usage


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

    # logger
    # setup(level=logging.DEBUG, log_dir=f"{PROJECT_DIR}/logs")

    # basic
    token_tracker = Token_Tracker()
    agent_runtime, tool_registry, sandbox = await create_runtime()
    coder_profile = agent_runtime.resolve(AgentRole.CODER)
    planner_profile = agent_runtime.resolve(AgentRole.PLANNER)
    VERSION = __VERSION__
    
    # session
    is_resume = resume_id is not None
    SESSION_ID  = resume_id if is_resume else str(uuid7())
    
    sessions_dir   = Path.home() / ".vamp" / "sessions"
    checkpoint_dir = Path.home() / ".vamp" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_db  = checkpoint_dir / f"{SESSION_ID}.sqlite"
    
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_db)) as checkpointer:
        graph = create_graph(
                coder_model=coder_profile.model,
                planner_model=planner_profile.model,
                tool_registry=tool_registry,
                checkpointer=checkpointer
        )
        
        
        log = await EventLog.open(
                session_id=SESSION_ID ,
                workspace_root=PROJECT_DIR,
                version=VERSION,
        )
        emitter = EventEmitter(log)
        
        THREAD_ID = SESSION_ID
    
        session_config: RunnableConfig = {
            "configurable": {
                "thread_id": THREAD_ID,
                "emitter":   emitter,
            },
            "recursion_limit": 200,
        }
        
        # fresh session
        if not is_resume:
            await log.append("session_start", {
                "workspace_root": PROJECT_DIR,
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
            
        # default loop state 
        is_session_started = False
        pending_command = None
        last_state = None
        selected_mode: str = "normal"  # user's current choice (changedd by '/mode')
        executed_mode: str = "normal"  # mode the last graph run used
        
        # resume path
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
            except FileNotFoundError as e:
                await sandbox.destroy()
                print(f"[resume error] {e}")
                return

            selected_mode = resumed.selected_mode
            executed_mode = resumed.executed_mode
            is_session_started = resumed.is_session_started
            last_state = resumed.last_state
            
            if resumed.thread_id and resumed.thread_id != THREAD_ID:
                THREAD_ID = resumed.thread_id
                session_config["configurable"]["thread_id"] = THREAD_ID
                print(f"[resume] using forked thread: {THREAD_ID}")

        # banner
        session = PromptSession()
        
        print_formatted_text(
            FormattedText([(
                "yellow", 
                f"\nVAMP-CLI"
            )]),
            end="\n"
        )
        print_formatted_text(
            FormattedText([(
                "yellow", 
                f"\nSession: {SESSION_ID}" + (" (resumed)" if is_resume else "")
            )]),
            end="\n"
        )
        print_formatted_text(
            FormattedText([(
                "yellow", 
                "\nCommands:\n-- /mode: select normal or plan mode.\n-- /exit or /quit: exit cli\n-- /fork: fork a session.\n-- /branches: view branches of session.\n-- /compat: conversation compaction.\n-- /config: change model and profile configurations.\n\n"
            )]),
            end="\n"
        )

        
        if resumed:
            # print(f"events loaded: {len(resumed.events)}")
            # print(f"checkpointer: {resumed.checkpoint_source}")
            # print(f"selected mode: {resumed.selected_mode}")
            # print(f"executed mode: {resumed.executed_mode}\n")
            for w in resumed.warnings:
                print(f"\n  [warn] {w}\n")
            
            print_transcript(resumed.events)
            print("\n-- End of history --\n")
                
        

        # resume-parked-interrupt: run once before the loop 
        if resumed and resumed.pending_interrupt:
            pi = resumed.pending_interrupt
            checkpoint_data = pi.get("payload") or {}
            interrupt_kind  = pi.get("kind") or "unknown"

            print("=" * 60)
            print("RESUMED SESSION IS PARKED AT AN OUTSTANDING INTERRUPT")
            print("=" * 60 + "\n")

            if checkpoint_data.get("implementation_permission"):
                print("PLAN APPROVAL REQUESTED")
                print(f"\nPlan:\n\n{checkpoint_data.get('plan', '')}")
            elif checkpoint_data.get("requires_tool_approval"):
                print("SECURITY VERIFICATION INTERCEPTED")
                calls = checkpoint_data.get("dangerous_calls", [])
                print(f"\n{len(calls)} dangerous call(s) flagged; "
                      f"{checkpoint_data.get('safe_call_count', 0)} safe call(s) "
                      f"will also run on approval.\n")
                for c in calls:
                    print(f"  Tool: {c['tool_name']}")
                    print(f"  Args: {json.dumps(c['tool_args'], indent=2)}\n")
                print(f"System Message: {checkpoint_data.get('warning', '')}")
            else:
                print(f"Unrecognised interrupt payload:\n{checkpoint_data}")

            print()
            choice = (
                (await session.prompt_async(
                    "Do you authorize the above ask? (yes/no/abort): "
                ))
                .strip().lower()
            )

            if choice in ("abort", "a", "quit", "exit"):
                # Leave the interrupt unresolved but close cleanly.
                await log.append("interrupt_response", {
                    "kind": interrupt_kind,
                    "approved": False,
                    "reason": "user aborted resume",
                })
                await log.close(reason="user_abort_resume")
                await sandbox.destroy()
                return

            if choice in ("y", "yes"):
                await log.append("interrupt_response", {
                    "kind": interrupt_kind,
                    "approved": True,
                    "reason": None,
                })
                pending_command = Command(
                    resume=FeedbackResponse(approved=True)
                )
            else:
                reason = (await session.prompt_async("Reason: ")).strip()
                await log.append("interrupt_response", {
                    "kind": interrupt_kind,
                    "approved": False,
                    "reason": reason,
                })
                pending_command = Command(
                    resume=FeedbackResponse(approved=False, reason=reason)
                )
                
                 
        # main loop
        while True:
            try:
                with patch_stdout():
                    # 0. user selects the mode:
                    if pending_command is None:
                        # 1. get the query from the user
                        user_input = (await session.prompt_async(
                            HTML("<b><ansicyan>\nYou:</ansicyan></b> ")
                        )).strip()
                        if not user_input:
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
                                print("No branches.")
                                continue

                            print(f"\n{len(leaves)} branch tip(s) in this session:\n")
                            for i, leaf in enumerate(leaves, 1):
                                leaf_chain = load_chain(sessions_dir, SESSION_ID, leaf_uuid=leaf)
                                last_user = next(
                                    (e for e in reversed(leaf_chain)
                                    if e.get("type") == "user_message"),
                                    None,
                                )
                                preview = ""
                                if last_user:
                                    preview = ((last_user.get("data") or {}).get("content") or "")[:60]
                                    preview = preview.replace("\n", " ")
                                marker = " <- current" if leaf == log.head_uuid else ""
                                print(f"  [{i}] {leaf[:8]}  {preview}{marker}")

                            pick = (await session.prompt_async("Switch to branch [1-N] or 'cancel': ")).strip()
                            if pick.lower() in ("", "cancel", "c"):
                                continue
                            try:
                                idx = int(pick)
                                if idx < 1 or idx > len(leaves):
                                    print("Out of range.")
                                    continue
                            except ValueError:
                                print("Not a number.")
                                continue

                            target_leaf = leaves[idx - 1]

                            # Move the head; do NOT write a fork event - this is a checkout, not a fork.
                            await log.set_head(target_leaf)

                            # Rebuild state for the new branch.
                            chain = load_chain(sessions_dir, SESSION_ID, leaf_uuid=target_leaf)
                            new_state = reconstruct_agent_state(chain)
                            new_thread = current_thread_id(chain, default=SESSION_ID)

                            # Move to that branch's checkpointer thread.
                            THREAD_ID = new_thread
                            session_config["configurable"]["thread_id"] = THREAD_ID

                            # Refresh CLI-side state.
                            disp = derive_display_state(chain)
                            selected_mode = disp["selected_mode"]
                            executed_mode = disp["executed_mode"]
                            is_session_started = disp["is_session_started"]
                            last_state = new_state

                            print(f"Switched to branch {target_leaf}. "
                                f"thread={THREAD_ID}  mode={selected_mode}  "
                                f"messages={len(new_state['coder_messages'])}c/"
                                f"{len(new_state['planner_messages'])}p")
                            print_transcript(chain, header=f"-- Branch {target_leaf} --")
                            print("\n-- End of history --\n")
                            continue

                        # /fork
                        if user_input == "/fork" or user_input.startswith("/fork "):
                            chain = load_chain(sessions_dir, SESSION_ID)
                            ums   = list_user_messages(chain)

                            if not ums:
                                print("No user messages to fork to.")
                                continue

                            shadowed_count = sum(1 for m in ums if m["shadowed"])
                            header = "\nUser messages on this branch"
                            if shadowed_count:
                                header += f" ({shadowed_count} compacted)"
                            print(header + ":\n")

                            for m in ums:
                                ts = (m["timestamp"] or "")[11:19]
                                preview = m["content"].replace("\n", " ")[:70]
                                tag = "  [compacted]" if m["shadowed"] else ""
                                print(f"  [{m['index']}] {ts}  {preview}{tag}")

                            # footer explaining fork semantics for compacted branches
                            any_compaction = any(e.get("type") == "compaction" for e in chain)
                            if any_compaction:
                                print()
                                print("Note: rewinding creates a new branch from that point. Because the")
                                print("compaction event currently sits at the tip of this branch, any")
                                print("fork goes to a point BEFORE it - the new branch will show the")
                                print("original (uncompacted) messages from that point on.")

                            if user_input.startswith("/fork "):
                                pick = user_input[len("/fork "):].strip()
                            else:
                                pick = (await session.prompt_async(
                                    "Rewind to BEFORE message [1-N] or 'cancel': "
                                )).strip()

                            if pick.lower() in ("", "cancel", "c"):
                                continue
                            try:
                                idx = int(pick)
                            except ValueError:
                                print("Not a number.")
                                continue
                            if idx < 1 or idx > len(ums):
                                print("Out of range.")
                                continue

                            target = ums[idx - 1]

                            if target["shadowed"]:
                                print(f"\nMessage [{idx}] has been compacted away.")
                                print("Rewinding to it will produce a new branch where that compaction")
                                print("does not apply - the original messages from that point will be")
                                print("restored on the new branch.")
                                confirm = (await session.prompt_async("Proceed? [y/N]: ")).strip().lower()
                                if confirm not in ("y", "yes"):
                                    print("Cancelled.")
                                    continue

                            fork_point = target["parent_uuid"]
                            if not fork_point:
                                print("Cannot fork before the first event.")
                                continue

                            print(f"\nRewinding before message [{idx}]: \"{target['content'][:60]}\"")
                            print("(The old branch is preserved; nothing is deleted.)\n")

                            try:
                                result = await fork_at_event(
                                    log,
                                    session_id=SESSION_ID,
                                    fork_point_uuid=fork_point,
                                    graph=graph,
                                    reason=f"user_rewind_before_user_message_{idx}",
                                )
                            except Exception as e:
                                print(f"[fork failed] {e!r}")
                                continue

                            THREAD_ID = result["thread_id"]
                            session_config["configurable"]["thread_id"] = THREAD_ID
                            last_state = result["state"]

                            chain = load_chain(sessions_dir, SESSION_ID, leaf_uuid=result["fork_uuid"])
                            disp = derive_display_state(chain)
                            selected_mode = disp["selected_mode"]
                            executed_mode = disp["executed_mode"]
                            is_session_started = disp["is_session_started"]

                            print(f"Forked. new_thread={THREAD_ID}")
                            print(f"        state: {len(last_state['coder_messages'])} coder / "
                                f"{len(last_state['planner_messages'])} planner messages, "
                                f"iteration={last_state.get('iteration', 0)}")
                            print("Next message you type starts the new branch.\n")

                            print_transcript(chain, header=f"-- Forked branch {result['fork_uuid'][:8]} --")
                            print("\n-- End of history --\n")
                            continue
                        
                        # /compact
                        if user_input == "/compact" or user_input.startswith("/compact "):
                            # parse optional keep_turns
                            keep = DEFAULT_KEEP_TURNS
                            if user_input.startswith("/compact "):
                                try:
                                    keep = int(user_input[len("/compact "):].strip())
                                    if keep < 0:
                                        raise ValueError
                                except ValueError:
                                    print("Usage: /compact [keep_turns]  (default 4)")
                                    continue

                            chain = load_chain(sessions_dir, SESSION_ID)
                            plan = plan_compaction_range(chain, keep_turns=keep)
                            if plan is None:
                                print(f"Nothing to compact - you have ≤ {keep} user turns on this branch.")
                                continue

                            if pending_command is not None:
                                print("Cannot compact while resuming an interrupt. Resolve it first.")
                                continue

                            print(f"\nWill compact {plan['event_count']} events "
                                f"covering the earliest user turns up to (but not including) "
                                f"the last {keep} - roughly ~{plan['est_tokens']} tokens.")
                            print(f"Keeping the last {keep} user turns intact.")
                            confirm = (await session.prompt_async("Proceed? [Y/n]: ")).strip().lower()
                            if confirm not in ("", "y", "yes"):
                                print("Cancelled.")
                                continue

                            print("Summarizing...")
                            try:
                                result = await compact_session(
                                    log,
                                    chain=chain,
                                    graph=graph,
                                    session_config=session_config,
                                    model=coder_profile.model if selected_mode == "normal" else planner_profile.model,
                                    keep_turns=keep,
                                    channel="both",
                                )
                            except ValueError as e:
                                print(f"[compact] {e}")
                                continue
                            except Exception as e:
                                print(f"[compact failed] {e!r}")
                                continue

                            last_state = result["new_state"]
                            print(f"\nCompacted {result['event_count']} events: "
                                f"~{result['tokens_before']} → ~{result['tokens_after']} tokens.")
                            print("Summary preview:\n")
                            preview = result["summary"].strip()
                            print("  " + preview.replace("\n", "\n  "))
                            # if len(preview) > 600:
                            #     print("  ...")
                            print()
                            continue
                        
                        # /exit or /quit
                        if user_input.lower() in ["/exit", "/quit"]:
                            print("Stopping active workspace containers...")
                            await log.close(reason="user_exit")
                            await sandbox.destroy()
                            return

                        # /mode
                        if user_input == "/mode":
                            picked = (
                                (await session.prompt_async("Select Mode [plan/normal]: "))
                                .strip()
                                .lower()
                            )

                            if picked not in ["plan", "normal"]:
                                print("Type the correct mode spelling.")
                                print("Mode not selected. try again...")
                                continue
                            
                            prev = selected_mode
                            selected_mode = picked
                            
                            if prev != selected_mode:
                                await log.append("mode_switch", {
                                    "from": prev,
                                    "to":   selected_mode,
                                })
                                emitter.last_input_tokens = 0
                                print(f"{selected_mode} mode selected. Continue...")
                            else:
                                print(f"Already in {selected_mode} mode.")
                            continue

                        token_tracker.start()
                        new_mode = selected_mode
                        
                        # Persist the user's message before building payload.
                        await log.append("user_message", {
                            "content": user_input,
                            "mode":    new_mode,
                        })
                        
                        # auto-compaction gate
                        # Fires only when the previous turn's final prompt was
                        # at/above the threshold. Runs synchronously so the
                        # checkpointer and last_state are consistent before the
                        # graph is invoked.
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
                        if auto is not None:
                            last_state = auto["new_state"]
                            # Reset the tracked input size after compaction so
                            # we don't retrigger on the next turn before the
                            # model has actually seen the new prompt size.
                            emitter.last_input_tokens = 0


                        # 2. Build or pass updates to the thread state
                        if not is_session_started:
                            # first message so initialize all seed states
                            payload = {
                                "planner_messages": [
                                    SystemMessage(
                                        content=planner_profile.system_prompt,
                                        id="planner_system_prompt"
                                    ),
                                    HumanMessage(content=user_input),
                                ],
                                "coder_messages": [
                                    SystemMessage(
                                        content=coder_profile.system_prompt,
                                        id="coder_system_prompt"
                                    ),
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
                            # recurring turns

                            # context sync on mode switch
                            sync = mode_switch_sync(
                                prev_mode=executed_mode,
                                new_mode=new_mode,
                                last_state=last_state,
                            )

                            message_type = (
                                "planner_messages"
                                if new_mode == "plan"
                                else "coder_messages"
                            )

                            payload = {
                                message_type: sync.get(message_type, [])
                                + [HumanMessage(content=user_input)],
                                "mode": new_mode,
                            }

                            # force a fresh plan when moving from normal -> plan
                            if new_mode == "plan":
                                payload["is_plan_injected"] = False

                            # dropping stale plan when moving from plan -> normal
                            if new_mode == "normal" and executed_mode == "plan":
                                payload["plan"] = ""

                        executed_mode = new_mode

                    else:
                        payload = pending_command
                        pending_command = None

                    # 2. Fire execution step
                    usage = await streaming(graph, payload, session_config, emitter)
                    token_tracker.add(usage)

                    # interrupt handling
                    # 3. Handle runtime verification interrupts
                    state_snapshot = await graph.aget_state(session_config)
                    result = state_snapshot.values
                    # cache full state for next switch
                    last_state = result

                    active_interrupts = getattr(state_snapshot, "interrupts", [])
                    if not active_interrupts and state_snapshot.tasks:
                        # check inside individual graph task state frames
                        for task in state_snapshot.tasks:
                            if getattr(task, "interrupts", None):
                                active_interrupts = task.interrupts
                                break

                    if active_interrupts:
                        raw_interrupt = active_interrupts[0]
                        checkpoint_data = getattr(raw_interrupt, "value", raw_interrupt)
                        
                        # classify & emit interrupt_request
                        
                        # plan approval
                        if checkpoint_data.get("implementation_permission"):  # type: ignore
                            interrupt_kind = "plan_approval"
                            prompt_text = "Do you authorize the above plan? (yes/no): "

                        # tool approval
                        elif checkpoint_data.get("requires_tool_approval"):  # type: ignore
                            interrupt_kind = "tool_approval"
                            prompt_text = "Do you authorize the above ask? (yes/no): "

                        else:
                            # unknown payload - surface raw for debugging
                            interrupt_kind = "unknown"
                            prompt_text = "Do you authorize the above ask? (yes/no): "
                            
                        await log.append("interrupt_request", {
                            "kind": interrupt_kind,
                            "payload": checkpoint_data,
                        })
                        
                        # display
                        print("\n" + "*" * 40)
                        if checkpoint_data.get("implementation_permission"): # type: ignore
                            print("PLAN APPROVAL REQUESTED")
                            print(f"\nPlan:\n\n{checkpoint_data.get('plan', '')}") # type: ignore
                            
                        elif checkpoint_data.get("requires_tool_approval"): # type: ignore
                            print("SECURITY VERIFICATION INTERCEPTED")
                            calls = checkpoint_data.get("dangerous_calls", []) # type: ignore
                            print(
                                f"\n{len(calls)} dangerous call(s) flagged; "
                                f"{checkpoint_data.get('safe_call_count', 0)} safe call(s) will " # type: ignore
                                f"also run on approval.\n"
                            )
                            for c in calls:
                                print(f"  Tool: {c['tool_name']}")
                                print(f"  Args: {json.dumps(c['tool_args'], indent=2)}")
                                print()
                            print(f"System Message: {checkpoint_data.get('warning', '')}") # type: ignore
                            
                        else:
                            print(f"Unrecognised interrupt payload:\n{checkpoint_data}")
                        print("*" * 40 + "\n")

                        # collect response
                        choice = (
                            (
                                await session.prompt_async(
                                    "Do you authorize the above ask? (yes/no): "
                                )
                            )
                            .strip()
                            .lower()
                        )

                        if choice in ("y", "yes"):
                            print("Authorized. Continuing execution...")
                            await log.append("interrupt_response", {
                                "kind": interrupt_kind,
                                "approved": True,
                                "reason": None,
                            })
                            pending_command = Command(
                                resume=FeedbackResponse(approved=True)
                            )
                            continue
                        else:
                            reason = (await session.prompt_async("Reason: ")).strip()
                            await log.append("interrupt_response", {
                                "kind": interrupt_kind,
                                "approved": False,
                                "reason": reason,
                            })
                            print("Blocked. Returning denial to agent history...")
                            pending_command = Command(
                                resume=FeedbackResponse(approved=False, reason=reason)
                            )
                            continue

                    token_tracker.close()

            except KeyboardInterrupt:
                print("\n[interrupted - returning to prompt]")
                pending_command = None
                continue

            except Exception as e:
                await log.close(reason="error")
                await sandbox.destroy()
                print(f"[runtime error] {e}")
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

    if list_flag:
        sessions_dir = Path.home() / ".vamp" / "sessions"
        rows = list_sessions(sessions_dir)
        if not rows:
            print("No sessions found.")
            return
        print(f"\n{len(rows)} session(s) in {sessions_dir}:\n")
        for r in rows:
            started = (r["started_at"] or "")[:19]
            msg = (r["first_message"] or "")[:70].replace("\n", " ")
            print(f"  {r['session_id']}  {started}  {msg}")
        print()
        return
    
    asyncio.run(main(resume_id=resume_id))
    

if __name__ == "__main__":
    start()