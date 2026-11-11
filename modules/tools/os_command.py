"""Configuration-driven, bounded OS command execution (without a shell)."""
import os
import shlex
import shutil
from pathlib import Path

from modules.engine.process_control import IterationInterrupted
from modules.tools.common import require_access, ProjectAccessDenied


class OSCommandRejected(ValueError):
    def __init__(self, message, whitelistable=False, whitelist_scope=None):
        super().__init__(message)
        self.whitelistable = whitelistable
        self.whitelist_scope = whitelist_scope


def _safe_project_path(root, value, label):
    if not isinstance(value, str) or not value.strip():
        raise OSCommandRejected("{} must be a non-empty project-relative path.".format(label))
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise OSCommandRejected("{} must remain inside the project directory.".format(label))
    base = Path(root).resolve()
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise OSCommandRejected("{} resolves outside the project directory.".format(label))
    if any(part in (".git", ".ssh", ".aws") for part in resolved.parts):
        raise OSCommandRejected("{} targets a protected directory.".format(label))
    if resolved.name.startswith(".env") or resolved.name in ("credentials.json", "secrets.json"):
        raise OSCommandRejected("{} targets a protected file.".format(label))
    if not resolved.exists() or not (resolved.is_file() or resolved.is_dir()):
        raise OSCommandRejected("{} must exist inside the project.".format(label))
    return resolved


def _validate_tokens(args):
    if not isinstance(args, list) or len(args) > 100:
        raise OSCommandRejected("args must be an array containing at most 100 strings.")
    result = []
    for token in args:
        if not isinstance(token, str) or len(token) > 8000:
            raise OSCommandRejected("Every argument must be a string no longer than 8000 characters.")
        if "\x00" in token or any(ord(ch) < 32 and ch not in "\t" for ch in token):
            raise OSCommandRejected("Arguments cannot contain NUL or control characters.")
        result.append(token)
    return result


def _minimal_environment():
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LC_ALL": "C",
    }


def execute_os_command(executable, args, cwd=".", timeout_seconds=None, *,
                       root, settings, confirm, runner, allow_blocked_once=False):
    """Run a configured executable with an argument vector and bounded output."""
    if not isinstance(settings, dict) or settings.get("enabled") is not True:
        return {"error": "os_command is disabled in command_config.json."}
    platform_key = "windows" if os.name == "nt" else "unix"
    platforms = settings.get("platforms")

    def merge_platform_value(common_value, platform_value, default):
        if isinstance(default, list):
            if not isinstance(common_value, list) or not isinstance(platform_value, list):
                raise OSCommandRejected("Platform command sections must be arrays.")
            return common_value + platform_value
        if not isinstance(common_value, dict) or not isinstance(platform_value, dict):
            raise OSCommandRejected("Platform rule sections must be objects.")
        merged = {name: list(items) for name, items in common_value.items()}
        for name, items in platform_value.items():
            merged[name] = list(merged.get(name, [])) + list(items)
        return merged

    try:
        if isinstance(platforms, dict):
            common_block = platforms.get("common", {})
            active_block = platforms.get(platform_key, {})
            if not isinstance(common_block, dict) or not isinstance(active_block, dict):
                raise OSCommandRejected("os_command.platforms common/platform entries must be objects.")

            def policy_value(name, default):
                return merge_platform_value(
                    common_block.get(name, default),
                    active_block.get(name, default),
                    default,
                )

            allowed = policy_value("allowed_commands", [])
            blocked = policy_value("blocked_commands", [])
            blocked_exceptions = policy_value("blocked_command_exceptions", {})
            blocked_rules = policy_value("blocked_command_rules", {})
            rule_exceptions = policy_value("blocked_command_rule_exceptions", {})
            unrestricted = policy_value("unrestricted_commands", [])
        else:
            # Backward compatibility for projects upgrading from the previous
            # top-level platform maps.
            allowed = settings.get("allowed_commands", [])
            if isinstance(allowed, dict):
                allowed = allowed.get("common", []) + allowed.get(platform_key, [])
            blocked = settings.get("blocked_commands", [])
            blocked_exceptions = settings.get("blocked_command_exceptions", {})
            blocked_rules = settings.get("blocked_command_rules", {})
            rule_exceptions = settings.get("blocked_command_rule_exceptions", {})
            unrestricted = settings.get("unrestricted_commands", [])
            for name, value, default in (
                ("blocked_commands", blocked, []),
                ("blocked_command_exceptions", blocked_exceptions, {}),
                ("blocked_command_rules", blocked_rules, {}),
                ("blocked_command_rule_exceptions", rule_exceptions, {}),
                ("unrestricted_commands", unrestricted, []),
            ):
                if isinstance(value, dict) and any(k in value for k in ("common", "unix", "windows")):
                    if isinstance(default, list):
                        value = value.get("common", []) + value.get(platform_key, [])
                    else:
                        value = merge_platform_value(value.get("common", {}), value.get(platform_key, {}), {})
                if name == "blocked_commands": blocked = value
                elif name == "blocked_command_exceptions": blocked_exceptions = value
                elif name == "blocked_command_rules": blocked_rules = value
                elif name == "blocked_command_rule_exceptions": rule_exceptions = value
                else: unrestricted = value

        if not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed):
            return {"error": "platform allowed_commands must contain command arrays."}
        if not isinstance(blocked, list) or not all(isinstance(x, str) for x in blocked):
            return {"error": "platform blocked_commands must be an array of command names."}
        if not isinstance(unrestricted, list) or not all(isinstance(x, str) for x in unrestricted):
            return {"error": "platform unrestricted_commands must be an array of command names."}
        if not all(isinstance(value, dict) for value in (blocked_exceptions, blocked_rules, rule_exceptions)):
            return {"error": "Platform blocked exception/rule settings must be objects."}
        if not isinstance(executable, str) or not executable or "/" in executable or "\\\\" in executable:
            return {"error": "Executable must be a command name, not a path."}

        tokens = _validate_tokens(args)

        def rule_tokens(value, label, allow_empty=False):
            if not isinstance(value, str) or (not value and not allow_empty):
                raise OSCommandRejected("{} entries must be command argument strings.".format(label))
            try:
                parsed = shlex.split(value)
            except ValueError as error:
                raise OSCommandRejected("Invalid {} entry {!r}: {}".format(label, value, error))
            if not parsed and not allow_empty:
                raise OSCommandRejected("{} entries cannot be empty.".format(label))
            return parsed

        # Exception entries are exact argument strings; blocked rules are
        # argument sequences matched anywhere in argv (e.g. "checkout --").
        exception_entries = blocked_exceptions.get(executable, [])
        if not isinstance(exception_entries, list) or not all(isinstance(x, str) for x in exception_entries):
            return {"error": "blocked_command_exceptions entries for {} must be strings.".format(executable)}
        exception_args = [rule_tokens(x, "blocked_command_exceptions") for x in exception_entries]

        rules = blocked_rules.get(executable, [])
        if not isinstance(rules, list) or not all(isinstance(x, str) for x in rules):
            return {"error": "blocked_command_rules entries for {} must be strings.".format(executable)}
        parsed_rules = [rule_tokens(x, "blocked_command_rules") for x in rules]
        rule_blocked = any(
            any(tokens[index:index + len(rule)] == rule
                for index in range(len(tokens) - len(rule) + 1))
            for rule in parsed_rules
        )
        whole_command_blocked = executable in blocked
        blocked_invocation = whole_command_blocked or rule_blocked

        allowed_rule_entries = rule_exceptions.get(executable, [])
        if not isinstance(allowed_rule_entries, list) or not all(isinstance(x, str) for x in allowed_rule_entries):
            return {"error": "blocked_command_rule_exceptions entries for {} must be strings.".format(executable)}
        allowed_rule_exceptions = [rule_tokens(x, "blocked_command_rule_exceptions") for x in allowed_rule_entries]
        rule_exception = any(
            any(tokens[index:index + len(rule)] == rule
                for index in range(len(tokens) - len(rule) + 1))
            for rule in allowed_rule_exceptions
        )
        blocked_exception = tokens in exception_args or rule_exception
        if blocked_invocation and not blocked_exception and not allow_blocked_once:
            return {
                "error": "{} invocation is blocked by os_command configuration.".format(executable),
                "status": "blocked_confirmation_required",
                "blocked_scope": "executable" if whole_command_blocked else "subcommand",
                "executable": executable,
                "arguments": tokens,
            }
        project_root = Path(root).resolve()
        working_directory = _safe_project_path(project_root, cwd, "cwd")
        require_access(cwd, "execute", root=project_root)
        # Existing project paths passed as arguments must also satisfy execute
        # access policy; ordinary flags, patterns and text arguments are untouched.
        for token in tokens:
            if token.startswith("-") or not token or not ("/" in token or (project_root / token).exists()):
                continue
            candidate = (project_root / token.split("::", 1)[0]).resolve()
            if candidate.exists():
                _safe_project_path(project_root, token.split("::", 1)[0], "command path")
                require_access(token.split("::", 1)[0], "execute", root=project_root)
        if executable not in allowed and not blocked_exception and not (blocked_invocation and allow_blocked_once):
            return {"error": "{} is not in os_command.allowed_commands.".format(executable),
                    "status": "whitelist_required", "whitelist_scope": "executable"}
        if not working_directory.is_dir():
            raise OSCommandRejected("cwd must be an existing project directory.")
        binary = shutil.which(executable)
        if not binary:
            raise OSCommandRejected("{} executable was not found on PATH.".format(executable))
        timeout = timeout_seconds if timeout_seconds is not None else settings.get("default_timeout_seconds", 30)
        maximum = settings.get("max_timeout_seconds", 120)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise OSCommandRejected("timeout_seconds must be a positive integer.")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            maximum = 120
        timeout = min(timeout, maximum, 120)
    except (OSCommandRejected, ProjectAccessDenied, OSError, ValueError) as error:
        return {"error": str(error)}

    command = [binary] + tokens
    preview = "\n".join((
        "Full command: {}".format(shlex.join(command)),
        "Executable: {}".format(binary),
        "Arguments: {}".format(shlex.join(tokens)),
        "Working directory: {}".format(working_directory),
        "Timeout: {} seconds".format(timeout),
        "Execution: no shell; command receives arguments directly.",
    ))
    command_profile = settings.get("commands", {}).get(executable, {}) if isinstance(settings.get("commands"), dict) else {}
    # Unrestricted commands bypass routine approval only; blocked rules above
    # still take precedence and require explicit permission.
    requires_approval = (
        executable not in unrestricted
        and settings.get("requires_approval", command_profile.get("requires_approval", True)) is True
    )
    if requires_approval and not confirm(preview):
        return {"error": "User declined os_command execution; no process was started.", "status": "rejected"}

    def bounded_setting(key, default, minimum=1, maximum=1048576):
        value = settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            value = default
        return max(minimum, min(value, maximum))

    output_settings = {
        "max_stdout_bytes": bounded_setting("max_stdout_bytes", 24576),
        "max_stderr_bytes": bounded_setting("max_stderr_bytes", 8192),
        "preserve_output_head_bytes": bounded_setting("preserve_output_head_bytes", 4096, 0),
        "preserve_output_tail_bytes": bounded_setting("preserve_output_tail_bytes", 4096, 0),
        "terminate_child_processes": settings.get("terminate_child_processes", True) is True,
    }
    try:
        result = runner(command, working_directory, timeout, output_settings, env=_minimal_environment())
    except IterationInterrupted:
        raise
    except Exception as error:
        return {"error": "os_command failed to start: {}".format(error)}
    status = "timed_out" if result.get("timed_out") else "completed" if result.get("returncode") == 0 else "failed"
    return {
        "status": status, "executable": executable, "arguments": tokens,
        "cwd": str(working_directory), "exit_code": result.get("returncode"),
        "duration_seconds": result.get("duration_seconds"),
        "stdout": result.get("stdout", ""), "stderr": result.get("stderr", ""),
        "output_truncated": result.get("output_truncated", False),
        "stdout_total_bytes": result.get("stdout_total_bytes", 0),
        "stderr_total_bytes": result.get("stderr_total_bytes", 0),
    }


def parse_os_command_line(value):
    """Parse /exec input without invoking a shell."""
    try:
        parts = shlex.split(value, posix=(os.name != "nt"))
    except ValueError as error:
        raise OSCommandRejected("Could not parse command arguments: {}".format(error))
    if not parts:
        raise OSCommandRejected("Usage: /exec <command> [arguments...]")
    return parts[0], parts[1:]
