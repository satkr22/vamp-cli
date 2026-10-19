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
- **Session persistence**: all conversations are durably logged as append-only JSONL with per-session SQLite checkpoints.
- **Session resumption**: resume any past session exactly where it left off, including parked interrupts.
- **Session listing**: list all past sessions with timestamps and first-message previews.
- **Forking**: rewind to any past user message and branch off a new conversation path without deleting the original.
- **Branch switching**: view and switch between conversation branches within a session.
- **Conversation compaction**: automatically or manually summarize early turns to stay within model context limits.

## Requirements

- Python 3.11 or newer
- Docker Engine or Docker Desktop, running and accessible to the current user
- An API key env var for each model configured in `~/.vamp/config.yaml` (it gets created in first run).

The first run automatically builds the `vamp-sandbox:latest` image from [`src/vamp_cli/sandbox/Dockerfile`](src/vamp_cli/sandbox/Dockerfile) when it is not already available locally. The image includes Python, Node.js, Git, `ripgrep`, `fd`, and common build tools.

## Installation

Create and activate a virtual environment, then install the project (all dependencies will be installed itself):

Virtual Env:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```
Package:

```bash
python -m pip install vamp-cli 
```

For development:

```bash
python -m pip install -e .
```

Confirm that Docker is available before starting the agent:

```bash
docker info
```

## Configuration

Model and profile configuration lives in [`/src/vamp_cli/config/default_config.yaml`](/src/vamp_cli/config/default_config.yaml)(this is used to create `config.yaml` for first run). After first run `~/.vamp/config.yaml` is created and vamp-cli reads from this always. API keys are read from environment variables named by each model's `api_key_env` field; keys are not read from YAML values directly.

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

### CLI Arguments

| Argument | Action |
| --- | --- |
| `--resume <id>` or `-r <id>` | Resume a previous session by its ID |
| `--list` or `-l` | List all past sessions |

Examples:

```bash
# List all past sessions
vamp --list

# Resume a specific session
vamp --resume 01a145b4-5103-7014-97da-d5ca9c871d68
```

### In-Session Commands

| Command | Action |
| --- | --- |
| `/mode` | Select `plan` or `normal` mode |
| `/exit` | Stop the session and remove the active sandbox container |
| `/quit` | Same as `/exit` |
| `/fork` | Rewind to a previous user message and create a new branch |
| `/fork <N>` | Fork directly to before message N (non-interactive) |
| `/branches` | View all branches in the current session and switch between them |
| `/compact` | Summarize early conversation turns to reduce context usage |
| `/compact <N>` | Compact keeping the last N turns intact (default: 4) |
| `/config` | Change configurations of models, coder or planner profiles, and tool access. |

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

## Session Persistence & Resumption

VAMP-CLI automatically saves every interaction so you can pick up where you left off.

### How Sessions Are Stored

Sessions are persisted in `~/.vamp/` using two complementary mechanisms:

| Storage | Location | Contents |
| --- | --- | --- |
| **JSONL Event Log** | `~/.vamp/sessions/<id>.jsonl` | Append-only, human-readable record of every event (messages, tool calls, mode switches, interrupts, forks, compactions). Each event has a UUID7, parent UUID, and timestamp. |
| **Head Sidecar** | `~/.vamp/sessions/<id>.head` | Contains the UUID of the current leaf (tip) of the active branch. |
| **SQLite Checkpoint** | `~/.vamp/checkpoints/<id>.sqlite` | LangGraph's internal graph state — full message objects, plan, todo list, iteration count, and pipeline flags. |

### Resuming a Session

When you resume a session with `vamp --resume <id>`, VAMP-CLI:

1. **Loads the event chain** — walks the `parentUuid` linked list from the current leaf back to `session_start`, reconstructing the full conversation history.
2. **Recovers CLI state** — replays the events to determine the current mode, whether the session had started, and whether a human-in-the-loop interrupt is still pending.
3. **Restores graph state** — reads the authoritative LangGraph state from the SQLite checkpointer. If the checkpointer is missing or corrupted, it falls back to reconstructing state from the JSONL event log (lossy but functional — preserves messages, reasoning content, plan, and todo list).
4. **Handles parked interrupts** — if the previous session was interrupted mid-approval (plan approval or dangerous tool verification), the user is immediately prompted to approve or deny before continuing.

### Platform-Independent

The `~/.vamp` directory is relative to the user's home directory, so sessions work across different projects and can be listed from anywhere — not just the original workspace directory.

## Forking & Branching

The JSONL event log uses a `parentUuid` chain instead of a linear sequence, which means conversations can branch.

### Forking (`/fork`)

Forking lets you rewind to any past user message and start a new branch from that point:

1. Type `/fork` to see a numbered list of user messages on the current branch.
2. Choose a message number to rewind to *before* that message.
3. VAMP-CLI reconstructs the agent state at that point, creates a new LangGraph checkpointer thread, and appends a `fork` event to the JSONL (pointing its `parentUuid` at the fork point).
4. The old branch is **never deleted** — it remains intact and can be switched back to at any time.

Compact (summarized) messages are also forkable. Forking to a compacted message creates a new branch where the original (unsummarized) messages are restored.

### Branch Switching (`/branches`)

Each fork creates a new branch tip (leaf). Type `/branches` to:

- See all branch tips with a short preview of the last user message on each.
- Identify the current branch (marked with `<- current`).
- Switch to any branch by number — this repoints the `.head` sidecar, reloads state from the JSONL, and switches the checkpointer thread.

## Conversation Compaction

Long conversations can exceed the model's context window. Compaction solves this by summarizing early turns.

### Manual Compaction (`/compact`)

Type `/compact` to summarize all but the last 4 user turns. Use `/compact <N>` to keep a different number of recent turns. The process:

1. Identifies the range of events to compact (earliest user messages up to, but not including, the keep boundary).
2. Builds a transcript from those events.
3. Calls the current model to produce a dense, information-rich summary.
4. Appends a `compaction` event to the JSONL (original events are kept — they are "shadowed", not deleted).
5. Replaces the checkpointer's message channels with the compacted view (summary marker + recent messages only).

### Auto-Compaction

When the last prompt's input tokens exceed **90%** of the model's context window (configurable via `DEFAULT_COMPACT_THRESHOLD`), VAMP-CLI automatically runs compaction before the next turn. This keeps sessions healthy without manual intervention.

A progress line is printed:
```
[auto-compact] context at 118,000/128,000 tokens (92.2%) — summarizing…
[auto-compact] done: 24 events, ~9,500 → ~600 tokens
```

## How it works

1. `vamp_cli.main` creates a workspace, ignore matcher, tool registry, model router, EventLog, and Docker sandbox.
2. The selected planner or coder profile supplies a system prompt and permitted tools.
3. LangGraph routes model output through tool classification, approval, and execution nodes.
4. File tools operate within the current workspace; terminal commands execute in the mounted Docker container.
5. An `EventEmitter` translates LangChain `astream_events` v2 frames into durable JSONL events in real time — `on_chat_model_start/stream/end` → `assistant_message`, `on_tool_start/end/error` → `tool_result`.
6. The session retains graph state via LangGraph's SQLite checkpointer so follow-up prompts can continue the current task.
7. On resume, the event log and checkpointer are rehydrated together — the event log provides the human-readable chain and fallback state, while the checkpointer is the authoritative source for the LangGraph agent state.

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
├── persistence/ Session event log, emitter, loader, fork, compaction, transcript
└── main.py      Interactive CLI entry point
```

### Persistence Module Details

```text
src/vamp_cli/persistence/
├── event_log.py    Append-only JSONL writer with async queue, UUID7, parent-chain, and .head sidecar
├── emitter.py      Translates LangChain astream_events v2 → event log entries
├── loader.py       Session rehydration: load_chain, derive_display_state,
│                   detect_pending_interrupt, reconstruct_agent_state, list_sessions
├── fork.py         Rewind to a past event and seed a new checkpointer thread
├── compaction.py   Summarize early turns, plan ranges, auto-compact on threshold
└── transcript.py   Render and print human-readable session transcripts
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