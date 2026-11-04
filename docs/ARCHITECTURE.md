# Project Architecture

## Overview

Shai is a Python command-line assistant with two entry points. Agent mode combines a language model with local project tools; chat mode provides conversation without local project access.

```text
                         User
                          |
                +---------+---------+
                |                   |
             agent.py             chat.py
          Coding-agent mode      Chat mode
                |
        Tool schemas and loop
                |
      +---------+---------------------+
      |         |          |          |
   Discovery  Reading   Editing   Verification
      |         |          |          |
      +---------+----------+----------+
                          |
                   Project directory
                          |
                Provider / model API
```

## Entry points

- **`agent.py`** manages the coding-agent loop, tool schemas, local tool execution, approvals, safeguards and verification.
- **`chat.py`** provides conversational mode without local file, Git or command-execution tools.
- **`modules/tools.py`** owns the shared read-only project-inspection tool implementations, schemas and dispatch used by chat mode and imported by agent mode. It receives the active project root through `configure_root()`; agent-only mutating and verification tools remain registered in `agent.py` because they depend on approval and bounded-execution policies.

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
| `modules/tools.py` | Shared read-only project tools, schemas, dispatch and project-root configuration. |

## Provider integration

Provider selection is shared by both modes. API requests use Python's standard-library HTTP facilities. OpenRouter, OpenAI, Gemini and Kilo use compatible chat/tool payloads where supported; Anthropic uses its Messages API format.

Reasoning handlers expose provider-supplied summaries when available. They do not expose hidden private chain-of-thought. The context meter estimates prompt size before a request and uses provider usage metadata for exact prompt/completion counts after a response. Percentages are shown only when the model context limit is known from provider metadata or configuration.

## Persistent data

- `shai_config.json`: runtime configuration and last-used provider/model.
- `.shai/history.json`: conversation history in the selected working project.
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
