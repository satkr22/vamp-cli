from vamp_cli.config.loader import load_config
from vamp_cli.llm.router import ModelRouter
from vamp_cli.llm.router import ModelRouter
from vamp_cli.tools.registry import ToolRegistry
from vamp_cli.prompts.prompts import PromptStore
from vamp_cli.agent.runtime import AgentRuntime
from vamp_cli.llm.adapter_registry import AdapterRegistry

def create_runtime() -> AgentRuntime:

        config = load_config(path="config.yaml")
        
        adapter = AdapterRegistry()

        tool_registry = ToolRegistry()

        prompt_store = PromptStore()

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