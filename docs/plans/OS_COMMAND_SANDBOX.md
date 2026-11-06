# OS Command Sandboxing — Technical Design and Architecture

**Status: Forward-Looking Roadmap.**
The existing `os_command` implementation (documented in [`../OS_COMMAND.md`](../OS_COMMAND.md))
provides application-level allowlists for executables and subcommands, argument validation,
project-root path checks, minimal child environments, timeouts, and process-group cleanup.
However, application-level checks do not prevent allowlisted Python test commands (`pytest`,
`unittest`) from executing untrusted code that attempts network access or filesystem traversal.

This plan specifies the OS-level isolation boundary required to safely run untrusted
project code.

---

## 1. Threat Model & Security Objectives

### Threats Addressed
1. **Network Exfiltration:** A project test, `conftest.py`, or imported dependency attempting to transmit source code, environment secrets, or credentials over HTTP/DNS/raw sockets.
2. **Filesystem Escape:** Child processes reading or tampering with files outside the designated project root (e.g. `~/.ssh/`, `~/.aws/credentials`, `/etc/passwd`).
3. **Resource Exhaustion & Denial of Service:** Fork bombs, unbounded memory consumption, runaway CPU cycles, or infinite loops spawned by child processes.
4. **Credential Leakage via Environment:** Inheriting parent shell environment variables, API keys (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`), or Git credentials.

### Security Objectives
- **Zero Host Network Access:** Default complete network isolation for all child processes unless explicitly authorized.
- **Strictly Scoped Filesystem Mounts:** Only the active project directory is mounted writable; the rest of the filesystem is read-only or masked.
- **Fail-Closed on Unsupported Platforms:** If a requested security level cannot be guaranteed by the underlying OS, the command fails closed with an informative diagnostic.
- **Process Tree Annihilation:** On timeout, cancellation, or error, every child and grandchild process is guaranteed to be terminated.

---

## 2. Multi-Platform Isolation Architecture

Because ShAI supports diverse environments (Linux servers, macOS, and iOS/iSH), isolation
mechanisms are structured as a pluggable backend interface:

```python
class SandboxProvider(abc.ABC):
    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if this isolation provider can execute on the host."""
    
    @abc.abstractmethod
    def build_command_vector(self, spec: CommandSpec) -> list[str]:
        """Wrap the target command vector with platform isolation primitives."""
```

### 2.1 Linux Backend: Bubblewrap (`bwrap`) & User Namespaces
On Linux systems where unprivileged user namespaces or Bubblewrap are present:
- **Namespaces:** Unshare network (`CLONE_NEWNET`), PID (`CLONE_NEWPID`), IPC (`CLONE_NEWIPC`), UTS, and mounts (`CLONE_NEWNS`).
- **Mount Configuration:**
  - Read-only system paths: `--ro-bind /usr /usr`, `--ro-bind /lib /lib`, `--ro-bind /lib64 /lib64`, `--ro-bind /bin /bin`.
  - Clean tmpfs: `--tmpfs /tmp`, `--tmpfs /dev/shm`.
  - Minimal `/dev`: `--dev /dev` (providing null, zero, urandom).
  - Project Mount: `--bind <project_root> <project_root>` (mounted read-only for inspection commands; read-write only when editing/test execution requires it).
  - Masking: `--tmpfs /home`, `--tmpfs /root` (ensuring host user configs, SSH keys, and cloud credentials cannot be read).
- **Network Denial:** The child process receives an isolated loopback interface without a default gateway, making external socket connections immediately fail with `ENETUNREACH`.

### 2.2 macOS Backend: `sandbox-exec` & Seatbelt Profiles
On macOS, commands are wrapped using custom Seatbelt policy profiles:
```scheme
(version 1)
(deny default)
(allow process-exec)
(allow file-read* (subpath "/System") (subpath "/usr") (subpath "/Library"))
(allow file-read* file-write* (subpath "<project_root>"))
(allow file-read* file-write* (subpath "/private/tmp"))
(deny network*)
(deny file-read* (subpath (param "USER_HOME") "/.ssh"))
(deny file-read* (subpath (param "USER_HOME") "/.aws"))
```

### 2.3 iSH / Alpine / Restricted Environments
- In restricted environments (such as iOS iSH) where nested namespaces and Seatbelt are not supported, ShAI operates in **Restricted Emulation Mode**:
  - Process limits via `prlimit` / `setrlimit` (virtual memory caps, CPU time caps, max process count).
  - Pure argument allowlists and strict path checks.
  - Transparent user notification: ShAI informs the user that OS-level network isolation is inactive on this platform, requiring explicit user approval for test commands.

---

## 3. Resource Quotas & Process-Tree Management

Process execution in `modules/engine/process_control.py` is extended to enforce kernel-level limits:

1. **Memory Budget:** Capped at 512 MB via `RLIMIT_AS` (or cgroup `memory.max`). Memory allocations beyond the quota trigger immediate `SIGKILL` or memory error rather than exhausting host RAM.
2. **Process Count:** Capped at 32 concurrent processes per command via `RLIMIT_NPROC` to eliminate fork-bomb vectors.
3. **File Descriptor Budget:** Capped at 64 file descriptors via `RLIMIT_NOFILE`.
4. **Execution Timeout:** Enforced with wall-clock timers. On expiration:
   - Send `SIGTERM` to the process group (`os.killpg(pgid, signal.SIGTERM)`).
   - Grace period of 1.0 second.
   - Forceful cleanup with `SIGKILL` (`os.killpg(pgid, signal.SIGKILL)`).

---

## 4. Configuration Schema (`assets/command_config.json`)

```json
{
  "os_command_sandbox": {
    "enabled": false,
    "enforce_network_denial": true,
    "backend_preference": ["bwrap", "seatbelt", "rlimit"],
    "resource_limits": {
      "max_memory_mb": 512,
      "max_processes": 32,
      "max_cpu_seconds": 60,
      "max_output_bytes": 1048576
    },
    "path_isolation": {
      "mask_user_home": true,
      "isolate_tmp": true,
      "writable_project_only": true
    }
  }
}
```

---

## 5. Verification & Acceptance Criteria

1. **Adversarial Network Probe:** A Python test suite containing `socket.connect(("8.8.8.8", 53))` or `urllib.request.urlopen("https://example.com")` must fail immediately with a network unreachable error.
2. **Credential Masking:** Attempts to read `~/.ssh/id_rsa`, `~/.aws/credentials`, or parent directory files must fail with `FileNotFoundError` or `PermissionDenied`.
3. **Fork Bomb Defeat:** A command attempting recursive process spawning is stopped when `max_processes` is reached without degrading host stability.
4. **Clean Process Termination:** Long-running sleep processes or multi-threaded children are terminated with zero orphaned processes remaining.
5. **Contract Compatibility:** Standard `os_command` invocations (Git inspection, pytest, compileall) return identical status, exit codes, and output structures as the existing implementation.
