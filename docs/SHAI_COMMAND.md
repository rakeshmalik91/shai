# `shai_command` — Natural-Language Slash-Command Routing

**Status: Implemented**

## Purpose

`shai_command` lets a user invoke an existing Shai slash command using an exact,
curated natural-language phrase. It selects Shai's own commands; it does not
execute operating-system processes.

Examples:

- “Show my Git status” → `/status`
- “Switch to read-only mode” → `/read-only`
- “Show my task list” → `/todo`
- “Generate a commit message” → `/commit --message-only`

## Source of truth

`assets/command_registry.json` is the canonical registry. Each entry defines:

- stable command `id`;
- canonical `slash_command`;
- description and supported `modes`;
- slash-command `aliases`;
- exact natural-language `triggers`;
- risk metadata and optional enabled state.

Slash aliases, command descriptions, and natural-language phrases are read from
this JSON file. Do not duplicate the registry in Python or prompt text.

## Runtime

- `modules/commands/shai_command.py` loads and validates the registry and
  resolves exact phrases.
- `modules/commands/registry.py` normalizes slash commands and their arguments.
- `modules/commands/aliases.py` and `definitions.py` expose registry-derived
  compatibility views.
- `modules/engine/agent_runtime.py` applies routing in Agent mode.
- `modules/ui/chat_runtime.py` applies routing in Chat mode.

The resolver is deterministic. It does not make an LLM call, invent command
arguments, or fuzzy-match arbitrary text. It rejects negated/meta requests,
ambiguous phrases, disabled commands, and commands unavailable in the current
mode. A request that requires a parameter is not routed unless the exact
registered phrase maps to a complete supported invocation.

## Configuration

```json
{
  "shai_command": {
    "enabled": true
  }
}
```

`enabled` defaults to `true` for existing installations that do not yet have
the setting. Set it to `false` to disable natural-language routing while
keeping explicit slash commands available.

## Safety boundary

`shai_command` only converts a phrase into a registered slash command. It does
not grant approval or bypass the command's normal handler, mode restrictions,
or confirmation flow. In particular, it cannot convert a request into an
arbitrary shell command. OS process execution is handled separately by
[`OS_COMMAND.md`](OS_COMMAND.md).

## Adding a command phrase

1. Confirm the slash command already exists and document its actual behavior.
2. Add a unique stable entry or add a phrase to the existing entry in
   `assets/command_registry.json`.
3. Include only modes where the command is genuinely available.
4. Do not add broad phrases such as “run it” or “reset that” when they require
   guessing a target or parameter.
5. Add positive, negative, ambiguous, and mode-restriction tests.

## Tests

`test/test_shai_command.py` verifies registry loading, exact phrase
matching, mode restrictions, negative/meta requests, slash normalization, and
the `/exec` compatibility alias for `/os-command`.
