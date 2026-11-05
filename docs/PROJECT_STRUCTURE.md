# Project Structure

This document provides a complete overview of the Shai project structure, including all modules, entry points, tests, and documentation.

## Root Directory

```
.
├── .gitignore
├── .lectra-project.json
├── .shai/                    # Runtime data (history, task state)
│   ├── .gitignore
│   ├── history.json
│   └── task.json
├── .shai_backups/            # Automatic backups of edited files
├── LICENSE
├── README.md
├── __init__.py
├── agent.py                  # Entry point: Coding agent mode
├── chat.py                   # Entry point: Chat mode
├── docs/                     # Documentation
├── logs/                     # Diagnostic logs
├── modules/                  # Core implementation modules
├── pytest.ini
├── requirements.txt
├── run_all_tests.sh
├── setup_keys.sample.sh
├── shai_config.json          # Runtime configuration
└── test/                     # Unit tests
```

## Entry Points

| File | Description |
|------|-------------|
| `agent.py` | Coding agent with full tool access (editing, Git, verification, task management) |
| `chat.py` | Conversational mode with read-only project inspection tools |

## Core Modules (`modules/`)

### Shared Modules (used by both agent and chat)

| Module | Responsibility |
|--------|----------------|
| `agent_safeguards.py` | Argument validation, repeat detection, result bounding, history compaction |
| `context_meter.py` | Prompt-token estimation, provider usage normalization, context-window percentages |
| `conversation_history.py` | Agent/chat history, prompt history, input navigation |
| `file_change_display.py` | Bounded diff previews |
| `logging_config.py` | Run-based logs, retention, console settings, API payload redaction |
| `provider_selection.py` | Provider definitions, API keys, model discovery, selection, persistence |
| `reasoning_handlers/` | Provider-specific reasoning summary extraction |
| `terminal_style.py` | Terminal styling utilities |
| `tools/` | Shared read-only project inspection tools |

### Engine Modules (`modules/engine/`) - Agent Mode Implementation

| Module | Responsibility |
|--------|----------------|
| `agent_runtime.py` | Compatibility/runtime composition surface, shared namespace |
| `configuration.py` | Config loading, safeguards, approvals, limits |
| `file_editing.py` | Approval, validation, atomic edits, check execution |
| `git_operations.py` | Git operations, commit-message generation |
| `history_recovery.py` | Tool-history repair, recovery notes |
| `model_orchestration.py` | Model request loop, continuation orchestration |
| `process_control.py` | Process control utilities |
| `project_inspection.py` | Filesystem navigation, source inspection, verification |
| `provider_api.py` | Provider request construction, retries, history sanitation |
| `task_plan.py` | Task-plan persistence and continuation |
| `tool_execution.py` | Tool normalization, dispatch, safeguards, execution |
| `tool_presentation.py` | Tool and assistant output formatting |

### Commands (`modules/commands/`)

| Module | Responsibility |
|--------|----------------|
| `aliases.py` | Command aliases |
| `definitions.py` | Command definitions |
| `help.py` | Help system |
| `registry.py` | Command registry |

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

### Tools (`modules/tools/`) - Shared Read-Only Tools

| Module | Responsibility |
|--------|----------------|
| `common.py` | Shared tool utilities |
| `filesystem.py` | Filesystem operations |
| `git.py` | Git read operations |
| `inspection.py` | Code inspection (AST-based) |
| `schemas.py` | Tool schemas |

### UI (`modules/ui/`)

| Module | Responsibility |
|--------|----------------|
| `chat_runtime.py` | Chat mode runtime |
| `spinner.py` | Loading spinner |

## Documentation (`docs/`)

| File | Description |
|------|-------------|
| `ARCHITECTURE.md` | Detailed architecture overview with diagrams |
| `COMMANDS.md` | Command reference |
| `MODULAR_ARCHITECTURE.md` | Legacy modular architecture reference |
| `PROVIDERS.md` | Provider integration details |
| `TERMINAL_OUTPUT.md` | Terminal output handling |
| `TODO.md` | Task tracking |
| `TOOLS.md` | Tool reference |
| `bugfixes/` | Bug fix documentation |
| `roadmap/` | Future roadmap documents |

## Tests (`test/`)

| File | Description |
|------|-------------|
| `test_agent.py` | Agent mode tests |
| `test_agent_safeguards.py` | Safeguards tests |
| `test_bounded_execution.py` | Bounded execution tests |
| `test_chat.py` | Chat mode tests |
| `test_configuration.py` | Configuration tests |
| `test_context_meter.py` | Context meter tests |
| `test_conversation_history.py` | Conversation history tests |
| `test_engine_module_boundaries.py` | Engine module boundary tests |
| `test_file_change_display.py` | File change display tests |
| `test_invalid_tool_recovery.py` | Invalid tool recovery tests |
| `test_live_status.py` | Live status tests |
| `test_logging_config.py` | Logging configuration tests |
| `test_modular_architecture.py` | Modular architecture tests |
| `test_priority_tools.py` | Priority tools tests |
| `test_provider_selection.py` | Provider selection tests |
| `test_reasoning_handlers.py` | Reasoning handlers tests |
| `test_tools_module.py` | Tools module tests |

## Configuration & Data Files

| File | Description |
|------|-------------|
| `shai_config.json` | Runtime configuration (provider, model, approvals, limits) |
| `.shai/history.json` | Conversation history per project |
| `.shai/task.json` | Task plan state |
| `logs/` | Diagnostic logs (when enabled) |
| `setup_keys.sh` | User-created shell file for API keys (not committed) |

## Module Dependency Overview

```
agent.py
├── modules/engine/agent_runtime.py (composition surface)
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
├── modules/conversation_history.py
├── modules/agent_safeguards.py
├── modules/logging_config.py
├── modules/file_change_display.py
├── modules/reasoning_handlers/
├── modules/context_meter.py
└── modules/live_status.py

chat.py
├── modules/ui/chat_runtime.py
├── modules/tools/ (shared read-only tools)
├── modules/provider_selection.py
├── modules/conversation_history.py
├── modules/agent_safeguards.py
├── modules/logging_config.py
├── modules/reasoning_handlers/
└── modules/context_meter.py
```

## Key Design Principles

1. **Two stable entry points**: `agent.py` and `chat.py` remain unchanged as launch/import surfaces
2. **Engine decomposition**: Implementation moved from monolithic `agent_runtime.py` into focused domain modules
3. **Shared namespace compatibility**: Extracted modules install functions into `agent_runtime`'s shared namespace for backward compatibility
4. **Tool separation**: Read-only tools in `modules/tools/` shared by both modes; mutating tools remain in agent mode
5. **Provider abstraction**: `provider_selection.py` and `reasoning_handlers/` isolate provider-specific logic
6. **Test coverage**: Dedicated test for engine module boundaries (`test_engine_module_boundaries.py`)