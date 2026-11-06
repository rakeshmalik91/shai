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
| `git` | `status`, summary/path-scoped `diff`, `log` (including `--oneline -10` and bounded pretty formats), `branch --show-current`, `rev-parse`, metadata-only `show` | No per-run approval by default; only read-only subcommands are accepted |
| `python3` | `-m pytest`, `-m unittest discover`, `-m compileall` | Approval required by default |

The executable is resolved by Shai; the model cannot supply an arbitrary
executable path. Python script execution, package installation, arbitrary
interpreters, Git mutation, remote/network Git operations, privileged commands,
pipes, redirects, command substitution, and shell expansion are not supported.

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
3. Validates the command profile and every supported option.
4. Resolves `cwd` and path arguments beneath the active project root.
5. Rejects traversal, absolute paths, protected paths, and unsupported command
   forms. Shell syntax is never interpreted; arguments are passed literally.
6. Constructs an argument vector and invokes the process with `shell=False`.
7. Applies the configured timeout, independent stdout/stderr caps, and process
   group cleanup where supported.
8. Returns structured status, exit code, duration, output, and truncation data.

Git runs with system/global configuration disabled, pager/editor interaction
suppressed, fsmonitor disabled, and external diff/textconv disabled for diff
and show operations. To support repositories on shared or mounted filesystems
whose owner differs from the current process, Shai passes
`-c safe.directory=<canonical project root>` to that Git invocation only. It
does not modify global Git configuration or trust additional directories. Child processes receive a minimal environment rather than
the full parent environment.

The project-root check is an application-level boundary, not a complete OS
sandbox. Same-user processes may still access other resources allowed by the
operating system. Use OS/container sandboxing if stronger isolation is needed.

## Configuration

```json
{
  "shai_command": {"enabled": true},
  "os_command": {
    "enabled": true,
    "default_timeout_seconds": 30,
    "max_timeout_seconds": 120,
    "max_stdout_bytes": 24576,
    "max_stderr_bytes": 8192,
    "preserve_output_head_bytes": 4096,
    "preserve_output_tail_bytes": 4096,
    "terminate_child_processes": true,
    "commands": {
      "git": {
        "enabled": true,
        "subcommands": ["status", "diff", "log", "branch", "rev-parse", "show"],
        "requires_approval": false
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

The command profile is validated in code as well as configured here. Adding an
executable or subcommand requires a code review and tests; adding a string to
configuration alone cannot authorize an unknown executable or arbitrary
operation.

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
