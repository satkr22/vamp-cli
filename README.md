# VAMP-CLI

VAMP-CLI is an interactive coding agent for working in a local repository. It combines a planner, a coder, model fallbacks, workspace-aware file tools, and a Docker-backed command sandbox in one terminal application.

The agent can inspect and edit files, search a codebase, run commands, maintain a task list, and ask for confirmation before executing potentially dangerous operations.

> **Status:** VAMP-CLI is an early-stage project. The interfaces and default model configuration may change.

## Highlights

- Interactive terminal UI launched with the `vamp` command.
- Two operating modes:
  - **Normal mode**: work directly with the coding agent.
  - **Plan mode**: have the planner produce a plan and approve it before implementation.
- Workspace-scoped file operations and searches.
- Docker sandbox for command execution, with CPU, memory, process, timeout, and workspace-boundary limits.
- Human-in-the-loop approval for plan execution and commands classified as dangerous.
- Configurable models, profiles, prompts, streaming, reasoning, and model fallbacks.
- Token usage tracking and LangGraph-based agent orchestration.

## Requirements

- Python 3.11 or newer
- Docker Engine or Docker Desktop, running and accessible to the current user
- An API key for each model configured in `src/vamp_cli/config/config.yaml`

The first run automatically builds the `vamp-sandbox:latest` image from [`src/vamp_cli/sandbox/Dockerfile`](src/vamp_cli/sandbox/Dockerfile) when it is not already available locally. The image includes Python, Node.js, Git, `ripgrep`, `fd`, and common build tools.

## Installation

Create and activate a virtual environment, then install the project (all dependencies will be install itself):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Confirm that Docker is available before starting the agent:

```bash
docker info
```

## Configuration

Model and profile configuration lives in [`src/vamp_cli/config/config.yaml`](src/vamp_cli/config/config.yaml). API keys are read from environment variables named by each model's `api_key_env` field; keys are not read from YAML values directly.

The checked-in default configuration defines `qwen` and `openai`, so both variables must be present unless you edit the configuration to remove or replace one of those models:

```bash
export DASHSCOPE_API_KEY="your-dashscope-key"
export OPENAI_API_KEY="your-openai-key"
```

For a different provider, update the model's `model`, optional `api_base`, `api_key_env`, parameters, capabilities, and fallback list in the YAML file. Profiles select the model and the tools available to the planner or coder.

## Running VAMP-CLI

Run it from the repository you want the agent to work on:

```bash
vamp
```

The current directory becomes the agent workspace and is mounted at `/workspace` inside the sandbox. On startup, VAMP-CLI prints an interactive prompt:

```text
You:
```

Useful commands:

| Command | Action |
| --- | --- |
| `/mode` | Select `plan` or `normal` mode |
| `/exit` | Stop the session and remove the active sandbox container |
| `/quit` | Same as `/exit` |

When the agent requests approval, answer `yes` to continue or `no` and provide a reason to deny the action. Approvals are used for plan implementation and security-sensitive tool calls such as destructive commands, package installation, or network access.

Example requests:

```text
Inspect this repository and explain how the authentication flow works.
```

```text
/mode
Select Mode [plan/normal]: plan
Add tests for the configuration loader, then implement the changes after I approve the plan.
```

## How it works

1. `vamp_cli.main` creates a workspace, ignore matcher, tool registry, model router, and Docker sandbox.
2. The selected planner or coder profile supplies a system prompt and permitted tools.
3. LangGraph routes model output through tool classification, approval, and execution nodes.
4. File tools operate within the current workspace; terminal commands execute in the mounted Docker container.
5. The session retains graph state so follow-up prompts can continue the current task.

The main package areas are:

```text
src/vamp_cli/
├── agent/       LangGraph state, nodes, routing, and runtime profiles
├── config/      YAML loading, validation, model/profile configuration
├── llm/         LiteLLM-backed model routing, adapters, fallbacks, usage
├── prompts/     Planner and coder system prompts
├── sandbox/     Docker lifecycle and command execution
├── tools/       File, search, diagnostic, todo, and command tools
├── workspace/   Workspace path-safety enforcement
└── main.py      Interactive CLI entry point
```

## Development and tests

Run the automated test suite from the repository root:

```bash
python -m pytest
```

Some sandbox and system-verification tests require Docker. The manual security and tool-operation scenarios are documented in [`tests/TEST_VERIFICATION.md`](tests/TEST_VERIFICATION.md).

For a quick syntax check:

```bash
python -m compileall src
```

## Security and operational notes

- The host project directory is mounted into the sandbox at `/workspace`.
- The default sandbox limits are 2 CPUs, 2 GB of memory, 256 processes, and 30 seconds per command.
- Commands are executed with `/bin/bash -lc` inside the container.
- The default sandbox has network access; set `network_enabled: false` in the sandbox configuration if an offline container is required.
- Treat model output as untrusted. Review approval prompts carefully, especially for deletion, package installation, credential access, and network operations.
- The current graph limits an agent run to 15 iterations to prevent unbounded tool loops.

## License

VAMP-CLI is released under the [MIT License](LICENSE).
