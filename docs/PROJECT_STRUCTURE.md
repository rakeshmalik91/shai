# Project Structure

This document provides a complete overview of the Shai project structure, including all modules, entry points, tests, configuration, and documentation.

## Shai Installation Directory

The following tree describes the Shai source/install directory. It contains shared code, documentation and `assets/` policy. Runtime project state is **not** read from or written to this directory's `.shai/` merely because Shai is installed here.

```text
.
├── .gitignore
├── LICENSE
├── README.md
├── __init__.py
├── agent.py
├── chat.py
├── assets/
│   ├── shai_config.json
│   ├── approval_config.json
│   ├── project_access_config.json
│   ├── provider_config.json
│   ├── model_routing.json
│   ├── command_config.json
│   ├── cli_config.json
│   ├── logging_config.json
│   ├── command_registry.json
│   └── tools_registry.json
├── docs/
│   ├── bugfixes/
│   └── plans/
├── modules/
│   ├── commands/
│   ├── engine/
│   ├── providers/
│   ├── reasoning_handlers/
│   ├── tools/
│   ├── ui/
│   └── project_paths.py
├── pytest.ini
├── requirements.txt
├── run_all_tests.sh
├── setup_keys.sample.sh
└── test/
```

## Selected Project State

For a selected working project at `/path/to/project`, Shai creates and uses this separate state directory:

```text
/path/to/project/
└── .shai/
    ├── .gitignore
    ├── model_config.json
    ├── approval_config.json
    ├── os_command_approvals.json
    ├── project_access.json
    ├── model_routing.json
    ├── backup_config.json
    ├── history.json
    ├── task.json
    ├── logs/
    └── shai_backups/
```

The `modules/project_paths.py` resolver is the source of truth for this location. The CLI initializes it from the selected project and updates it whenever `/project <path>` changes the working project. See [Configuration](CONFIGURATION.md).

## Entry Points

| File | Description |
|------|-------------|
| `agent.py` | CLI entry point for coding agent mode (launches `modules.ui.agent_cli`) |
| `chat.py` | CLI entry point for conversational chat mode (launches `modules.ui.chat_runtime`) |

## Core Modules (`modules/`)

### Shared Modules (used by both agent and chat)

| Module | Responsibility |
|--------|----------------|
| `agent_safeguards.py` | Argument validation, repeat detection, result bounding, history compaction |
| `context_meter.py` | Prompt-token estimation, provider usage normalization, context-window percentages |
| `conversation_history.py` | Agent/chat history, prompt history, input navigation |
| `file_change_display.py` | Bounded diff previews |
| `logging_config.py` | Run-based logs, retention, console settings, API payload redaction |
| `provider_selection.py` | Shared provider configuration, API transport/authentication, interactive selection and persistence |
| `providers/` | Provider-specific model discovery and capability metadata handlers, dispatched through a registry |
| `model_routing.py` | Project-specific task-role routing, priority resolution and availability cache |
| `reasoning_handlers/` | Provider-specific reasoning summary extraction |
| `terminal_style.py` | Terminal styling utilities |
| `tools/` | Shared read-only project inspection tools used by Agent mode |

### Engine Modules (`modules/engine/`) — Core Agent Execution Engine

| Module | Responsibility |
|--------|----------------|
| `agent_runtime.py` | Compatibility/runtime composition surface, shared namespace and engine facade |
| `interaction.py` | Pluggable UI/GUI interaction delegate interface (`InteractionHandler`, confirmations, choices) |
| `configuration.py` | Config loading, safeguards, approvals, limits |
| `file_editing.py` | Approval, validation, atomic edits, check execution |
| `git_operations.py` | Git operations, commit-message generation |
| `history_recovery.py` | Tool-history repair, recovery notes |
| `model_orchestration.py` | Model request loop, continuation orchestration |
| `process_control.py` | Bounded subprocess execution, output capture, timeout and interruption |
| `project_inspection.py` | Filesystem navigation, source inspection, verification |
| `provider_api.py` | Provider request construction, retries, history sanitation |
| `task_plan.py` | Task-plan persistence and continuation |
| `tool_execution.py` | Tool normalization, dispatch, safeguards, execution |
| `tool_presentation.py` | Tool and assistant output formatting |

### User Interface (`modules/ui/`) — Presentation & Frontend Adapters

The UI layer is completely decoupled from the engine. Alternative frontends (e.g. GUI, Web, Desktop) can drive the engine without terminal dependencies:

| Module | Responsibility |
|--------|----------------|
| `agent_cli.py` | Interactive terminal REPL, slash-command dispatch, and ANSI rendering for agent mode |
| `chat_runtime.py` | Interactive terminal REPL and presentation for chat mode |
| `spinner.py` | Non-blocking terminal activity spinner |

### Commands (`modules/commands/`)

| Module | Responsibility |
|--------|----------------|
| `shai_command.py` | Deterministic natural-language routing for Shai slash commands |
| `aliases.py` | Command aliases loaded dynamically from `assets/command_registry.json` |
| `definitions.py` | Command definitions and help text loaded from `assets/command_registry.json` |
| `help.py` | Formatted help text system |
| `registry.py` | Slash-command normalization and registry facade |

### Reasoning Handlers (`modules/reasoning_handlers/`)

| Module | Provider |
|--------|----------|
| `anthropic.py` | Anthropic (Claude) |
| `common.py` | Shared utilities |
| `gemini.py` | Google Gemini |
| `kilo.py` | Kilo |
| `openai.py` | OpenAI |
| `openai_compatible.py` | OpenAI-compatible APIs |
| `openrouter.py` | OpenRouter |

### Tools (`modules/tools/`) — Shared Tools and Controlled OS Execution

| Module | Responsibility |
|--------|----------------|
| `common.py` | Centralized path access policy, glob matching, and project root management |
| `filesystem.py` | Read-only filesystem operations (`list_files`, `read_file`, `search_files`) |
| `git.py` | Read-only Git inspection (`git_status`, `git_diff`) |
| `inspection.py` | AST code inspection (`get_file_outline`, `find_symbol`, `find_references`, `get_project_map`, `get_test_map`, `inspect_test_failure`) |
| `os_command.py` | Agent-only allowlisted OS command execution; no shell |
| `schemas.py` | Canonical tool dispatch tables and function definitions |

## Documentation (`docs/`)

| File | Description |
|------|-------------|
| `ARCHITECTURE.md` | Detailed architecture overview with diagrams |
| `COMMANDS.md` | Slash command reference and global shortcuts |
| `CONFIGURATION.md` | Split configuration layout and policy specification |
| `MODEL_ROUTING.md` | Project-scoped task-role routing and candidate resolution |
| `OS_COMMAND.md` | Controlled OS command execution and security profile |
| `PROJECT_PATH_ACCESS_CONTROL.md` | Path permission rules, glob matching, and interactive grants |
| `PROJECT_STRUCTURE.md` | Repository structure and module dependency overview |
| `PROVIDERS.md` | Provider integration details, authentication, and endpoint overrides |
| `SHAI_COMMAND.md` | Deterministic natural-language routing to slash commands |
| `TERMINAL_OUTPUT.md` | ANSI color conventions and transient tool activity line |
| `TESTING.md` | Automated test runner workflow and durable report formats |
| `TOOLS.md` | Existing tool reference and parameter contracts |
| `bugfixes/` | Resolved bug reports and architectural fixes |
| `plans/` | Future architectural, tool, sandbox, and testing roadmaps |

## Tests (`test/`)

| File | Description |
|------|-------------|
| `test_agent.py` | Agent mode tests |
| `test_agent_safeguards.py` | Safeguards tests |
| `test_backup_management.py` | Backup management and retention tests |
| `test_bounded_execution.py` | Bounded execution tests |
| `test_chat.py` | Chat mode tests |
| `test_commit_message_routing.py` | Commit message routing tests |
| `test_configuration.py` | Configuration tests |
| `test_context_meter.py` | Context meter tests |
| `test_conversation_history.py` | Conversation history tests |
| `test_engine_module_boundaries.py` | Engine module boundary tests |
| `test_file_change_display.py` | File change display tests |
| `test_interaction.py` | Pluggable UI interaction and confirmation tests |
| `test_invalid_tool_recovery.py` | Invalid tool recovery tests |
| `test_live_status.py` | Live status tests |
| `test_logging_config.py` | Logging configuration tests |
| `test_model_routing.py` | Model routing tests |
| `test_modular_architecture.py` | Modular architecture tests |
| `test_os_command.py` | OS command allowlist and bounds tests |
| `test_priority_tools.py` | Priority code intelligence and line replacement tests |
| `test_project_access_control.py` | Path access control and interactive grant tests |
| `test_provider_selection.py` | Provider selection tests |
| `test_reasoning_handlers.py` | Reasoning handlers tests |
| `test_shai_command.py` | Natural-language command routing tests |
| `test_tool_registry.py` | Tool registry validation tests |
| `test_tools_module.py` | Shared read-only tools and filesystem tests |

## Module Dependency Overview

```
agent.py
└── modules/ui/agent_cli.py (Interactive Terminal REPL)
    ├── modules/engine/agent_runtime.py (Engine Facade & Composition Surface)
    │   ├── modules/engine/interaction.py (Pluggable UI/GUI delegates)
    │   ├── modules/engine/configuration.py
    │   ├── modules/engine/project_inspection.py
    │   ├── modules/engine/task_plan.py
    │   ├── modules/engine/git_operations.py
    │   ├── modules/engine/file_editing.py
    │   ├── modules/engine/provider_api.py
    │   ├── modules/engine/tool_execution.py
    │   ├── modules/engine/tool_presentation.py
    │   ├── modules/engine/history_recovery.py
    │   └── modules/engine/model_orchestration.py
    ├── modules/commands/registry.py
    ├── modules/tools/ (shared read-only tools)
    ├── modules/provider_selection.py
    ├── modules/model_routing.py
    ├── modules/conversation_history.py
    ├── modules/agent_safeguards.py
    ├── modules/logging_config.py
    ├── modules/file_change_display.py
    ├── modules/reasoning_handlers/
    ├── modules/context_meter.py
    └── modules/live_status.py

[Future GUI Entrypoint, e.g. gui.py]
└── modules/ui/gui_runtime.py (GUI Frontend)
    └── modules/engine/agent_runtime.py (Same engine, driven via InteractionHandler callbacks)

chat.py
└── modules/ui/chat_runtime.py
    ├── modules/tools/ (shared read-only tools)
    ├── modules/provider_selection.py
    ├── modules/model_routing.py
    ├── modules/conversation_history.py
    ├── modules/agent_safeguards.py
    ├── modules/logging_config.py
    ├── modules/reasoning_handlers/
    └── modules/context_meter.py
```

## Key Design Principles

1. **Two stable entry points**: `agent.py` and `chat.py` launch respective UI runtimes while preserving backward-compatible import namespaces.
2. **Strict CLI vs Engine Separation**: Terminal I/O, prompts, colorization, and REPL controls are isolated in `modules/ui/`, allowing clean GUI integration without terminal entanglements.
3. **Pluggable Interaction Layer**: `modules/engine/interaction.py` enables frontends to provide custom confirmation dialogs, choice pickers, and status listeners.
4. **Engine decomposition**: Engine logic is partitioned into cohesive domain modules under `modules/engine/`.
5. **Tool separation**: Read-only inspection tools in `modules/tools/` are shared by both agent and chat modes; mutating tools remain restricted to agent mode.
6. **Centralized access control**: `modules/tools/common.py` enforces canonical path validation and policy evaluation before any filesystem, AST, or OS operation.
