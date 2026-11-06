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
| `/read-only` | `/ro` | Switch to **read-only mode** (Agent mode with read-only project tools: list, read, search, Git status/diff, and code navigation). |

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

### Controlled OS Command Execution

| Command | Alias | Description |
|---------|-------|-------------|
| `/exec <executable> <args...>` | `/os-command` (legacy) | Run a command through the `os_command` allowlist. This is not a shell. |

Examples:

```text
/exec git status --short --branch
/exec git diff --stat
/exec python3 -m pytest -q
```

Only approved Git inspection subcommands and Python verification modules are
supported. Python test execution requires approval by default. Pipes,
redirections, shell expansion, arbitrary scripts, network commands, privileged
commands, and Git mutations are rejected. See [os_command](OS_COMMAND.md).

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

> Chat mode does **not** have access to project tools, Git operations, `os_command`, or task management.

---

## Global Shortcuts

| Key | Context | Action |
|-----|---------|--------|
| `Ctrl+D` | At prompt | Exit (Chat) / Return to prompt (Agent, during iteration) |
| `Ctrl+C` | During iteration | Interrupt current operation |
| `a` / `all` | At approval prompt | Approve all future actions for the session |

---

## Configuration

Commands and behavior are customized across split configuration files under `assets/`:

- `assets/shai_config.json` — Core settings: `max_tool_call_iterations` (iteration ceiling), `tool_safeguards` (repeat call detection, timeout bounds), `retry` (HTTP retry and recovery policy), and `approve` (granular tool/category approval permissions).
- `assets/command_config.json` — Execution policies: `shai_command` (enable/disable natural language routing) and `os_command` (allowlisted command profiles, subcommands, and bounds).
- `assets/command_registry.json` — Canonical slash command metadata, aliases, and exact trigger phrases.
- `assets/cli_config.json` — Terminal interface settings: `tool_call_display` (`show_parameters`, `max_argument_chars`), file change diff previews, and reasoning display.

See [`CONFIGURATION.md`](CONFIGURATION.md) for full details on the configuration layout and precedence rules.

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
/exec <executable> <args...>, /os-command 
/exit
```

---

## Related Documentation

- [Existing Tools](TOOLS.md) — Model-callable tools available in Agent mode
- [Architecture](ARCHITECTURE.md) — Project architecture overview
- [shai_command](SHAI_COMMAND.md) — Natural-language slash-command routing
- [os_command](OS_COMMAND.md) — Controlled OS command execution
- [Tools Roadmap](plans/TOOLS.md) — Planned tool additions