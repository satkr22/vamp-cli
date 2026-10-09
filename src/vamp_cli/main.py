import os
# os.environ["LITELLM_LOG"] = "DEBUG"

import logging
from pathlib import Path

from vamp_cli.config.loader import load_config
from vamp_cli.llm.router import ModelRouter
from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.prompts.prompts import PromptStore
from vamp_cli.agent.runtime.runtime import AgentRuntime
from vamp_cli.llm.adapter_registry import AdapterRegistry
from vamp_cli.llm.usage import UsageTracker, _UsageCallback

from vamp_cli.tools.files import FileTools
from vamp_cli.tools.execute_cmd import ExecuteCommandTool
from vamp_cli.tools.tools_classes import *

from vamp_cli.workspace.workspace import Workspace
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.tools.diagnostic import DiagnosticTools
from vamp_cli.sandbox.terminal import Terminal
from vamp_cli.sandbox.docker import DockerSandbox, SandboxConfig

from vamp_cli.agent.graph import create_graph
from vamp_cli.agent.roles import AgentRole
from vamp_cli.agent.status import AgentStatus
from vamp_cli.logging_setup import setup

from langchain_core.messages import HumanMessage, SystemMessage


log = logging.getLogger(__name__)

PROJECT_DIR = str(Path.cwd())

def build_startup():
    
    # --- services ---------------------------------------------------------
    
    workspace = Workspace(root=PROJECT_DIR)
    ignore = IgnoreMatcher(repo_root=str(workspace.root))
    diagnostic = DiagnosticTools(workspace=workspace)
    sandbox_config = SandboxConfig()
    d_sandbox = DockerSandbox(workspace, sandbox_config)
    d_sandbox.start()
    terminal = Terminal(sandbox=d_sandbox)
    
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
        WriteFileTool(file_tools),
        ApplySearchReplaceTool(file_tools),
        FileSearchTool(file_tools),
        RipgrepSearchTool(file_tools),
        ExecuteCommandToolDef(exec_tool),
    ]

    # --- tool registry ---------------------------------------------------------
    registry = ToolRegistry()
    registry.register_many(tools)

    log.info("Registered %d tools: %s", len(registry), registry.list_tool_names())
    
    return registry, d_sandbox


def create_runtime():

        config = load_config(path="src/vamp_cli/config/config.yaml")
        
        adapter = AdapterRegistry()

        tool_registry, sandbox = build_startup()

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
        
def main():
    
    setup(level=logging.DEBUG, log_dir=f"{PROJECT_DIR}/logs")
    
    agent_runtime, tool_registry, sandbox = create_runtime()     
    
    agent_profile = agent_runtime.resolve(AgentRole.DEFAULT)
        
    graph = create_graph(
        model=agent_profile.model,
        tool_registry=tool_registry
    )
    
    result = graph.invoke({
        "messages": [
            SystemMessage(
                content=agent_profile.system_prompt
            ),
            HumanMessage(
                content="read all the tool descpritions and tool whose access you have and give me the details and tell me how sufficient these tools are for a coding agent and do u need more tool to be more efficient coding agent ??"
            )
        ],
        "task": "read all the tool descpritions and tool whose access you have and give me the details and tell me how sufficient these tools are for a coding agent and do u need more tool to be more efficient coding agent ??",
        "iteration": 0,
    })
    
    for message in result["messages"]:
        # print(type(message))
        print(message)
        
    usage_dict = agent_runtime._model_router.usage()
    print(usage_dict)
        
    sandbox.stop()
        
        
if __name__ == "__main__":
    main()
    
    
    
# content="run sha256_hash.py in the root dir with an example string and show me the output and do not chabe ay other file at all"

# content="can u create a small python file which will convert a string into a sha-256 key in the root dir of project and also test it with a string example and show me the result and do not chnage any other file at all?"

# content="can you tell me how sandbox is working for this coding agent project??"
                