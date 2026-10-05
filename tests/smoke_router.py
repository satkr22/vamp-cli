"""One real model call through the config + router stack.

Run from the project root:
    export DASHSCOPE_API_KEY=...        # or put it in a .env file
    PYTHONPATH=src python scripts/smoke_router.py
"""

from agent.config.loader import load_config
from agent.llm.router import ModelRouter

config = load_config()
profile = config.profiles["default"]
llm = ModelRouter(config).get(profile.model)

print(f"profile 'default' -> model '{profile.model}' ({config.models[profile.model].model})")
print(llm.invoke("Say hello in five words.").content)
