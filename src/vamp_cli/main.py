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
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.prompts.prompts import PromptStore

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

        model_router = ModelRouter(
            config=config,
            adapters=adapter
        )

        return AgentRuntime(
            config=config,
            model_router=model_router,
            tool_registry=tool_registry,
            prompt_store=prompt_store,
        ), tool_registry, sandbox
        
        
async def streaming(graph, payload: dict | Command, session_config: RunnableConfig) -> Any:
    
    last_node = None
    current_tool_id = None
    
    
    print("\nAgent: \n", end="", flush=True)
    
    async for event in graph.astream_events(payload, config=session_config, version="v2"):
        kind = event["event"]
        name = event["name"]
        
        # print("\nevent kind", kind, end="\n", flush=True)
        # print("event name", name)
        
        # Track which graph component or tool node is actively processing
        if "metadata" in event and "langgraph_node" in event["metadata"]:
            last_node = event["metadata"]["langgraph_node"]
            
        # A. CAPTURE CHAT MODEL STREAM CHUNKS (Final Output & Reasoning)
        if kind == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            
            if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
                print(f"\r[Thinking_1]: {chunk.reasoning_content}", end="", flush=True)
                
            elif "reasoning_content" in chunk.additional_kwargs:
                print(f"\r[Thinking_2]: {chunk.additional_kwargs['reasoning_content']}", end="", flush=True)
                
            elif "reasoning" in chunk.additional_kwargs:
                print(f"\r[Thinking_3]: {chunk.additional_kwargs['reasoning']}", end="", flush=True)
                
            # 2. Capture Final Human-Facing Markdown Text Response
            elif chunk.content:
                print(chunk.content, end="", flush=True)
            
            # 3. Capture Live Tool
                if chunk.tool_call_chunks:
                    for tc_chunk in chunk.tool_call_chunks:
                        # When a brand new tool frame begins generating
                        if tc_chunk.get("name"):
                            print(f"\n [Preparing Tool]: {tc_chunk['name']}(", end="", flush=True)
                        # Stream the dynamic parameters drop across your terminal canvas
                        if tc_chunk.get("args"):
                            print(tc_chunk["args"], end="", flush=True)
                            
        # B. CAPTURE TOOL EXECUTION TIMELINE (Sandbox Invocations & File Access)
        elif kind == "on_tool_start":
            print(f") -> Executing task target inside sandbox environment...")
                        
        elif kind == "on_tool_end":
            # Prints confirmation when the localized processing closes out safely
            output_snippet = str(event['data']['output'])[:120]
            print(f"[Tool Finished]: Returned response segment: {output_snippet}...")
            
    # print("\nstreaming end")
    print("\n")         
           
    
    return 1
    
        
        
        
        

async def main():
    
    # setup(level=logging.DEBUG, log_dir=f"{PROJECT_DIR}/logs")
    
    agent_runtime, tool_registry, sandbox = await create_runtime()  
    
       
    
    coder_profile = agent_runtime.resolve(AgentRole.CODER)
    planner_profile = agent_runtime.resolve(AgentRole.PLANNER)
    
    graph = create_graph(
        coder_model=coder_profile.model,
        planner_model=planner_profile.model,
        tool_registry=tool_registry
    )
    
    # Session configuration context tracking thread
    session_config: RunnableConfig = {"configurable": {"thread_id": "interactive_vamp_cli_session"}}
    
    session = PromptSession()
    
    print("\nVAMP-CLI Agent Workspace Initialized.")
    
    print("Type /mode to select normal or plan mode\n")
    print("Type 'exit' or 'quit' to exit this and clear docker instances safely.\n")

    is_session_started = False
    pending_command = None
    last_state = None
    current_mode: str  = "normal"
    
    while True:
        try:
            with patch_stdout():
                # 0. user selects the mode:
                if pending_command is None:
                    # 1. get the query from the user
                    user_input = (await session.prompt_async("You: ")).strip()
                    if not user_input:
                        continue
                    if user_input.lower() in ["exit", "quit"]:
                        print("Stopping active workspace containers...")
                        await sandbox.destroy()
                        return
                    
                    u_mode = "normal"
                    
                    if user_input == "/mode":
                        u_mode = (await session.prompt_async(
                            "Select Mode [plan/normal]: "
                        )).strip().lower()
                        
                        if u_mode not in ["plan", "normal"]:
                            print("Type the correct mode spelling.")
                            print("Mode not selected. try again...")
                            continue
                            
                        print(f"{u_mode} mode selected. Continue...")
                        continue
                        
                    new_mode = u_mode
                        
                
                    # 2. Build or pass updates to the thread state
                    if not is_session_started:
                        # first message so initialize all seed states
                        payload = {
                                
                            "planner_messages": [
                                SystemMessage(content=planner_profile.system_prompt),
                                HumanMessage(content=user_input)
                            ],
                            "coder_messages": [
                                SystemMessage(content=coder_profile.system_prompt),
                                HumanMessage(content=user_input)
                            ],
                            "task": user_input,
                            "mode": new_mode,
                            "plan": "",
                            "requires_execution": False,
                            "is_plan_injected": False,
                            "todo": [],
                            "iteration": 0,
                        }
                        is_session_started = True
                    else:
                        # recurring turns
                        
                        # CONTEXT SYNC on mode switch
                        sync = mode_switch_sync(
                            prev_mode=current_mode,
                            new_mode=new_mode,
                            last_state=last_state,
                        )
                        
                        message_type = "planner_messages" if new_mode == "plan" else "coder_messages"
                        
                        payload = {
                            message_type: 
                                sync.get(message_type, []) + [HumanMessage(content=user_input)],
                                
                            "mode": new_mode
                        }
                        
                        # force a fresh plan when moving from normal -> plan
                        if new_mode == "plan":
                            payload["is_plan_injected"] = False
                        
                        # dropping stale plan when moving from plan -> normal
                        if new_mode == "normal" and current_mode == "plan":
                            payload["plan"] = ""
                            
                    current_mode = new_mode

                        
                else:
                    payload = pending_command
                    pending_command = None 
                
                # 2. Fire execution step
                
                d = await streaming(graph, payload, session_config)
                
                # result = await graph.ainvoke(payload, config=session_config) # type: ignore
                
                # cache full state for next switch
                
                # interrupt handling
                
                # 3. Handle runtime verification interrupts
                state_snapshot = await graph.aget_state(session_config)
                
                result = state_snapshot.values
                
                last_state = result
                
                active_interrupts = getattr(state_snapshot, "interrupts", [])
                
                if not active_interrupts and state_snapshot.tasks:
                    # check inside individual graph task state frames
                    for task in state_snapshot.tasks:
                        if getattr(task, "interrupts", None):
                            active_interrupts = task.interrupts
                            break
                        
                if active_interrupts:
                    # Safe structure extraction 
                    raw_interrupt = active_interrupts[0]
                    checkpoint_data = getattr(raw_interrupt, "value", raw_interrupt)
                    
                    print("\n" + "*" * 40)
                    print("SECURITY VERIFICATION INTERCEPTED")
                    
                    if checkpoint_data.get('implementation_permission'): # type: ignore
                        print(f"\nPlan:\n\n{checkpoint_data.get('plan', '')}") # type: ignore
                    else:   
                        print(f"Requested Tool:  {checkpoint_data.get('tool_name', 'Unknown')}") # type: ignore
                        print(f"Arguments Passed: {json.dumps(checkpoint_data.get('tool_args', {}), indent=2)}") # type: ignore
                        
                    print(f"System Message:   {checkpoint_data.get('warning', '')}") # type: ignore
                    print("*" * 40 + "\n")
                    
                    choice = (await session.prompt_async(
                        "Do you authorize the above ask? (yes/no): "
                    )).strip().lower()
                    
                    if choice in ["y", "yes"]:
                        print("Authorized. Continuing execution...")
                        # Command unpauses the graph right where it froze, passing back the approval dict
                        user_response = FeedbackResponse(approved=True)
                        
                        pending_command = Command(resume=user_response)
                        
                    else:
                        reason = (await session.prompt_async(
                            "Reason: "
                        )).strip()
                        
                        print("Blocked. Returning execution exception back to agent history...")
                        
                        user_response = FeedbackResponse(approved=False, reason=reason)
                        
                        pending_command = Command(resume=user_response)
                else:
                    # 4. Print out the AI's final turn response text
                    # active_log = "planner_messages" if current_mode == "plan" else "coder_messages"
                    
                    # if result.get(active_log):
                    #     last_msg = result[active_log][-1]
                    #     if isinstance(last_msg, AIMessage) and last_msg.content:
                    #         print(f"\nAgent:\n{last_msg.content}")    
                    pass
        except KeyboardInterrupt:
            print("\nInterrupted — stopping sandbox.")
            await sandbox.destroy()
            return
        except Exception as e:
            await sandbox.destroy()
            print(f"[runtime error] {e}")
            return
       
if __name__ == "__main__":
    asyncio.run(main())
    
    





# how to make sandbox more secure for this project??
    
# content="run sha256_hash.py in the root dir with an example string and show me the output and do not chabe ay other file at all"

# content="can u create a small python file which will convert a string into a sha-256 key in the root dir of project and also test it with a string example and show me the result and do not chnage any other file at all?"

# content="can you tell me how sandbox is working for this coding agent project??"

# task: str = "read all the tool descpritions and tool whose access you have and give me the details and tell me how sufficient these tools are for a coding agent and do u need more tool to be more efficient coding agent ??"

# task: str = "how is the langgraph configured to achieve plan and normal mode in this project??"

# task: str = "write me a read_many_files tool for the llm and do not change any other code"
