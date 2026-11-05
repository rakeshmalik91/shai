# Shai Commands Reference

This document lists all available slash commands in Shai's two modes: **Agent** (coding agent with project tools) and **Chat** (conversation only).

---

## Agent Mode Commands

Available when running `python3 agent.py [project-path]`.

### Mode Switching

| Command | Aliases | Description |
|---------|---------|-------------|
| `/code` | — | Switch to **code mode** (agent with full tool access). Default mode. |
| `/ask` | — | Switch to **ask mode** (chat only, no tools). |
| `/read-only` | `/ro` | Switch to **read-only mode** (chat with read-only tools: list, read, search, Git status/diff, code navigation). |

### Provider & Model

| Command | Aliases | Description |
|---------|---------|-------------|
| `/model` | `/switch`, `/switch-model` | Switch provider and model. Prompts for selection; remembers last choice. |

### Project Inspection

| Command | Description |
|---------|-------------|
| `/status` | Show Git status (branch, staged/unstaged changes). |
| `/files` | List project files under the current root. |
| `/diff` | Show unstaged Git diff. |
| `/project <path>` | Switch to a different project folder. Resolves `~` and relative paths. Loads conversation history for the new project. |

### Conversation Control

| Command | Description |
|---------|-------------|
| `/help` | Show help with examples and command list. |
| `/reset` | Clear conversation history (keeps system prompt). |
| `/exit` | Exit Shai. |
| `Ctrl+D` | During an iteration: cancel current operation and return to prompt. |

### Task Management

| Command | Aliases | Description |
|---------|---------|-------------|
| `/todo` | `/task` | Show the current in-memory task plan with status indicators. |

### Git Operations

| Command | Options | Description |
|---------|---------|-------------|
| `/commit` | `--message-only` | Commit staged changes with an AI-generated message. Use `--message-only` to only generate the message without committing. |
| `/push` | — | Push commits to the configured remote. |
| `/commit-push` | — | Commit and push in a single command. |

> **Note:** Git operations require approval unless the session approval setting permits them.

### Shell Execution

| Command | Description |
|---------|-------------|
| `/exec <command>` | Execute a shell command in the project root directory. Runs with a 60-second timeout. Captures and displays stdout/stderr. Example: `/exec python -m pytest` |

---

## Chat Mode Commands

Available when running `python3 chat.py`.

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | — | Show help with examples and command list. |
| `/model` | `/switch`, `/switch-model` | Switch provider and model. |
| `/status` | — | Show current provider and model. |
| `/reset` | — | Clear conversation history. |
| `/exit` | — | Exit Shai. |
| `Ctrl+D` | — | Exit Shai (at prompt). |

> Chat mode does **not** have access to project tools, Git operations, or task management.

---

## Global Shortcuts

| Key | Context | Action |
|-----|---------|--------|
| `Ctrl+D` | At prompt | Exit (Chat) / Return to prompt (Agent, during iteration) |
| `Ctrl+C` | During iteration | Interrupt current operation |
| `a` / `all` | At approval prompt | Approve all future actions for the session |

---

## Configuration

Commands and behavior can be customized via `shai_config.json`:

- `max_tool_call_iterations` — Maximum tool calls per agent iteration (default: 80)
- `tool_safeguards` — Enable/disable safeguards (repeat detection, history compaction, etc.)
- `retry` — API retry settings (count, backoff, jitter, retryable statuses)
- `approval` — Session approval behavior (`ask`, `auto`, `never`)
- `tool_call_display` — Tool call display settings (`show_parameters`, `max_argument_chars`)

See `shai_config.json` for the full configuration schema.

---

## Quick Reference Card

```
Agent Mode:          Chat Mode:
─────────────────    ────────────
/help                /help
/code                /model
/ask                 /status
/read-only, /ro      /reset
/model, /switch      /exit
/status
/files
/diff
/reset
/todo, /task
/commit [--message-only]
/push
/commit-push
/exec <command>
/exit
```

---

## Related Documentation

- [Existing Tools](TOOLS.md) — Model-callable tools available in Agent mode
- [Architecture](ARCHITECTURE.md) — Project architecture overview
- [Tools Roadmap](roadmap/TOOLS_ROADMAP.md) — Planned tool additions