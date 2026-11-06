# Future Plan: OS Command Sandboxing

**Status: Not implemented.** The current `os_command` tool is restricted by
application-level executable/subcommand allowlists, argument validation,
project-root checks, a minimal environment, timeouts, output caps, and process
cleanup. These controls are useful but are not an OS security boundary.

## Goals

- Run commands in an OS/container sandbox with a project-only writable mount.
- Enforce network denial outside the child process, including for Python test
  code and imported plugins.
- Restrict devices, host sockets, credentials, and inherited file descriptors.
- Apply CPU, memory, process-count, disk and wall-clock limits.
- Keep process-group cleanup and bounded stdout/stderr capture.
- Preserve an explicit user approval preview for operations that execute
  project code.

## Non-goals

- Turning `os_command` into an unrestricted shell.
- Allowing a model-supplied executable path or arbitrary shell string.
- Treating a Python test command as safe merely because it is allowlisted.

## Work required

1. Evaluate platform-specific options for Linux, macOS and iSH; do not assume a
   Linux namespace or container runtime exists on every supported platform.
2. Define a sandbox capability interface and report when a platform cannot
   provide a requested isolation guarantee.
3. Mount the active project with the minimum write permissions needed for the
   operation; keep credentials and user home directories unavailable.
4. Enforce network denial at the sandbox/firewall layer.
5. Add resource quotas and process-tree cleanup.
6. Add adversarial tests for symlinks, child processes, network attempts,
   credential access, output floods, timeouts and cancellation.
7. Keep unsupported platforms fail-closed for command classes that require
   strong isolation.

## Acceptance criteria

- A child cannot read host credentials or files outside its declared mounts.
- Network access is denied when the policy says it is denied.
- Descendant processes cannot survive cancellation or timeout.
- Resource and output budgets are enforced.
- The runtime clearly reports the isolation level actually provided.
- The existing `os_command` argument and result contracts remain compatible.
