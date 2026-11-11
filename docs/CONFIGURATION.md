# Configuration layout

Shai configuration is stored under `assets/` and split by responsibility:

- `assets/tools_registry.json` stores model-facing tool definitions and chat availability.

`assets/shai_config.json` — core agent, runtime, retry, approval, and safeguard settings.
- `assets/project_access_config.json` — generic project path access defaults and administrator-authored path rules.
- `assets/provider_config.json` — provider endpoint/authentication overrides under `providers`.
- `assets/command_config.json` — Shai slash-command and OS command settings (`shai_command`, `os_command`).
- `assets/model_config.json` — per-model technical limits and capabilities, including context windows, output limits, supported input/output content types, tool calling, structured output, and reasoning. Model-specific `overrides` take precedence over model metadata, which takes precedence over global `defaults`.


### Model metadata configuration

`assets/model_config.json` is the shared model metadata registry. Model keys use
`provider::model` so identifiers remain unambiguous across providers. The file
is divided into three layers:

- `defaults`: fallback values for attributes not advertised or configured.
- `models`: provider-discovered or curated metadata for each model.
- `overrides`: explicit user values, applied last and therefore highest priority.

Example:

```json
{
  "version": 1,
  "defaults": {
    "context_window": null,
    "max_output_tokens": null,
    "supported_input_types": ["text"],
    "supported_output_types": ["text"],
    "supports_tools": null,
    "supports_structured_output": null,
    "supports_reasoning": null
  },
  "models": {
    "provider::model-id": {
      "context_window": 128000,
      "max_output_tokens": 8192,
      "supported_input_types": ["text", "image"],
      "supported_output_types": ["text"],
      "supports_tools": true
    }
  },
  "overrides": {}
}
```

`null` means unknown; it does not mean unsupported. Content type arrays are
extensible, and additional provider/model attributes are preserved. Provider
model discovery updates `models` without replacing explicit `overrides`. The
old `context_window_config.json` format is migrated automatically when the new
file is absent.
- `assets/cli_config.json` — terminal/CLI presentation, tool-call display, reasoning display, and file-change display.
- `assets/logging_config.json` — log level, output location (`.shai/logs/` by default), rotation, retention, and console logging.
- `assets/command_registry.json` — natural-language command registry.

Runtime configuration loaders merge the split files for consumers that need a unified view. Provider/model selection metadata remains in the core configuration; provider-specific endpoint and authentication overrides belong in `provider_config.json`. Context-window values, CLI presentation settings, and logging settings are persisted only to their dedicated files.


## Project path access policy

Generic project-access defaults and administrator-authored path rules live in
`assets/project_access_config.json`. This is a shared, application-level policy
file; it is separate from both the core `assets/shai_config.json` and each
project's user-approved grants in `.shai/project_access.json`.

Example `assets/project_access_config.json`:

```json
{
  "project_access": {
    "default": {
      "view": true,
      "edit": true,
      "truncate": false,
      "delete": false,
      "execute": true
    },
    "paths": [
      {"path": "src/**", "view": true, "edit": true},
      {"path": "secrets/**", "view": false, "edit": false, "execute": false},
      {"path": "docs/generated/**", "view": true, "edit": false}
    ]
  }
}
```

The project-local grant file is managed by Shai and should not normally be
edited manually. It contains versioned `grants` rules, for example:

```json
{
  "version": 1,
  "grants": [
    {"path": "private/**", "view": true, "edit": false,
     "truncate": false, "delete": false, "execute": false}
  ]
}
```

Permissions:

- `view` (`read` in prompts): read, list, search and inspect.
- `edit` (`write`): ordinary file creation or modification. Modifying an
  existing file also requires `view` because Shai must inspect the original to
  calculate a safe diff and show the change preview; creating a new file does
  not require read access.
- `truncate` (`write`): high-risk content reduction; separately enforced.
- `delete`: reserved for deletion tools; no general delete tool is enabled.
- `execute` (`exec`): controlled command working directories and execution targets.

When a policy blocks an operation in an interactive terminal, Shai displays
the exact path and requested capability, then asks for the grant scope:

1. This file/folder only.
2. Its immediate parent directory (including descendants).
3. Its level-2 parent directory (including descendants).
4. Deny this request.

Shai then asks which capabilities to grant: `read`, `write`, `delete`, `exec`,
`all`, or a comma-separated combination. `write` grants both ordinary edits
and high-risk truncation. `all` grants every supported capability. A denial or
invalid response does not persist anything. Grants are saved in the active
project's `.shai/project_access.json`, not in generic configuration. Existing
grants are merged for the same scope.

Generic rules are evaluated first; explicit project-local user grants are
evaluated afterward and can authorize a path blocked by a generic rule. This
allows a user to resolve a block interactively without weakening the generic
policy for other projects. A denial applies to the current request and is not
persisted. Non-interactive runs never prompt and remain denied.

- Paths are project-relative glob patterns. `**` matches descendants.
- Absolute paths, `..` traversal, and symlink escapes are rejected and cannot
  be approved through the prompt.
- Directory listing and recursive search omit denied paths; denied parent
  directories are pruned so their descendants are not disclosed.
- Access policy is independent of command/file approval settings. An approval
  does not itself grant path access.
- Missing or malformed generic policy falls back to built-in safe defaults.


## Project backup configuration

Automatic file backups are stored under `<project-root>/.shai/shai_backups/`.
Their cleanup policy is project-specific and lives in
`<project-root>/.shai/backup_config.json`:

```json
{
  "enabled": true,
  "retention_days": 1
}
```

If this file is missing or invalid, ShAI uses the safe defaults shown above.
The former `<project-root>/.shai_backups/` directory is migrated into the new
location during backup cleanup. Only the generated backup directory is ignored
by Git; `.shai/backup_config.json` remains available for project-specific policy.

## Logging location

Diagnostic logs are written to `<Shai-root>/.shai/logs/` by default.
The legacy `logs/` configuration value is treated as `.shai/logs/`, and
existing files in the old root-level `logs/` directory are moved into the
canonical directory when logging initializes. A custom absolute or relative
`log_dir` remains supported.


## Model routing

`assets/model_routing.json` is the tracked routing template. It declares every
supported task category with an empty candidate list, so routing is disabled
until a project configures candidates. The active project's override is `.shai/model_routing.json`. This specific
file is intentionally committed with the project so the team's routing policy
is shared. Other files under `.shai/` remain Git-ignored. The root `.gitignore`
uses `.shai/*` with an exception for `.shai/model_routing.json`.

For a populated example, see `assets/model_routing.sample.json`. Copy its
`models` entries into `.shai/model_routing.json` and replace every
`YOUR_*_MODEL` placeholder with an exact model identifier returned by that
provider's model discovery. The Ollama examples also require those models to
be installed locally.

Each category accepts an ordered candidate list. Lower `priority` numbers are
tried first:

```json
{
  "version": 1,
  "models": {
    "orchestrator": [
      {"provider": "ollama", "model": "qwen2.5:14b", "priority": 1},
      {"provider": "openrouter", "model": "vendor/model:free", "priority": 2}
    ],
    "planner": [],
    "code": [],
    "debug": [],
    "test": [],
    "chat": [],
    "commit_message": [],
    "review": [],
    "documentation": [],
    "research": []
  }
}
```

Supported categories are `orchestrator`, `planner`, `code`, `debug`, `test`,
`chat`, `commit_message`, `review`, `documentation`, and `research`. The
project file overrides only the categories it defines; unspecified categories
remain unconfigured. A candidate is selected only when its provider is
registered and its model appears in that provider's discovered model list.
Discovery results are cached briefly per project/provider. Invalid candidates
are skipped; missing roles, empty lists, unsupported versions, and exhausted
candidate lists fall back to the normal provider/model selected in Shai.

Chat requests use the `chat` role, Agent requests use `orchestrator`, and
commit-message generation from `/commit` and `/commit --message-only` uses
`commit_message`. The other roles are available for future task-specific call
sites as those workflows are split into independent model requests. Routing
does not change the default model saved by `/model`, provider credentials,
endpoint configuration, or context-window overrides.

Legacy `commit_message.provider` and `commit_message.model` fields are ignored
for model selection; the existing enable/prompt/pattern settings remain
separate.


### Empty model response retries

The `retry` object in `assets/shai_config.json` also controls successful API
responses that contain neither assistant text nor tool calls. This applies to
all registered providers, including Kilo and Kilo Free.

```json
"retry": {
  "empty_response_retry_enabled": true,
  "empty_response_max_retries": 2
}
```

- `empty_response_retry_enabled`: enables or disables retries for empty model
  responses.
- `empty_response_max_retries`: number of additional model requests after the
  initial empty response. `0` disables additional attempts; values are capped
  at `5`.

Each retry adds a temporary recovery instruction to a copy of the request
history. It does not permanently modify saved conversation history. A valid
text response or tool call ends the retry sequence immediately. Truncated
responses are handled by the existing truncation safeguards, not as empty
responses.
