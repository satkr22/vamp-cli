import os
import sys
import logging
import asyncio
from pathlib import Path
from typing import Any
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from vamp_cli.workspace.workspace import Workspace
from vamp_cli.config.loader import load_config
from vamp_cli.prompts.prompts import PromptStore

from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.utils.token_tracker import Token_Tracker

from vamp_cli.sandbox.terminal import Terminal
from vamp_cli.sandbox.docker import DockerSandbox, SandboxConfig
from vamp_cli.sandbox.manager import SandboxManager

from vamp_cli.llm.router import ModelRouter
from vamp_cli.llm.adapter_registry import AdapterRegistry
from vamp_cli.llm.usage import UsageTracker, _UsageCallback

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

from vamp_cli.logging_setup.logging_setup import setup

from langgraph.types import Command
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.runnables import RunnableConfig

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

    config = load_config(path="src/vamp_cli/config/config.yaml")

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
    graph, payload: dict | Command, session_config: RunnableConfig
) -> Any:

    call_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    header_printed = False
    thinking_open = False

    if isinstance(payload, dict):
        print("mode:", payload["mode"])

    try:

        async for event in graph.astream_events(
            payload, config=session_config, version="v2"
        ):
            kind = event["event"]

            if kind == "on_chat_model_start":
                # First time we know a model will run, print the header.
                if not header_printed:
                    print("\nAgent: \n", end="", flush=True)
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
                        print("Thinking:\n", end="", flush=True)
                        thinking_open = True
                    print(reasoning_chunk, end="", flush=True)

                # 2. capture final human text
                elif chunk.content:
                    if thinking_open:
                        print("\n\nResponse:\n", end="", flush=True)
                        thinking_open = False
                    print(chunk.content, end="", flush=True)

            elif kind == "on_chat_model_end":
                output = event["data"].get("output")
                um = getattr(output, "usage_metadata", None)
                if um:
                    call_usage["input_tokens"] += um.get("input_tokens", 0)
                    call_usage["output_tokens"] += um.get("output_tokens", 0)
                    call_usage["total_tokens"] += um.get("total_tokens", 0)

    except KeyboardInterrupt as e:
        print("\n[aborted by user]")
        return call_usage
    except Exception as e:
        print("\n[Some error happened]")
        return call_usage

    print("\n")
    return call_usage


turn_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
turn_open = True


def _add_usage(acc, delta):
    if not delta:
        return
    for k in acc:
        acc[k] += delta.get(k, 0)


def _close_turn():
    global turn_usage, turn_open
    if not turn_open:
        return
    print("Token Usage (this turn):")
    print(f"  Input : {turn_usage['input_tokens']}")
    print(f"  Output: {turn_usage['output_tokens']}")
    print(f"  Total : {turn_usage['total_tokens']}")
    print()
    turn_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    turn_open = False


async def main():

    token_tracker = Token_Tracker()

    # setup(level=logging.DEBUG, log_dir=f"{PROJECT_DIR}/logs")

    agent_runtime, tool_registry, sandbox = await create_runtime()

    coder_profile = agent_runtime.resolve(AgentRole.CODER)
    planner_profile = agent_runtime.resolve(AgentRole.PLANNER)

    graph = create_graph(
        coder_model=coder_profile.model,
        planner_model=planner_profile.model,
        tool_registry=tool_registry,
    )

    # Session configuration context tracking thread
    session_config: RunnableConfig = {
        "configurable": {"thread_id": "interactive_vamp_cli_session"},
        "recursion_limit": 200,
    }

    session = PromptSession()

    print("\nVAMP-CLI Agent Workspace Initialized.")

    print("\nType /mode to select normal or plan mode.")

    print("\nType '/exit' or '/quit' to exit this and clear docker instances safely.\n")

    is_session_started = False
    pending_command = None
    last_state = None

    selected_mode: str = "normal"  # user's current choice (changedd by '/mode')
    executed_mode: str = "normal"  # mode the last graph run used

    while True:
        try:
            with patch_stdout():
                # 0. user selects the mode:
                if pending_command is None:
                    # 1. get the query from the user
                    user_input = (await session.prompt_async("You: ")).strip()
                    if not user_input:
                        continue
                    if user_input.lower() in ["/exit", "/quit"]:
                        print("Stopping active workspace containers...")
                        await sandbox.destroy()
                        return

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
                        selected_mode = picked
                        print(f"{selected_mode} mode selected. Continue...")
                        continue

                    token_tracker.start()
                    new_mode = selected_mode

                    # 2. Build or pass updates to the thread state
                    if not is_session_started:
                        # first message so initialize all seed states
                        payload = {
                            "planner_messages": [
                                SystemMessage(content=planner_profile.system_prompt),
                                HumanMessage(content=user_input),
                            ],
                            "coder_messages": [
                                SystemMessage(content=coder_profile.system_prompt),
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
                usage = await streaming(graph, payload, session_config)
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

                    print("\n" + "*" * 40)

                    # plan approval
                    if checkpoint_data.get("implementation_permission"):  # type: ignore
                        print("PLAN APPROVAL REQUESTED")
                        print(f"\nPlan:\n\n{checkpoint_data.get('plan', '')}")  # type: ignore

                    # tool approval
                    elif checkpoint_data.get("requires_tool_approval"):  # type: ignore
                        print("SECURITY VERIFICATION INTERCEPTED")
                        calls = checkpoint_data.get("dangerous_calls", [])  # type: ignore
                        print(
                            f"\n{len(calls)} dangerous call(s) flagged; "
                            f"{checkpoint_data.get('safe_call_count', 0)} safe call(s) will "  # type: ignore
                            f"also run on approval.\n"
                        )
                        for c in calls:
                            print(f"  Tool: {c['tool_name']}")
                            print(f"  Args: {json.dumps(c['tool_args'], indent=2)}")
                            print()
                        print(f"System Message: {checkpoint_data.get('warning', '')}")  # type: ignore

                    else:
                        # unknown payload — surface raw for debugging
                        print(f"Unrecognised interrupt payload:\n{checkpoint_data}")

                    print("*" * 40 + "\n")

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
                        pending_command = Command(
                            resume=FeedbackResponse(approved=True)
                        )
                        continue
                    else:
                        reason = (await session.prompt_async("Reason: ")).strip()
                        print("Blocked. Returning denial to agent history...")
                        pending_command = Command(
                            resume=FeedbackResponse(approved=False, reason=reason)
                        )
                        continue

                token_tracker.close()

        except KeyboardInterrupt:
            print("\n[interrupted — returning to prompt]")
            pending_command = None
            continue

        except Exception as e:
            await sandbox.destroy()
            print(f"[runtime error] {e}")
            return


if __name__ == "__main__":
    asyncio.run(main())
