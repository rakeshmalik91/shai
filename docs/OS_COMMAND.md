# `os_command` — Controlled Operating-System Command Execution

**Status: Implemented with a deliberately restricted command profile**

## Purpose

`os_command` gives Agent mode a controlled way to run a small set of approved
operating-system commands. It is **not an interactive shell** and must not be
expanded into unrestricted shell execution by passing command strings to a
shell.

The canonical CLI command is `/exec`. The older `/os-command` spelling
remains a backward-compatible alias. Both route to the same `os_command` tool
and execution policy. Arguments are parsed into an array and executed with
`shell=False`; this is not an interactive shell.

## Supported command profiles

| Executable | Supported operations | Approval |
|---|---|---|
| `git` | Configurable. Restricted mode uses the subcommands and argument rules in `assets/command_config.json`; unrestricted mode accepts any Git subcommand and arguments. | Configurable with `requires_approval` |
| `python3` | `-m pytest`, `-m unittest discover`, `-m compileall` | Approval required by default |

The executable is resolved by Shai; the model cannot supply an arbitrary
executable path. Python script execution, package installation, arbitrary
interpreters, privileged commands, pipes, redirects, command substitution, and
shell expansion are not supported. Git may be configured as restricted or
unrestricted; unrestricted Git can perform mutations, remote/network operations,
and other actions supported by the installed Git executable.

Python test modules execute project code. A test suite or `conftest.py` can
perform arbitrary actions, including network access. Shai does not currently
provide an OS-level sandbox or enforce network isolation for child processes;
that is why Python execution requires approval by default. Do not run
untrusted project tests merely because the command is allowlisted.

## Tool interface

The model-callable tool is named `os_command` and is registered only in Agent
mode. Its parameters are:

| Parameter | Type | Description |
|---|---|---|
| `executable` | string | `git` or `python3` |
| `args` | string array | Individual arguments; never a shell command string |
| `cwd` | string | Optional project-relative working directory; defaults to `.` |
| `timeout_seconds` | integer | Optional timeout, capped by configuration and a hard 120-second ceiling |

Example tool requests:

```json
{"executable":"git","args":["status","--short","--branch"]}
{"executable":"python3","args":["-m","pytest","-q"]}
```

The CLI equivalent is:

```text
/exec git status --short --branch
/exec python3 -m pytest -q
```

`/os-command` is accepted as a legacy alias. Arguments are parsed with `shlex` and
passed as a list to a process runner; shell interpretation is never enabled.

## Validation and execution

Implementation lives in `modules/tools/os_command.py` and uses the shared
bounded process runner in `modules/engine/process_control.py`.

Before a process starts, Shai:

1. Checks that `os_command.enabled` is true.
2. Resolves the executable from the fixed allowlist.
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
pager interaction suppressed, and terminal prompting disabled. In restricted
mode, configured per-invocation options can disable fsmonitor and external
diff/textconv. The default configuration adds the canonical project root as a
per-invocation `safe.directory` value; it does not modify global Git
configuration. Unrestricted mode bypasses Git argument validation and does not
inject restricted-mode subcommand options. Child processes receive a minimal
environment rather than the full parent environment.

The project-root check is an application-level boundary, not a complete OS
sandbox. Same-user processes may still access other resources allowed by the
operating system. Use OS/container sandboxing if stronger isolation is needed.

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
configured timeout/output limits, project-relative working directory, and the
configured approval setting.

To customize policy, edit `subcommands`, `argument_rules`,
`blocked_subcommands`, `forbidden_options`, `forbidden_option_prefixes`,
`forbidden_argument_patterns`, and the injected-option maps in `assets/command_config.json`.
`forbidden_argument_patterns` contains regular expressions tested against each
argument token. `argument_rules` supports `allowed_args`, `value_options`,
`numeric_options`, `max_numeric_value`, regular-expression `patterns`,
`positional_patterns`, `allow_project_paths_after_separator`, and `require_any`.
The implementation contains the generic rule evaluator; individual Git option
allowlists and supported formats live in JSON.

The executable identity (`git` or `python3`) remains an application-level
allowlist. Within Git, restricted subcommands and their argument policies are
configuration-driven; adding a subcommand with a corresponding rule does not
require editing the validator. Unrestricted mode bypasses those Git-specific
rules entirely.

Python execution approval uses the existing `approve.tools.os_command` or
`approve.execution` policy. An explicit tool-level `false` takes precedence.
If neither an explicit approval setting nor a prior approved configuration
applies, Shai asks for a one-time confirmation showing the executable,
arguments, working directory, and timeout. The broad `approve.all` setting is
not sufficient by itself for this new execution boundary. The checked-in
configuration sets `approve.tools.os_command` to `false`, which forces a fresh
confirmation for Python execution even if the broader `approve.execution`
category is enabled.

## Result semantics

- `completed`: process exited with code 0.
- `failed`: process exited with a non-zero code.
- `timed_out`: execution exceeded its configured deadline.
- `rejected`: validation or user approval prevented process creation.

A rejected command is not an ordinary process failure. A timeout does not
prove that a side effect did not occur; never automatically retry a mutation
based on an ambiguous timeout.

## Tests

`test/test_os_command.py` covers executable allowlisting, Git read-only
subcommands, disallowed mutation, Python module restrictions, path traversal,
working-directory restrictions, approval rejection, disabled configuration,
argument-vector parsing, and tool registration.

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
`assets/command_config.json` under the top-level `os_command` object.
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
