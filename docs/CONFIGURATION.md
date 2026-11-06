# Configuration layout

Shai configuration is stored under `assets/` and split by responsibility:

- `assets/shai_config.json` — core agent, runtime, display, retry, approval, and safeguard settings.
- `assets/provider_config.json` — provider endpoint/authentication overrides under `providers`.
- `assets/command_config.json` — Shai slash-command and OS command settings (`shai_command`, `os_command`).
- `assets/context_window_config.json` — per-model context limits and active context token limit.
- `assets/cli_config.json` — terminal/CLI presentation, tool-call display, reasoning display, and file-change display.
- `assets/logging_config.json` — log level, output location, rotation, retention, and console logging.
- `assets/command_registry.json` — natural-language command registry.

Runtime configuration loaders merge the split files for consumers that need a unified view. Provider/model selection metadata remains in the core configuration; provider-specific endpoint and authentication overrides belong in `provider_config.json`. Context-window values, CLI presentation settings, and logging settings are persisted only to their dedicated files.


## Project path access policy

Generic project-access defaults and administrator-authored path rules live only
inside the `project_access` object in `assets/shai_config.json`. Shai's
project-specific, user-approved exceptions are stored separately in the active
project's ignored `.shai/project_access.json` file. No second generic access
configuration file is used.

Example generic configuration (inside `assets/shai_config.json`):

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
