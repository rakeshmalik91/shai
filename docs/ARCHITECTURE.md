# Project Architecture

## Overview

Shai is a small Python command-line application with two entry points that share provider selection, conversation storage, logging, and reasoning-summary support.

```text
                     User
                      |
             +--------+--------+
             |                 |
          agent.py           chat.py
       Coding-agent mode    Chat-only mode
             |                 |
      Local project tools      |
             |                 |
             +--------+--------+
                      |
                Shared modules
                      |
          Provider / Model selection
                      |
          Provider-specific HTTP API
                      |
             Remote language model
```

## Main entry points

### `agent.py`

The coding-agent loop. It sends the conversation and tool definitions to the selected model, displays available reasoning summaries and tool activity, executes local tools within the project root and applicable safeguards, appends tool results to the conversation, and continues until the model responds or a safeguard stops the request.

The project root is the directory passed as the first command-line argument; if omitted, the current working directory is used.

### `chat.py`

A conversational mode without local file, Git, or command-execution tools. It shares provider/model selection and persistent conversation handling with agent mode.

## Shared modules

| Module | Responsibility |
|---|---|
| `modules/provider_selection.py` | Provider endpoints, API-key environment variable names, model discovery, interactive selection, and persistence of the last provider/model. |
| `modules/conversation_history.py` | Separate agent/chat message histories, prompt history, readline navigation, and atomic history-file writes. |
| `modules/agent_safeguards.py` | Tool-argument validation helpers, repeat signatures, no-progress tracking, tool-result bounding, and conversation-history compaction. |
| `modules/logging_config.py` | Run-based file logging, log retention, optional console logging, and API payload redaction. |
| `modules/file_change_display.py` | Bounded file-change previews. |
| `modules/reasoning_handlers/` | Provider-specific extraction and display of supported reasoning summaries. |

## Agent tool flow

1. The user enters a request in agent mode.
2. Shai sends the conversation and declared tool schemas to the selected provider.
3. If the model requests a tool, Shai validates the tool name and arguments.
4. Shai displays the tool call and its parameters.
5. The local tool runs within the selected project root and applicable safeguards.
6. The result is bounded, logged, and added to the conversation.
7. Shai asks the model to continue with the updated conversation.

The currently exposed tools are documented in [TOOLS.md](TOOLS.md).

## Provider integration

Provider and model selection is shared by both modes. API requests are made using Python's standard-library HTTP facilities. The supported provider entries are OpenRouter, Gemini, Anthropic, OpenAI, Kilo Gateway, and Kilo Gateway's free-model filter.

Most providers use an OpenAI-compatible chat-completions interface. Anthropic uses its Messages API format. `modules/reasoning_handlers/` normalizes supported provider-specific reasoning-summary fields for display.

## Persistent files

| Path | Purpose |
|---|---|
| `shai_config.json` | User-adjustable provider/model and runtime settings. |
| `setup_keys.sh` | User-created shell file containing API-key environment variables; do not commit it. |
| `.shai/history.json` | Separate saved conversations for agent and chat, plus prompt history. |
| `logs/` | Diagnostic log files when file logging is enabled. |

Conversation history is project-local. Agent mode stores it under the selected project root; chat mode stores it under the current working directory.

## Tests

`test/` contains unit tests for the agent, chat, provider selection, safeguards, bounded execution, conversation history, logging, file-change display, and reasoning handlers.

`run_all_tests.sh` runs `unittest`, runs `pytest` when installed, and compiles the source and test modules.
