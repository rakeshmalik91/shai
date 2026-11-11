# `os_command` — Controlled Operating-System Command Execution

**Status: Implemented with a deliberately restricted command profile**

## Purpose

Shai exposes two separate command-execution paths:

- The model-facing `os_command` tool is available to the coding agent. It
  enforces `assets/command_config.json`, including executable allowlists,
  blocked commands/rules, exceptions, project access controls, execution
  bounds, and interactive approval.
- `/exec` and its `/os-command` alias are CLI-only entry points for commands
  typed directly by the human. They bypass command_config allowlists,
  blocklists, blocked rules, and routine approval. They retain bounded process
  execution and a project-root working-directory boundary.

The two paths share the bounded process runner but do not share command-policy
permissions. Arguments are parsed into an array and executed with `shell=False`;
shell operators are not interpreted automatically. A human may explicitly
invoke a shell executable through `/exec` if desired.

## Supported command profiles

The model-facing executable is resolved from the configured allowlist.
Manual `/exec` accepts any executable available on PATH (or an explicit
executable path). Model calls remain subject to blocked executable and argument
rules, except for configured exact exceptions. The checked-in examples permit
only informational `sudo --help`, `sudo --version`, `su --help`, and
`su --version` forms for the model. Manual CLI commands bypass these rules.

Python test modules execute project code. A test suite or `conftest.py` can
perform arbitrary actions, including network access. Shai does not currently
provide an OS-level sandbox or enforce network isolation for child processes.
Model-initiated execution requires configured approval unless the executable
is explicitly listed in `unrestricted_commands`; manual `/exec` is always an
explicit human action and skips command-policy approval.


## Destructive-operation defaults

The default `assets/command_config.json` platform blocks whole executables inside each `os_command.platforms` block:

- Unix: `rm`, `rmdir`, `shred`
- Windows: `del`, `erase`, `rd`, `format`, `diskpart`, `shutdown`
- Privilege escalation: `sudo`, `su`

It also blocks these Git subcommands: `reset`, `push`, `clean`,
`checkout --`, and `restore`. Windows `reg delete` and forced `taskkill /F`
are blocked as well. Inline script execution through common shell wrappers
(`sh -c`, `bash -c`, `cmd /c`, PowerShell `-Command`/`-File`, and equivalents)
also requires an explicit exception because the nested script can perform
destructive operations that cannot be safely inferred from the outer argv. The rules are argument-sequence matches, so `git status`,
`git diff`, and `git log` remain available.

When a command is not allowlisted or a blocked operation needs an explicit
exception, the interactive prompt offers:

1. **Allow once** — run this invocation once; do not change configuration.
2. **Allow command permanently** — approve every invocation of this executable.
   Example: `python3` approves all `python3` commands.
3. **Allow subcommand permanently** — shown only for command-oriented tools
   when a valid subcommand is present. Example: `git status` approves all
   `git status` invocations, but not `git push` or `git reset`.
4. **Allow exact command permanently** — approve only this executable plus the
   exact argument vector. Example: `python3 -c 'print(1)'` does not approve a
   different script or different Python code.
5. **Deny** — do not start the process.

Permanent choices are stored in `approved_commands`, `approved_subcommands`,
and `approved_invocations`. For a blocked operation, the matching exception is
also stored in `blocked_commands`/`blocked_command_rules` exception settings.
Subcommand approval is deliberately unavailable for ordinary utilities such as
`rm`, where the first positional argument is a file target rather than a
subcommand. The subcommand menu is enabled only for known command-oriented
executables such as Git, pip, npm, Docker, Cargo, and systemctl.

Example configuration:

```json
{
  "approved_commands": ["python3"],
  "approved_subcommands": {"git": [["status"]]},
  "approved_invocations": {"python3": [["-c", "print(1)"]]}
}
```

These controls are an application-level command policy, not a complete
security sandbox. In particular, a permitted interpreter or shell can invoke
other operations internally; do not run untrusted scripts or PowerShell
expressions merely because their top-level executable is allowlisted.

## Tool interface

The model-facing `os_command` tool is registered in
`assets/tools_registry.json` (Agent mode only) and dispatched to
`modules.engine.agent_runtime.os_command`. Its parameters are:

| Parameter | Type | Description |
|---|---|---|
| `executable` | string | `git` or `python3` |
| `args` | string array | Individual arguments; never a shell command string |
| `cwd` | string | Optional project-relative working directory; defaults to `.` |
| `timeout_seconds` | integer | Optional timeout, capped by configuration and a hard 120-second ceiling |

Examples of model tool arguments (approval/policy applies):

```json
{"executable":"git","args":["status","--short","--branch"]}
{"executable":"python3","args":["-m","pytest","-q"]}
```

Examples of direct human CLI usage (no command_config approval or allowlist):

```text
/exec git status --short --branch
/exec python3 -m pytest -q
/exec rm -rf build
```

The built-in `/push` and `/commit-push` commands route their push operation
through this same policy; they cannot bypass the default `git push` block.

`/os-command` is accepted as a legacy alias. Arguments are parsed with `shlex` and
passed as a list to a process runner; shell interpretation is never enabled.

## Validation and execution

Implementation lives in `modules/tools/os_command.py` and uses the shared
bounded process runner in `modules/engine/process_control.py`.

Before a process starts, Shai:

1. For model calls, checks that `os_command.enabled` is true. Manual CLI calls
   bypass this command-policy enable switch.
2. For model calls, resolves the executable from the configured allowlist.
   Manual CLI calls resolve any available executable or explicit executable path.
3. Applies the configured Git policy (or Python module policy). In Git
   unrestricted mode, positive allowlists and argument rules are bypassed, but
   the configured Git blacklists are still enforced.
4. Resolves `cwd` beneath the active project root. Restricted Git path rules
   additionally validate path arguments.
5. Rejects traversal, absolute paths, protected paths, and unsupported command
   forms. Shell syntax is never interpreted; arguments are passed literally.
6. Constructs an argument vector and invokes the process with `shell=False`.
7. Applies the configured timeout, independent stdout/stderr caps, and process
   group cleanup where supported.
8. Returns structured status, exit code, duration, output, and truncation data.

Git receives a minimal environment with system/global configuration disabled,
pager interaction suppressed, and terminal prompting disabled. The Git-specific
tool uses its restricted invocation options. `/exec git ...` creates a temporary
global Git config file containing only the canonical project root as
`safe.directory`, points `GIT_CONFIG_GLOBAL` at that file for the child process,
and deletes the file immediately after execution. This protected global-config
scope is required because Git may ignore command-line `-c safe.directory`
settings for ownership checks. The user's real global Git configuration is
never modified. In restricted Git-tool mode, configured per-invocation options
can also disable fsmonitor and external diff/textconv.
Unrestricted mode bypasses Git argument validation and does not inject
restricted-mode subcommand options. Child processes receive a minimal
environment rather than the full parent environment.

The project-root check is an application-level boundary, not a complete OS
sandbox. Same-user processes may still access other resources allowed by the
operating system. Use OS/container sandboxing if stronger isolation is needed.

## Platform-scoped command policy (current schema)

All command lists and destructive-operation policies live inside
`os_command.platforms.common`, `os_command.platforms.unix`, and
`os_command.platforms.windows`. Each platform block contains its own
`allowed_commands`, `unrestricted_commands`, `unrestricted_subcommands`,
`blocked_commands`, `blocked_command_exceptions`, `blocked_command_rules`,
and `blocked_command_rule_exceptions`.

`common` entries are inherited by both Unix and Windows. The active platform
block adds its entries. This keeps platform-specific policy together and avoids
parallel top-level lists.

Example:

```json
{
  "os_command": {
    "requires_approval": true,
    "platforms": {
      "common": {
        "allowed_commands": ["git", "python3"],
        "unrestricted_commands": [],
        "unrestricted_subcommands": {
          "git": [
            "status", "diff", "log", "show", "rev-parse", "ls-files",
            "describe", "diff-tree", "cat-file", "for-each-ref",
            "name-rev", "check-ignore", "count-objects", "remote -v",
            "tag -l", "branch --list", "branch --show-current",
            "branch -a", "branch -r"
          ]
        },
        "blocked_commands": [],
        "blocked_command_exceptions": {},
        "blocked_command_rules": {},
        "blocked_command_rule_exceptions": {}
      },
      "unix": {
        "allowed_commands": ["ls", "grep", "sed", "awk", "jq"],
        "unrestricted_commands": [
          "ls", "pwd", "cat", "head", "tail", "wc", "grep",
          "stat", "file", "du", "df", "ps", "pgrep", "uptime",
          "free", "uname", "hostname", "id", "whoami", "date",
          "which", "whereis", "printenv", "env", "jq", "tree"
        ],
        "unrestricted_subcommands": {},
        "blocked_commands": ["sudo", "su", "rm", "rmdir", "shred"],
        "blocked_command_exceptions": {
          "sudo": ["--help", "--version"],
          "su": ["--help", "--version"]
        },
        "blocked_command_rules": {
          "git": ["reset", "push", "clean", "checkout --", "restore"],
          "sh": ["-c", "-lc", "-ec"],
          "bash": ["-c", "-lc", "-ec"]
        },
        "blocked_command_rule_exceptions": {}
      },
      "windows": {
        "allowed_commands": ["where", "whoami", "ipconfig", "tasklist"],
        "unrestricted_commands": [
          "where", "whoami", "ver", "systeminfo", "ipconfig",
          "netstat", "ping", "tracert", "nslookup", "tasklist",
          "driverquery", "tree", "findstr", "find", "fc", "vol"
        ],
        "unrestricted_subcommands": {},
        "blocked_commands": ["del", "erase", "rd", "format", "diskpart", "shutdown"],
        "blocked_command_exceptions": {},
        "blocked_command_rules": {
          "reg": ["delete"],
          "taskkill": ["/F"],
          "cmd": ["/c", "/k"],
          "powershell": ["-Command", "-File", "-EncodedCommand"]
        },
        "blocked_command_rule_exceptions": {}
      }
    }
  }
}
```

`unrestricted_commands` bypasses routine execution approval for an entire
executable. Use `unrestricted_subcommands` for safer, argument-prefix-specific
exceptions. Each value is a string tokenized like blocked-rule entries; for
example, `"branch --list"` matches `git branch --list` and its additional
read-only options, but not `git branch -d`.

The default common Git policy marks read-only operations such as `status`,
`diff`, `log`, `show`, `rev-parse`, `ls-files`, `describe`, `remote -v`,
`tag -l`, and read-only branch listing as unrestricted. Other Git operations
still require approval. Blocked commands/rules always take precedence over
unrestricted subcommands. Git output-writing (`--output`) and external
diff/text-conversion options (`--ext-diff`, `--textconv`) also disable the
unrestricted approval bypass.

Neither setting bypasses project access checks, executable allowlisting, or
bounded execution. Commands capable of modifying files, processes, services,
or system configuration remain subject to explicit approval or blocked-command
handling.

`blocked_command_exceptions` contains exact argument strings. For example,
`"--version"` permits exactly that argument list and rejects
`"--version extra"`. `blocked_command_rules` contains argument-sequence strings
matched anywhere in argv; `"checkout --"` therefore catches
`git checkout -- file` as well as the equivalent invocation with Git global
options before the subcommand. Strings are tokenized before matching.

## Configuration

```json
{
  "os_command": {
    "enabled": true,
    "default_timeout_seconds": 30,
    "max_timeout_seconds": 120,
    "commands": {
      "git": {
        "enabled": true,
        "allow_unrestricted": false,
        "requires_approval": false,
        "subcommands": ["status", "diff", "log", "branch", "rev-parse", "show"],
        "injected_global_options": [
          "-c", "safe.directory=${PROJECT_ROOT}",
          "-c", "core.fsmonitor=false",
          "-c", "core.pager=cat"
        ],
        "injected_subcommand_options": {
          "diff": ["--no-ext-diff", "--no-textconv"],
          "show": ["--no-ext-diff", "--no-textconv"]
        },
        "forbidden_options": ["-c", "-C", "--config-env", "--exec-path", "--git-dir", "--work-tree"],
        "forbidden_option_prefixes": ["--config-env=", "--git-dir=", "--work-tree="],
        "argument_rules": {
          "log": {
            "allowed_args": ["--oneline", "--format=short", "--format=fuller"],
            "numeric_options": ["-n", "--max-count"],
            "max_numeric_value": 1000
          }
        }
      },
      "python3": {
        "enabled": true,
        "modules": ["pytest", "unittest", "compileall"],
        "requires_approval": true
      }
    },
    "unrestricted_commands": {
      "common": [],
      "unix": [],
      "windows": []
    },
    "blocked_commands": {
      "common": [],
      "unix": ["sudo", "su", "rm", "rmdir", "shred"],
      "windows": ["del", "erase", "rd", "format", "diskpart", "shutdown"]
    },
    "blocked_command_exceptions": {
      "common": {},
      "unix": {"sudo": ["--help", "--version"], "su": ["--help", "--version"]},
      "windows": {}
    },
    "blocked_command_rules": {
      "common": {},
      "unix": {"git": ["reset", "push", "clean", "checkout --", "restore"]},
      "windows": {"reg": ["delete"], "taskkill": ["/F"]}
    }
  }
}
```

To allow every Git subcommand and its arguments, set
`os_command.commands.git.allow_unrestricted` to `true`. This bypasses only the
positive subcommand allowlist and argument rules. The negative policy always
applies in both modes: `blocked_subcommands`, `forbidden_options`,
`forbidden_option_prefixes`, and `forbidden_argument_patterns`. Blacklist checks
are performed against the supplied argument vector before any injected options
are added. This does **not** turn `/exec` into a shell: arguments remain an
array and execution still uses `shell=False`, the bounded process runner,
configured timeout/output limits, and project-relative working directory.
Because `/exec` is explicitly typed by the human, routine approval is skipped.

To customize policy, edit `subcommands`, `argument_rules`,
`blocked_subcommands`, `forbidden_options`, `forbidden_option_prefixes`,
`forbidden_argument_patterns`, and the injected-option maps in `assets/command_config.json`.
`blocked_command_exceptions` is an optional platform-scoped map from a blocked
executable to an array of argument strings. It is an exception list, not a
prefix or glob matcher: `["--help", "--version"]` permits exactly those two
invocations and rejects `--version anything`. `blocked_command_rules` uses
platform-scoped maps of executable names to argument-sequence strings such as
`"checkout --"`; each string is tokenized before matching. An exception bypasses the general executable
allowlist only for that exact invocation; it does not remove the executable
from `blocked_commands` or permit other arguments. Keep exceptions limited to
non-mutating informational forms. `forbidden_argument_patterns` contains
regular expressions tested against each argument token. `argument_rules` supports `allowed_args`, `value_options`,
`numeric_options`, `max_numeric_value`, regular-expression `patterns`,
`positional_patterns`, `allow_project_paths_after_separator`, and `require_any`.
The implementation contains the generic rule evaluator; individual Git option
allowlists and supported formats live in JSON.

The executable identity (`git` or `python3`) remains an application-level
allowlist. Within Git, restricted subcommands and their argument policies are
configuration-driven; adding a subcommand with a corresponding rule does not
require editing the validator. Unrestricted mode bypasses those Git-specific
rules entirely.

The interactive `/exec` command skips routine execution approval because
the command is explicitly typed by the human operator. This does not bypass
the executable allowlist, blocked-command rules, project access checks, or
execution limits. A command that matches a blocked rule still follows the
explicit blocked-command confirmation/exception flow. LLM tool calls cannot
reach this execution path because `os_command` is not registered as a model
tool.

## Result semantics

- `completed`: process exited with code 0.
- `failed`: process exited with a non-zero code.
- `timed_out`: execution exceeded its configured deadline.
- `rejected`: validation or user approval prevented process creation.

A rejected command is not an ordinary process failure. A timeout does not
prove that a side effect did not occur; never automatically retry a mutation
based on an ambiguous timeout.

## Tests

`test/test_os_command.py` covers executable allowlisting, exact informational
exceptions for blocked commands, rejection of extra arguments to those
exceptions, path traversal, working-directory restrictions, approval-policy handling, disabled configuration, argument-vector parsing,
manual-CLI policy bypass, and the LLM-facing approval workflow.

### Interactive Git allowlisting

When a Git command is rejected by the positive allowlist (not by a blacklist),
Shai displays the complete command and asks whether to whitelist it:

1. Exact command and parameters (`approved_commands`).
2. Every parameter for that Git subcommand (`approved_subcommands`).
3. Every Git command and subcommand (`allow_unrestricted`).

The selected permission is persisted in `assets/command_config.json`, then the command is
retried. Explicit blacklist rules (`blocked_subcommands`, forbidden options,
option prefixes, and argument patterns) are evaluated first and cannot be
bypassed by any whitelist choice. The approval preview also displays the full
resolved command, including injected Git options.


## Configuration source of truth

All `os_command` enablement and execution policy is read from
`assets/command_config.json` under `os_command`. Executable allowlists,
unrestricted commands, and blocked-command policy are grouped under
`os_command.platforms.common`, `os_command.platforms.unix`, and
`os_command.platforms.windows`.
The legacy `assets/shai_config.json` file is not consulted for command
enablement. Interactive Git whitelist choices are persisted back to
`assets/command_config.json` (never to the core configuration file), then the
policy is reloaded before the blocked command is retried.


## Project path access policy

The command profile allowlist is not the only path boundary. Before execution,
Shai also checks `execute` permission for the canonical working directory and
Python test/compile targets. Explicit Git pathspecs require `view`. These
checks use generic rules from
`assets/project_access_config.json` plus user-approved project-local grants in
`.shai/project_access.json`. They cannot be bypassed by command whitelisting or
approval. Absolute paths, parent
traversal and symlink escapes remain rejected. See `docs/CONFIGURATION.md`.


## Guidance for the coding model

The model may invoke the registered `os_command` tool for legitimate
command-line tasks. The tool follows the model command policy and interactive
approval flow. It must not emulate or invoke the manual `/exec` or
`/os-command` CLI entry points. Those are reserved for commands typed directly
by the human and bypass command_config allowlists, blocklists, blocked rules,
and routine approval.

Prefer dedicated Git/file/test tools when they directly support the requested
operation. Use `os_command` when a command-line operation is needed and no
dedicated tool is suitable. Never work around a denied model command by
switching to a different tool or pretending the human ran `/exec`.
