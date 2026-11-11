# Project Architecture

## Overview

Shai is a Python command-line and programmatic assistant with two primary operating modes:
- **Agent mode**: Combines a language model with local project inspection, editing, and execution tools.
- **Chat mode**: Provides conversation and read-only inspection without project mutation capabilities.

```mermaid
flowchart TD
    User[User] -->|Input| AgentCLI[modules/ui/agent_cli.py\nCoding Agent CLI]
    User -->|Input| ChatCLI[modules/ui/chat_runtime.py\nChat Mode CLI]
    FutureGUI[Future GUI / Web UI] -.->|InteractionHandler| EngineFacade

    AgentCLI --> EngineFacade[agent.py / modules/engine/agent_runtime.py\nEngine Facade & Composition Surface]

    subgraph EngineMode["Core Engine (modules/engine/)"]
        EngineFacade --> Loop[model_orchestration.py\nAgent Request Loop]
        EngineFacade --> Interaction[interaction.py\nPluggable UI Delegates]
        Loop --> Tools[tool_execution.py\nTool Registry & Dispatch]
        Tools --> Discovery[project_inspection.py\nAST & Project Intelligence]
        Tools --> Editing[file_editing.py\nAtomic Editing & Validation]
        Tools --> TaskMgmt[task_plan.py\nSession Task Plan]
        Tools --> GitOps[git_operations.py\nGit Operations]
        Tools --> OSExec[process_control.py\nBounded Subprocess Runner]
    end

    subgraph ChatMode["Chat UI (modules/ui/chat_runtime.py)"]
        ChatCLI --> ChatLoop[Conversation Loop]
    end

    subgraph SharedModules["Shared Modules"]
        ProviderSel[modules/provider_selection.py\nShared config, auth, selection & persistence]\n    ProviderHandlers[modules/providers/\nProvider-specific discovery & metadata]
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
    Editing --> ProjectDir
    GitOps --> ProjectDir

    ProviderSel --> ProviderHandlers\n    ProviderHandlers -->|Normalized model data| ProviderSel\n    ProviderSel -->|API Calls| LLM[(LLM Provider APIs)]
    Loop --> LLM
    ChatLoop --> LLM

    Config[assets/*.json] --> EngineFacade
    Config --> ChatCLI
    Config --> SharedModules
    History[.shai/history.json] --> ConvHist
    Backups[.shai/shai_backups/] --> Editing
    Logs[.shai/logs/] --> Logging
```

## Architecture Summary

- **Separated UI & Engine layers**: Terminal I/O, REPL loops, and ANSI rendering are isolated in `modules/ui/` (`agent_cli.py`, `chat_runtime.py`, `spinner.py`). The core execution logic lives in `modules/engine/`, enabling future graphical interfaces (GUIs, web frontends) to embed the engine via `modules.engine.interaction.InteractionHandler`.
- **Two entry points**: `agent.py` (coding agent CLI) and `chat.py` (conversational chat CLI).
- **Shared modules** provide provider selection, conversation history, safeguards, logging, reasoning handlers, and context metering.
- **Agent mode** adds mutating tools (editing, verification, Git, task management) with approval and bounded execution.
- **Chat mode** provides conversation and read-only inspection without local project mutation or OS command execution.
- **Configuration** (`assets/*.json`), history (`.shai/history.json`), and project-local logs are persistent data.

## Entry Points

- **`agent.py`** launches `modules.ui.agent_cli.main()` when run directly; when imported, it exposes `modules.engine.agent_runtime` for compatibility.
- **`chat.py`** launches `modules.ui.chat_runtime.main()` for conversational mode.
- **`modules/tools/`** owns shared read-only project-inspection tool implementations, schemas and dispatch. The Agent runtime imports these tools and separately registers editing, verification, task-management and `os_command` capabilities.

The project root is the first command-line argument; if omitted, the current working directory is used.

## Agent Tool Groups

### Discovery and code intelligence

`list_files`, `search_files`, `get_file_outline`, `find_symbol`, `find_references`, `get_project_map` and `get_test_map` help the model navigate a project. Python structure inspection uses the standard-library `ast` module. Reference search and test mapping are heuristic/textual, not compiler-grade semantic analysis.

### Reading

`read_file` returns bounded line chunks. Its `offset` and `limit` parameters allow the model to continue reading without repeating earlier chunks.

### Editing

`patch_file` performs an exact unique text replacement. A zero-match or multi-match result is surfaced as a tool error and recommends `replace_lines`; it is not counted as a successful operation. `replace_lines` replaces a 1-based inclusive line range and is the fallback when exact text matching is brittle. Three consecutive identical failed tool calls stop the current request instead of letting the model retry indefinitely. `edit_file` handles whole-file replacement.

Edits are previewed, approved according to configuration or interactive delegates, validated where supported, backed up and atomically written.

### Verification

`run_checks` runs a selected check. `verify_changes` combines Python compilation with an optional test suite. `inspect_test_failure` extracts likely failure details from supplied test output. Command execution is bounded by configured timeout and output limits.

### Task progress and continuation

When a model returns a text-only response while the task plan still has pending or in-progress items, the agent sends a bounded continuation instruction (at most two times) rather than silently treating the narration as task completion. The model must complete or explicitly block the remaining plan items. Completion and stop reasons are recorded in the log. The configured iteration count is capped to prevent pathological long-running requests.

`update_task_plan` maintains a lightweight in-memory checklist for the current agent session. The model can mark steps pending, in progress, completed or blocked.

## Shared Modules

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

## Provider Integration

Provider selection is shared by both modes. API requests use Python's standard-library HTTP facilities. OpenRouter, OpenAI, Gemini and Kilo use compatible chat/tool payloads where supported; Anthropic uses its Messages API format.

Reasoning handlers expose provider-supplied summaries when available. They do not expose hidden private chain-of-thought. The context meter estimates prompt size before a request and uses provider usage metadata for exact prompt/completion counts after a response. Percentages are shown only when the model context limit is known from provider metadata or configuration.

## Persistent Data

- `assets/shai_config.json`: core runtime configuration and last-used provider/model; `assets/project_access_config.json`: generic project path access policy; `assets/provider_config.json`: provider overrides; `assets/command_config.json`: command policies.
- `.shai/history.json`: conversation history in the selected working project.
- `.shai/project_access.json`: user-approved path grants scoped to that project (ignored by Git).
- `.shai/backup_config.json` and `.shai/shai_backups/`: project-specific backup policy and automatic file backups.
- `.shai/logs/`: diagnostic logs when file logging is enabled.
- `setup_keys.sh`: optional user-created shell file for API-key environment variables; keep it private and do not commit it.

## Tests

`test/` contains comprehensive unit tests for the agent, chat, providers, safeguards, bounded execution, history, logging, reasoning handlers, interactions, and tool functionality. `run_all_tests.sh` runs `unittest`, optionally runs `pytest`, and compiles source and test files.

## Live Tool Activity Rendering

During agent requests on interactive terminals (TTY), Shai updates tool execution status on a single transient terminal line using carriage return (`\r`) and ANSI line-erasing escape sequences, as documented in [`TERMINAL_OUTPUT.md`](TERMINAL_OUTPUT.md).

Conversation output scrolls normally in the terminal body and is never frozen or locked into a split window. Only each consecutive batch of tool calls is collapsed into one in-place-updated status line. Context-meter and task-plan values are maintained internally as state without printing repetitive spam to the terminal. When stdout is not an interactive terminal, in-place updates are suppressed, keeping batch output clean.

## Modular Package Layout

The root scripts are thin compatibility entry points:
- `python agent.py [project_path]` launches `modules.ui.agent_cli.main()` (or aliases `modules.engine.agent_runtime` on import).
- `python chat.py [project_path]` launches `modules.ui.chat_runtime.main()`.

### Engine and UI Boundaries

Shai strictly decouples user interface layers from the core agent execution engine to make future graphical user interfaces (GUI) or web interfaces straightforward to introduce:

- `modules/engine/` contains the headless agent execution engine (request orchestration, AST inspection, atomic file editing, bounded process execution, and task planning).
- `modules/engine/interaction.py` defines the pluggable `InteractionHandler` interface. Any UI (CLI or GUI) can register delegates for action confirmations, high-risk approvals, and choice dialogs without terminal `input()` dependencies.
- `modules/ui/agent_cli.py` owns the interactive coding-agent terminal REPL, command routing, and ANSI presentation for CLI mode.
- `modules/ui/chat_runtime.py` owns the chat CLI experience and response presentation.
- `modules/ui/spinner.py` owns the non-blocking terminal spinner component.

### Command Organization

`modules/commands/` separates the `shai_command` natural-language resolver, command aliases, canonical command descriptions, help text, and command normalization. The canonical command registry lives in `assets/command_registry.json`.

### Controlled OS Command Execution

`os_command` is registered only for Agent mode. It is implemented in `modules/tools/os_command.py` and uses `modules/engine/process_control.py`. It is not a shell: the allowlist is limited to read-only Git inspection and approved Python verification modules. It validates project-relative paths, invokes argument arrays with `shell=False`, requires approval for Python test execution, and bounds time and output. See [OS command documentation](OS_COMMAND.md).

### Tool Organization

The shared read-only project tools are divided by responsibility:
- `modules/tools/common.py`: project root and path-safety primitives.
- `modules/tools/filesystem.py`: listing, reading, and text search.
- `modules/tools/git.py`: Git status and diff inspection.
- `modules/tools/inspection.py`: AST outlines, symbol/reference lookup, project/test maps, and test-failure inspection.
- `modules/tools/schemas.py`: read-only tool schemas and dispatch registry.
- `modules/tools/__init__.py`: package exports for shared read-only tools.

## Project Path Access Control

All project-path decisions are centralized in `modules/tools/common.py`. Filesystem tools and code-inspection tools require `view`; file mutation operations require `edit`, with a separate `truncate` check for high-risk content reductions. Controlled command execution checks `execute` for its working directory and Python targets, and `view` for Git pathspecs.

Generic policy is stored in `assets/project_access_config.json`; user-approved project-specific grants are stored in `.shai/project_access.json`. Path rules use project-relative globs, explicit deny always wins, and canonicalization prevents traversal and symlink escapes. Access checks are separate from interactive/configured approvals: approval cannot grant a path permission. See `docs/CONFIGURATION.md`.
