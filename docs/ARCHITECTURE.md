# Project Architecture

## Overview

Shai is a Python command-line assistant with two entry points. Agent mode combines a language model with local project tools; chat mode provides conversation without local project access.

```mermaid
flowchart TD
    User[User] -->|Input| Agent[agent.py\nCoding Agent Mode]
    User -->|Input| Chat[chat.py\nChat Mode]

    subgraph AgentMode["Agent Mode (agent.py)"]
        Agent --> Loop[Agent Loop\nTool Schemas & Execution]
        Loop --> Tools[Tool Registry]
        Tools --> Discovery[Discovery & Code Intelligence]
        Tools --> Reading[Reading Tools]
        Tools --> Editing[Editing Tools]
        Tools --> Verification[Verification Tools]
        Tools --> TaskMgmt[Task Management]
        Tools --> GitOps[Git Operations]
    end

    subgraph ChatMode["Chat Mode (chat.py)"]
        Chat --> ChatLoop[Conversation Loop]
    end

    subgraph SharedModules["Shared Modules"]
        ProviderSel[modules/provider_selection.py]
        ConvHist[modules/conversation_history.py]
        Safeguards[modules/agent_safeguards.py]
        Logging[modules/logging_config.py]
        FileDisplay[modules/file_change_display.py]
        Reasoning[modules/reasoning_handlers/]
        ContextMeter[modules/context_meter.py]
        ToolsMod[modules/tools/\nShared Read-Only Tools]
    end

    Loop --> SharedModules
    ChatLoop --> SharedModules

    Discovery --> ProjectDir[(Project Directory)]
    Reading --> ProjectDir
    Editing --> ProjectDir
    Verification --> ProjectDir
    GitOps --> ProjectDir

    ProviderSel -->|API Calls| LLM[(LLM Provider APIs)]
    Loop --> LLM
    ChatLoop --> LLM

    Config[assets/shai_config.json + split runtime configs + assets/project_access_config.json + .shai/project_access.json] --> Agent
    Config --> Chat
    Config --> SharedModules
    History[.shai/history.json] --> ConvHist\n    Backups[.shai/shai_backups/] --> FileEditing
    Logs[logs/] --> Logging

    style Agent fill:#e1f5fe
    style Chat fill:#f3e5f5
    style Loop fill:#fff3e0
    style ToolsMod fill:#e8f5e9
    style LLM fill:#fce4ec
    style ProjectDir fill:#f1f8e9
```

## Architecture Overview (Text Summary)

The ASCII diagram above shows the high-level flow. Key points:

- **Two entry points**: `agent.py` (coding agent with tools) and `chat.py` (conversation only)
- **Shared modules** provide provider selection, conversation history, safeguards, logging, reasoning handlers, and context metering
- **Agent mode** adds mutating tools (editing, verification, Git, task management) with approval and bounded execution
- **Chat mode** provides conversation without local project or OS command tools
- **Configuration** (`assets/*.json`), history (`.shai/history.json`), and logs are persistent data

## Entry points

- **`agent.py`** manages the coding-agent loop, tool schemas, local tool execution, approvals, safeguards and verification.
- **`chat.py`** provides conversational mode without local file, Git or command-execution tools.
- **`modules/tools/`** owns shared read-only project-inspection tool implementations, schemas and dispatch. The Agent runtime imports these tools and separately registers editing, verification, task-management and `os_command` capabilities.

The project root is the first command-line argument to `agent.py`; if omitted, the current working directory is used.

## Agent tool groups

### Discovery and code intelligence

`list_files`, `search_files`, `get_file_outline`, `find_symbol`, `find_references`, `get_project_map` and `get_test_map` help the model navigate a project. Python structure inspection uses the standard-library `ast` module. Reference search and test mapping are heuristic/textual, not compiler-grade semantic analysis.

### Reading

`read_file` returns bounded line chunks. Its `offset` and `limit` parameters allow the model to continue reading without repeating earlier chunks.

### Editing

`patch_file` performs an exact unique text replacement. A zero-match or multi-match result is surfaced as a tool error and recommends `replace_lines`; it is not counted as a successful operation. `replace_lines` replaces a 1-based inclusive line range and is the fallback when exact text matching is brittle. Three consecutive identical failed tool calls stop the current request instead of letting the model retry indefinitely. `edit_file` handles whole-file replacement.

Edits are previewed, approved according to configuration, validated where supported, backed up and atomically written.

### Verification

`run_checks` runs a selected check. `verify_changes` combines Python compilation with an optional test suite. `inspect_test_failure` extracts likely failure details from supplied test output. Command execution is bounded by configured timeout and output limits.

### Task progress and continuation

When a model returns a text-only response while the task plan still has pending or in-progress items, the agent now sends a bounded continuation instruction (at most two times) rather than silently treating the narration as task completion. The model must complete or explicitly block the remaining plan items. Completion and stop reasons are recorded in the log. The configured iteration count is capped at 80 to prevent pathological long-running requests.

`update_task_plan` maintains a lightweight in-memory checklist for the current agent session. The model can mark steps pending, in progress, completed or blocked. The checklist is session-local and is not persisted after exit.

## Shared modules

| Module | Responsibility |
|---|---|
| `modules/provider_selection.py` | Provider definitions, API-key environment variable names, model discovery, selection and persistence. |
| `modules/conversation_history.py` | Agent/chat history, prompt history and input navigation. |
| `modules/agent_safeguards.py` | Argument validation, repeat signatures, no-progress detection, result bounding and history compaction. |
| `modules/logging_config.py` | Run-based logs, retention, console settings and API payload redaction. |
| `modules/file_change_display.py` | Bounded diff previews. |
| `modules/reasoning_handlers/` | Provider-specific extraction and display of supported reasoning summaries. |
| `modules/context_meter.py` | Prompt-token estimation, provider usage normalization and context-window percentage formatting. |
| `modules/tools/` | Shared read-only project tools, schemas, dispatch and project-root configuration. |

## Provider integration

Provider selection is shared by both modes. API requests use Python's standard-library HTTP facilities. OpenRouter, OpenAI, Gemini and Kilo use compatible chat/tool payloads where supported; Anthropic uses its Messages API format.

Reasoning handlers expose provider-supplied summaries when available. They do not expose hidden private chain-of-thought. The context meter estimates prompt size before a request and uses provider usage metadata for exact prompt/completion counts after a response. Percentages are shown only when the model context limit is known from provider metadata or configuration.

## Persistent data

- `assets/shai_config.json`: core runtime configuration and last-used provider/model; `assets/project_access_config.json`: generic project path access policy; `assets/provider_config.json`: provider overrides; `assets/command_config.json`: command policies.
- `.shai/history.json`: conversation history in the selected working project.
- `.shai/project_access.json`: user-approved path grants scoped to that project (ignored by Git).\n- `.shai/backup_config.json` and `.shai/shai_backups/`: project-specific backup policy and automatic file backups.
- `logs/`: diagnostic logs when file logging is enabled.
- `setup_keys.sh`: optional user-created shell file for API-key environment variables; keep it private and do not commit it.

## Tests

`test/` contains unit tests for the agent, chat, providers, safeguards, bounded execution, history, logging, reasoning handlers and tool functionality. `run_all_tests.sh` runs `unittest`, optionally runs `pytest`, and compiles source and test files.

## Pinned live status dashboard

During agent requests, Shai reserves a small terminal scroll region at the
bottom of interactive terminals. Conversation output scrolls in the body while
the status dashboard is redrawn in place. It shows the current phase, context
window usage, current/last tool and tool count, plus the latest task-plan items.
The dashboard is owned by `modules/live_status.py` and falls back to compact
status lines when stdout is not an interactive terminal or the terminal is too
short.

### Tool activity rendering

The live status module does not reserve terminal rows or freeze conversation
output. It collapses only each consecutive batch of tool calls into one
in-place-updated status line. Normal assistant text and warnings remain normal
scrolling chat output. Context-meter and task-plan values are maintained as
state without independently printing repetitive status messages.


## Modular package layout

The root scripts are now thin compatibility entry points. Existing commands remain
unchanged:

- `python agent.py [project_path]` loads `modules.engine.agent_runtime`.
- `python chat.py [project_path]` loads `modules.ui.chat_runtime`.

The compatibility entry points alias the runtime module namespace when imported,
so existing imports and test integrations that patch `agent` or `chat` continue
to operate on the live implementation.

### Engine and UI boundaries

- `modules/engine/agent_runtime.py` owns the coding-agent orchestration and tool
  lifecycle.
- `modules/engine/process_control.py` owns bounded subprocess execution,
  interruption monitoring, and Ctrl+D handling.
- `modules/ui/chat_runtime.py` owns the chat experience and provider response
  presentation.
- `modules/ui/spinner.py` owns the terminal spinner component.
- Existing shared modules under `modules/` continue to own provider selection,
  conversation history, context metering, safeguards, logging, reasoning
  extraction, terminal styling, and live status presentation.

### Command organization

`modules/commands/` separates the `shai_command` natural-language resolver, command aliases, canonical command descriptions, help text, and command normalization. The canonical command registry lives in `assets/command_registry.json`. The interactive agent loop consumes the
normalizer while preserving command arguments such as `/project <path>` and
`/exec <executable> <args...>` (with `/exec` retained as a legacy alias).

### Controlled OS command execution

`os_command` is registered only for Agent mode. It is implemented in `modules/tools/os_command.py` and uses `modules/engine/process_control.py`. It is not a shell: the current allowlist is limited to read-only Git inspection and approved Python verification modules. It validates project-relative paths, invokes argument arrays with `shell=False`, requires approval for Python test execution, and bounds time and output. `/exec` is only a legacy alias for `/exec`; the former `shell=True` path has been removed. See [OS command documentation](OS_COMMAND.md).

### Tool organization

The shared read-only project tools are divided by responsibility:

- `modules/tools/common.py`: project root and path-safety primitives.
- `modules/tools/filesystem.py`: listing, reading, and text search.
- `modules/tools/git.py`: Git status and diff inspection.
- `modules/tools/inspection.py`: AST outlines, symbol/reference lookup,
  project/test maps, and test-failure inspection.
- `modules/tools/schemas.py`: read-only tool schemas and dispatch registry.
- `modules/tools/__init__.py`: package exports for shared read-only tools.

### Configuration compatibility

The configuration now includes `shai_command` and `os_command` sections. Existing installations without these keys retain the default Shai command-routing behavior; `os_command` remains disabled unless explicitly enabled in configuration. The checked-in example enables the reviewed Git/Python profiles. See [OS command configuration](OS_COMMAND.md).


## Project path access control

All project-path decisions are centralized in `modules/tools/common.py`.
Filesystem tools and code-inspection tools require `view`; file mutation
operations require `edit`, with a separate `truncate` check for high-risk
content reductions. Controlled command execution checks `execute` for its
working directory and Python targets, and `view` for Git pathspecs.

Generic policy is stored in `assets/project_access_config.json`; user-approved project-specific grants are stored in `.shai/project_access.json`.
Path rules use project-relative globs, explicit deny always wins, and
canonicalization prevents traversal and symlink escapes. Access checks are
separate from interactive/configured approvals: approval cannot grant a
path permission. See `docs/CONFIGURATION.md`.
