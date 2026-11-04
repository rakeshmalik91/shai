# Roadmap: Controlled Command-Line Tool Access

## Goal

Give Shai useful access to developer command-line utilities—such as `git`,
`grep`, `sed`, `awk`, `find`, `python`, and test runners—without exposing an
unrestricted shell or allowing commands to escape the project boundary.

## Execution model

- Introduce a `run_command` tool that accepts an executable, an argument array,
  a working directory and an optional timeout.
- Use `subprocess` with an argument list and `shell=False` by default.
- Resolve executables through a configured allowlist; do not accept arbitrary
  shell strings, command substitution, pipes or redirections by default.
- Provide a small set of structured operations for common pipelines rather
  than enabling a general shell.
- Restrict the working directory to the project root or explicitly allowed
  paths. Resolve symlinks before validating paths.
- Apply per-command timeout, output byte limits, process limits and interrupt
  handling. Kill child process groups on timeout/cancellation where supported.
- Never pass API keys, environment dumps or unrelated host secrets to commands.

## Configuration proposal

```json
{
  "command_execution": {
    "enabled": false,
    "allowlist": ["git", "grep", "sed", "find", "python3"],
    "denylist": ["sudo", "su", "ssh", "scp", "curl", "wget"],
    "working_directory": "project",
    "allowed_paths": [],
    "timeout_seconds": 30,
    "max_output_bytes": 20000,
    "allow_shell": false,
    "require_approval": true,
    "approval": {
      "read_only": true,
      "mutating": true,
      "network": true
    }
  }
}
```

This is a proposed schema, not active configuration. Defaults should remain
disabled until the feature and safeguards are implemented.

## Risk classification

| Class | Examples | Default policy |
|---|---|---|
| Read-only local | `git status`, `git diff`, `grep`, `find`, `python -m compileall` | Allowlisted; bounded; approval configurable |
| Local mutation | `sed -i`, formatters, scripts that write files, `git add` | Explicit approval |
| History/remote mutation | `git commit`, `git reset`, `git push`, `git clean` | Explicit approval every time by default |
| Network / external | `curl`, package installs, remote fetches | Disabled unless explicitly enabled |
| Privileged / escape | `sudo`, `su`, device paths, process control | Denied |

Commands must be classified by resolved executable and arguments, not by a
string prefix alone. A Python script can mutate files even though `python3`
is allowlisted, so execution remains a high-risk capability.

## Safeguards

- Canonicalize paths and reject traversal, symlink escapes and device files.
- Use a minimal environment allowlist; never inherit secrets indiscriminately.
- Bound stdout and stderr independently, preserving useful head/tail excerpts.
- Record executable, arguments, working directory, duration, exit status and
  approval decision in logs; redact sensitive argument values.
- Detect repeated identical failures and avoid uncontrolled retry loops.
- Make cancellation reliable and visible.
- Provide dry-run/preview support for commands that can expose a diff or plan.

## Acceptance criteria

- No arbitrary shell command runs when the feature is disabled.
- An executable outside the allowlist is rejected before process creation.
- Path traversal and symlink escapes are rejected.
- Timeouts and output caps are enforced and tested.
- Destructive, network and privileged operations cannot inherit read-only
  approval.
- The user can inspect exactly what will run before approving it.
