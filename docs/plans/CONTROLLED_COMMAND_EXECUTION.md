# Controlled Command Execution

## 1. Purpose

Provide Shai with narrowly scoped access to developer command-line tools while
preserving the project boundary, user control, and a clear audit trail.

This document defines the **execution boundary**: what may run, under which
conditions, and how the process is contained. Natural-language recognition and
slash-command routing are specified separately in
[`COMMAND_TRIGGERING.md`](COMMAND_TRIGGERING.md).

The central rule is:

> Recognizing an intent is not authorization to execute it. Every execution
> must pass through the same validated command policy, regardless of whether
> it originated from a slash command, a natural-language request, or an
> internal workflow.

## 2. Design principles

1. **Disabled by default.** No command process may be created unless command
   execution is explicitly enabled.
2. **No general shell by default.** Execute an approved executable with an
   argument vector and `shell=False`; do not interpret shell syntax.
3. **Least privilege.** Restrict executables, arguments, paths, environment,
   network access, resources, and duration.
4. **One policy path.** All entry points use the same validator, risk
   classifier, approval gate, runner, and audit logger.
5. **Explicit authorization.** Approval is bound to the exact command preview,
   not a vague intent or a reusable blanket permission.
6. **Fail closed.** Unknown commands, ambiguous paths, unsupported platforms,
   and policy errors must reject execution.
7. **Observable and interruptible.** Users can inspect what is running, see
   bounded output, and cancel it.

## 3. Execution architecture

Recommended flow:

```text
Slash command / natural-language intent / internal workflow
                         |
                         v
                Command dispatcher
                         |
                         v
              Canonical command request
                         |
                         v
          Registry + executable validation
                         |
                         v
             Argument and path validation
                         |
                         v
                 Risk classification
                         |
                         v
               Approval / policy gate
                         |
                         v
                 Contained runner
                         |
              +----------+----------+
              |                     |
              v                     v
        Bounded output          Audit event
              |
              v
       Result returned to agent
```

The dispatcher must never invoke `subprocess` directly. Only the contained
runner may create processes, and only after the policy gate has approved the
canonical request.

### Canonical request

Use a structured internal object rather than passing an arbitrary command
string:

```python
CommandRequest(
    executable="git",
    args=["status", "--short"],
    cwd="/workspace/project",
    timeout_seconds=15,
    source="slash_command",  # or "natural_language", "workflow"
    command_id="status",
)
```

The request should carry a registry command ID where applicable. Do not accept
a model-supplied executable path, shell string, environment map, or arbitrary
working directory as trusted input.

## 4. Configuration

The following is a proposed schema, not active configuration. Validate it at
startup and reject unknown or invalid values rather than silently weakening
policy.

```json
{
  "command_execution": {
    "enabled": false,
    "working_directory": "project",
    "allowed_paths": [],
    "executables": {
      "git": {
        "enabled": true,
        "path": null
      },
      "grep": {
        "enabled": true,
        "path": null
      },
      "find": {
        "enabled": true,
        "path": null
      },
      "python3": {
        "enabled": true,
        "path": null
      }
    },
    "policy": {
      "read_only": "allow",
      "local_mutation": "confirm",
      "repository_mutation": "confirm_each_time",
      "network": "deny",
      "privileged": "deny"
    },
    "limits": {
      "default_timeout_seconds": 30,
      "max_timeout_seconds": 120,
      "max_stdout_bytes": 20000,
      "max_stderr_bytes": 10000,
      "max_processes": 8
    },
    "environment": {
      "inherit": false,
      "allow": ["PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"]
    },
    "allow_shell": false,
    "require_exact_approval": true
  }
}
```

Notes:

- `allow` means the operation may run without a per-run prompt only when all
  other validation checks pass. It does not bypass path, executable, or
  resource restrictions.
- `confirm` means a preview and explicit approval are required.
- `confirm_each_time` must not be satisfied by a prior approval or a broad
  session-level setting.
- `deny` cannot be overridden by the model. Changing a denied policy requires
  an explicit user configuration change.
- Avoid a broad executable allowlist alone. `python3`, `git`, and other
  interpreters can perform arbitrary actions depending on their arguments.

## 5. Risk classification

Classify the **effective operation**, not just the executable name or a string
prefix. The classifier should consider executable identity, normalized
arguments, command-specific semantics, working directory, and known side
effects.

| Class | Examples | Default policy |
|---|---|---|
| Read-only local | `git status`, `git diff`, `grep`, `find`, `git log` | Allow, bounded |
| Local file mutation | formatters, `sed -i`, scripts that write files | Confirm |
| Repository mutation | `git add`, `git commit`, `git reset`, `git clean`, branch changes | Confirm each time |
| Remote or network | `git push`, `git fetch`, `git pull`, package installs, network clients | Deny unless explicitly enabled; confirm each time |
| Privileged / host-level | `sudo`, `su`, service control, device access, mount operations | Deny |
| Unknown / opaque | unregistered executable, unknown subcommand, arbitrary script | Deny or require a separately designed policy |

Important cases:

- `python3 script.py` is not inherently read-only. Treat scripts as opaque
  unless they are specifically registered and constrained.
- `git status` and `git push` have different risk despite sharing an
  executable.
- `git clean`, `git reset --hard`, force pushes, and commands that overwrite
  files need destructive-operation handling.
- `git fetch` and `git pull` access the network and can change local state.
- A read-only command can still disclose sensitive file contents. Path and
  output policies apply to every class.

When classification is uncertain, choose the more restrictive class.

## 6. Validation and containment

### 6.1 Executable resolution

- Resolve executables from a configured, trusted search path or an explicit
  absolute path configured by the operator.
- Resolve symlinks and verify the final executable is the configured target.
- Do not allow a project-local executable to shadow a trusted system tool
  unless explicitly configured.
- Reject executable names or paths containing shell syntax or control
  characters.
- Do not use `shell=True`, `os.system`, or shell command concatenation.

### 6.2 Arguments

- Pass arguments as a list; never join them into a shell command.
- Validate command-specific options and positional arguments.
- Reject NUL bytes and unsupported control characters.
- Do not assume `shell=False` makes an argument safe: tools may interpret
  arguments as options, paths, URLs, scripts, or configuration files.
- Use `--` to terminate option parsing where the target tool supports it.
- Do not let the model provide arbitrary interpreter flags, plugin paths, or
  configuration-file paths unless explicitly supported.

### 6.3 Working directory and filesystem boundary

- Resolve the project root and requested working directory to canonical paths.
- Require the canonical working directory to be the project root or a
  specifically allowed descendant.
- Resolve symlinks before checking containment. Reject symlink escapes.
- Validate path arguments according to the target command; checking only the
  working directory is insufficient.
- Reject device files, sockets, FIFOs, and other special files unless a
  narrowly scoped feature requires them.
- Account for time-of-check/time-of-use races where practical. Prefer directory
  file descriptors and platform primitives that avoid re-resolving attacker-
  controlled paths.
- Treat paths outside the project as inaccessible even if the operating
  system account can read them.

### 6.4 Environment and credentials

- Start from a minimal environment; do not inherit the complete parent
  environment by default.
- Pass only explicitly allowed variables.
- Never expose API keys, cloud credentials, SSH agents, browser profiles,
  credential stores, or unrelated host secrets to a child process.
- Do not log secret values. Redact sensitive argument values and environment
  entries.
- If a command needs credentials, design a dedicated credential handoff
  rather than copying secrets into arguments or environment variables.

### 6.5 Process and resource limits

- Enforce a timeout for every process.
- Bound stdout and stderr independently while continuing to drain pipes so a
  child cannot block indefinitely on a full pipe.
- Preserve a useful head/tail excerpt and report that output was truncated.
- Limit concurrent processes, CPU/memory where supported, and total execution
  time for a workflow.
- Start commands in an isolated process group/session where supported.
- On timeout or cancellation, terminate the process group, wait for exit, and
  escalate to a hard kill after a short grace period.
- Ensure descendants cannot outlive the command runner. Use OS-level
  containment where available; process-group termination alone is not a
  complete sandbox.

### 6.6 Network and host isolation

- Network access is denied by default. An executable allowlist is not a
  network policy.
- Do not treat denying `curl` and `wget` as sufficient: tools such as Git,
  Python, package managers, and custom scripts can access the network.
- Where feasible, enforce network restrictions outside the process through a
  sandbox, firewall, or restricted execution environment.
- Do not expose host-level sockets, devices, mounts, or privileged interfaces.
- If strong isolation is unavailable on a platform, document the reduced
  guarantee and keep high-risk classes disabled.

## 7. Approval and preview

For operations requiring confirmation, show the user a preview containing:

- Command ID and risk category.
- Resolved executable path and exact argument list.
- Canonical working directory.
- Filesystem scope and any known files expected to change.
- Whether network access is required.
- Timeout and output limits.
- Any destructive or irreversible effects known to the dispatcher.

Approval must be bound to a hash or immutable representation of the exact
canonical request. If any executable, argument, path, environment, or policy
field changes, approval is invalidated and must be requested again.

Never infer approval from phrases such as "go ahead" if multiple operations
are pending or the approved preview is no longer the current request.

## 8. Logging and user feedback

Record structured events for:

- Request received and its source.
- Validation and classification decisions.
- Approval requested, granted, or denied.
- Process start and completion.
- Exit code, duration, timeout, cancellation, and output truncation.
- Policy rejection reason.

Do not log full secret-bearing arguments or environment values. Logs should
make it possible to reconstruct what happened without recording credentials
or unnecessary file contents.

Return a structured result to the agent, for example:

```json
{
  "status": "completed",
  "exit_code": 0,
  "stdout": "## main...origin/main\n",
  "stderr": "",
  "stdout_truncated": false,
  "stderr_truncated": false,
  "duration_ms": 42
}
```

Use distinct statuses such as `rejected`, `approval_required`, `cancelled`,
`timed_out`, and `failed`. Do not represent a policy rejection as an ordinary
non-zero process exit.

## 9. Failure handling

- Reject malformed requests before process creation.
- Do not retry mutations automatically.
- Retry only explicitly classified transient, idempotent operations, with a
  small bounded retry count.
- Never retry after an ambiguous timeout for a non-idempotent operation:
  the process may have completed its side effect before termination.
- Surface the exact policy reason and a safe corrective action.
- Keep tool errors separate from command stdout/stderr.

## 10. Implementation sequence

1. Define and validate the configuration schema.
2. Implement canonical request and executable resolution.
3. Implement path and argument validation.
4. Implement risk classification and policy decisions.
5. Implement the contained subprocess runner with output and timeout limits.
6. Add approval previews and exact-request binding.
7. Add structured audit events and cancellation.
8. Route every command entry point through this single execution service.
9. Add platform-specific isolation where supported.
10. Enable the feature only after the security and regression tests pass.

Do not enable natural-language auto-execution as a shortcut around this
sequence.

## 11. Acceptance criteria

### Policy and validation

- [ ] Execution is impossible while `command_execution.enabled` is false.
- [ ] Unknown executables and unregistered command IDs are rejected before
      process creation.
- [ ] The resolved executable cannot be shadowed or replaced by a project-local
      binary.
- [ ] Shell metacharacters are passed as literal arguments and never interpreted.
- [ ] Traversal, symlink escapes, special files, and disallowed path arguments
      are rejected.
- [ ] Unknown or ambiguous operations fail closed.
- [ ] Network and privileged operations remain denied under default policy.

### Approval

- [ ] A mutating operation cannot reuse approval for a read-only operation.
- [ ] Any change to an approved request invalidates approval.
- [ ] Destructive operations require fresh, explicit approval.
- [ ] The preview shows the exact executable, arguments, working directory,
      risk, and relevant side effects.

### Runtime

- [ ] Timeout, cancellation, process cleanup, and output caps are enforced.
- [ ] stdout and stderr are bounded independently without pipe deadlocks.
- [ ] Child processes cannot silently outlive the runner under supported
      containment.
- [ ] Environment inheritance is disabled by default.
- [ ] Logs redact configured sensitive values.

### Integration

- [ ] Slash commands, natural-language triggers, and workflows use the same
      dispatcher and execution policy.
- [ ] A failed or rejected command is not reported as successful.
- [ ] Tests cover success, rejection, timeout, cancellation, output overflow,
      symlink escape, argument injection, and approval invalidation.

## 12. Non-goals

This design does not provide:

- An unrestricted interactive shell.
- A general-purpose remote execution service.
- A guarantee that arbitrary Python or other scripts are safe.
- A substitute for OS-level sandboxing when strong isolation is required.
