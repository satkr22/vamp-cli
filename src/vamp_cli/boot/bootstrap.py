
import sys

from vamp_cli.workspace.workspace import Workspace
from vamp_cli.config.loader import load_config
from vamp_cli.sandbox.terminal import Terminal
from vamp_cli.sandbox.docker import SandboxConfig
from vamp_cli.sandbox.manager import SandboxManager
from vamp_cli.tools.files import FileTools
from vamp_cli.tools.diagnostic import DiagnosticTools
from vamp_cli.tools.execute_cmd import ExecuteCommandTool
from vamp_cli.tools.tools_classes import *
from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.agent.runtime.runtime import AgentRuntime
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.prompts.prompts import PromptStore



async def build_startup(project_dir: str):

    # services
    workspace = Workspace(root=project_dir)
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

    return registry, d_sandbox


async def create_runtime(project_dir: str):

    from vamp_cli.llm.router import ModelRouter
    from vamp_cli.llm.adapter_registry import AdapterRegistry
    
    config = load_config()

    adapter = AdapterRegistry()

    tool_registry, sandbox = await build_startup(project_dir=project_dir)

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
    
    
