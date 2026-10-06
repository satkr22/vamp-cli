import logging
from pathlib import Path

from vamp_cli.config.loader import load_config
from vamp_cli.llm.router import ModelRouter
from vamp_cli.llm.router import ModelRouter
from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.prompts.prompts import PromptStore
from vamp_cli.agent.runtime.runtime import AgentRuntime
from vamp_cli.llm.adapter_registry import AdapterRegistry

from vamp_cli.tools.files import FileTools
from vamp_cli.tools.execute_cmd import ExecuteCommandTool
from vamp_cli.tools.tools_classes import *

from vamp_cli.workspace.workspace import Workspace
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.tools.diagnostic import DiagnosticTools
from vamp_cli.sandbox.terminal import Terminal
from vamp_cli.sandbox.base import Sandbox
from vamp_cli.sandbox.docker import DockerSandbox, SandboxConfig

log = logging.getLogger(__name__)

PROJECT_DIR = str(Path.cwd())

def build_startup():
    
    # --- services ---------------------------------------------------------
    workspace = Workspace(root=PROJECT_DIR)
    ignore = IgnoreMatcher(repo_root=str(workspace.root))
    diagnostic = DiagnosticTools(workspace=workspace)
    sandbox_config = SandboxConfig()
    d_sandbox = DockerSandbox(workspace, sandbox_config)
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
    
    return registry


def create_runtime() -> AgentRuntime:

        config = load_config(path="config.yaml")
        
        adapter = AdapterRegistry()

        tool_registry = build_startup()

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
        )