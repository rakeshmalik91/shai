"""Controlled, non-shell operating-system command execution for Shai.

This is intentionally not a general-purpose shell. Only reviewed command
profiles and argument forms are accepted; every process is bounded and runs
inside the configured project directory.
"""
import os
import re
import shlex
import shutil
import sys
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
    if candidate.is_absolute():
        raise OSCommandRejected("{} must be project-relative; absolute paths are not allowed.".format(label))
    if any(part == ".." for part in candidate.parts):
        raise OSCommandRejected("{} cannot escape the project directory.".format(label))
    resolved_root = Path(root).resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        raise OSCommandRejected("{} resolves outside the project directory.".format(label))
    if any(part in (".git", ".ssh", ".aws") for part in resolved.parts):
        raise OSCommandRejected("{} targets a protected directory.".format(label))
    if resolved.name.startswith(".env") or resolved.name in ("credentials.json", "secrets.json"):
        raise OSCommandRejected("{} targets a protected file.".format(label))
    if not resolved.exists():
        raise OSCommandRejected("{} does not exist inside the project.".format(label))
    if not resolved.is_file() and not resolved.is_dir():
        raise OSCommandRejected("{} must be a regular file or directory.".format(label))
    return resolved


def _validate_tokens(args):
    if not isinstance(args, list) or len(args) > 40:
        raise OSCommandRejected("args must be an array containing at most 40 strings.")
    result = []
    for token in args:
        if not isinstance(token, str) or len(token) > 2000:
            raise OSCommandRejected("Every argument must be a string no longer than 2000 characters.")
        if "\x00" in token or any(ord(ch) < 32 and ch not in "\t" for ch in token):
            raise OSCommandRejected("Arguments cannot contain NUL or control characters.")
        result.append(token)
    return result


def _validate_git_args(args, profile, root):
    """Apply configurable Git blacklists, then optional allowlist rules.

    Unrestricted mode bypasses only the positive allowlists and argument rules.
    Explicit blacklists always apply in both modes.
    """
    # Negative policy is unconditional: unrestricted means no allowlist, not
    # permission to execute explicitly blocked commands or arguments.
    blocked_subcommands = profile.get("blocked_subcommands", [])
    if args and isinstance(blocked_subcommands, list) and args[0] in blocked_subcommands:
        raise OSCommandRejected("Git subcommand is blocked by os_command configuration.")

    forbidden = profile.get("forbidden_options", [])
    prefixes = profile.get("forbidden_option_prefixes", [])
    forbidden_patterns = profile.get("forbidden_argument_patterns", [])
    for token in args:
        if isinstance(forbidden, list) and token in forbidden:
            raise OSCommandRejected("Git option is blocked by os_command configuration.")
        if isinstance(prefixes, list) and any(
            isinstance(prefix, str) and prefix and token.startswith(prefix)
            for prefix in prefixes
        ):
            raise OSCommandRejected("Git option is blocked by os_command configuration.")
        if isinstance(forbidden_patterns, list) and any(
            isinstance(pattern, str) and re.search(pattern, token)
            for pattern in forbidden_patterns
        ):
            raise OSCommandRejected("Git argument matches a blocked pattern in os_command configuration.")

    # Explicit user-approved entries bypass positive allowlists only. The
    # blacklist above is always evaluated first and cannot be whitelisted.
    approved_commands = profile.get("approved_commands", [])
    if isinstance(approved_commands, list) and args in approved_commands:
        return args
    approved_subcommands = profile.get("approved_subcommands", [])
    subcommand = args[0] if args else ""
    if profile.get("allow_unrestricted") is True:
        return args
    if isinstance(approved_subcommands, list) and subcommand in approved_subcommands:
        return args

    subcommands = profile.get("subcommands", [])
    if not args or not isinstance(subcommands, list) or args[0] not in subcommands:
        raise OSCommandRejected("Git subcommand is not in the os_command allowlist.", True, "subcommand")

    rules = profile.get("argument_rules", {})
    rule = rules.get(subcommand) if isinstance(rules, dict) else None
    if not isinstance(rule, dict):
        raise OSCommandRejected("No argument policy is configured for git {}.".format(subcommand), True, "subcommand")

    tail = args[1:]

    allowed = rule.get("allowed_args", [])
    value_options = rule.get("value_options", [])
    numeric_options = rule.get("numeric_options", [])
    patterns = rule.get("patterns", [])
    numeric_patterns = rule.get("numeric_patterns", [])
    positional_patterns = rule.get("positional_patterns", [])
    max_numeric = rule.get("max_numeric_value", 1000)
    allow_paths = rule.get("allow_project_paths_after_separator") is True
    after_separator = False
    seen = []
    index = 0
    while index < len(tail):
        token = tail[index]
        if token == "--" and allow_paths:
            after_separator = True
            index += 1
            continue
        if after_separator:
            _validate_git_path(token, root)
            seen.append("<path>")
            index += 1
            continue
        if token in allowed:
            seen.append(token)
            index += 1
            continue
        if token in value_options:
            if index + 1 >= len(tail):
                raise OSCommandRejected("{} requires a value.".format(token))
            seen.append(token)
            index += 2
            continue
        matched_numeric = False
        for option in numeric_options:
            if token == option:
                if index + 1 >= len(tail) or not tail[index + 1].isdigit():
                    raise OSCommandRejected("{} requires a numeric value.".format(token))
                number = int(tail[index + 1])
                if number < 1 or number > max_numeric:
                    raise OSCommandRejected("{} value must be between 1 and {}.".format(option, max_numeric))
                seen.append(option)
                index += 2
                matched_numeric = True
                break
            if token.startswith(option + "="):
                value = token.split("=", 1)[1]
                if not value.isdigit() or not 1 <= int(value) <= max_numeric:
                    raise OSCommandRejected("{} value must be between 1 and {}.".format(option, max_numeric))
                seen.append(option)
                index += 1
                matched_numeric = True
                break
        if matched_numeric:
            continue
        matched_pattern = False
        for pattern_rule in numeric_patterns:
            if not isinstance(pattern_rule, dict) or not isinstance(pattern_rule.get("pattern"), str):
                continue
            if re.fullmatch(pattern_rule["pattern"], token):
                number_match = re.search(r"[0-9]+", token)
                maximum = pattern_rule.get("max", max_numeric)
                if number_match and isinstance(maximum, int) and int(number_match.group()) > maximum:
                    raise OSCommandRejected("Git numeric argument must not exceed {}.".format(maximum))
                seen.append("<pattern>")
                index += 1
                matched_pattern = True
                break
        if matched_pattern:
            continue
        if any(isinstance(pattern, str) and re.fullmatch(pattern, token) for pattern in patterns):
            seen.append("<pattern>")
            index += 1
            continue
        if not token.startswith("-") and any(
            isinstance(pattern, str) and re.fullmatch(pattern, token)
            for pattern in positional_patterns
        ):
            seen.append("<positional>")
            index += 1
            continue
        raise OSCommandRejected("Unsupported git {} argument: {}".format(subcommand, token), True, "command")

    required_any = rule.get("require_any", [])
    if required_any and not any(item in seen for item in required_any):
        raise OSCommandRejected(rule.get("require_any_message", "Git command is missing a required summary option."))
    return args


def _validate_git_path(value, root):
    if not value or value.startswith("-"):
        raise OSCommandRejected("Git path arguments must not be options.")
    if value:
        # Git pathspecs are constrained to the project and reject magic syntax.
        if (value.startswith(":(") or "\\" in value
                or any(char in value for char in "*?[]")):
            raise OSCommandRejected("Git pathspec magic, wildcards and backslash paths are not allowed.")
        parts = Path(value).parts
        if ".." in parts or Path(value).is_absolute():
            raise OSCommandRejected("Git pathspec escapes the project directory.")
        if any(part == ".git" or part.startswith(".env") or part in (".ssh", ".aws", "credentials.json", "secrets.json") for part in parts):
            raise OSCommandRejected("Git pathspec targets a protected path.")
        project_root = Path(root).resolve()
        resolved = (project_root / Path(value)).resolve()
        try:
            resolved.relative_to(project_root)
        except ValueError:
            raise OSCommandRejected("Git pathspec resolves outside the project directory.")
        if any(part == ".git" or part.startswith(".env") or part in (".ssh", ".aws", "credentials.json", "secrets.json") for part in resolved.parts):
            raise OSCommandRejected("Git pathspec resolves to a protected path.")
        require_access(value, "view", root=project_root)
        if resolved.exists() and resolved.is_dir():
            raise OSCommandRejected("Git pathspec must identify a file, not a directory.")


def _validate_python_args(args, profile, root):
    hardcoded_modules = {"pytest", "unittest", "compileall"}
    if (len(args) < 2 or args[0] != "-m" or args[1] not in hardcoded_modules
            or args[1] not in profile.get("modules", [])):
        raise OSCommandRejected("Python execution is limited to approved -m modules (pytest, unittest, compileall).")
    module = args[1]
    tail = args[2:]
    if module == "unittest" and (not tail or tail[0] != "discover"):
        raise OSCommandRejected("unittest is limited to discovery mode; module-name execution is not allowed.")
    allowed_flags = {
        "pytest": {"-q", "-v", "-vv", "-x", "--disable-warnings", "--tb=short", "--tb=line"},
        "unittest": {"discover", "-v", "-q"},
        "compileall": {"-q", "-f"}
    }[module]
    index = 0
    while index < len(tail):
        token = tail[index]
        if token in allowed_flags:
            index += 1
            continue
        if module == "unittest" and token in ("-s", "-p", "-t"):
            if index + 1 >= len(tail):
                raise OSCommandRejected("{} requires a value.".format(token))
            value = tail[index + 1]
            if token in ("-s", "-t"):
                _safe_project_path(root, value, "Python test path")
                require_access(value, "execute", root=root)
            elif not re.fullmatch(r"[A-Za-z0-9_*?.-]+", value):
                raise OSCommandRejected("Invalid unittest filename pattern.")
            index += 2
            continue
        if token.startswith("-"):
            raise OSCommandRejected("Unsupported {} argument: {}".format(module, token))
        if module == "unittest":
            raise OSCommandRejected("unittest discovery accepts only discover options; positional module names are not allowed.")
        # Pytest node IDs may contain ::; validate only their file component.
        path_part = token.split("::", 1)[0]
        _safe_project_path(root, path_part, "Python target")
        require_access(path_part, "execute", root=root)
        index += 1
    return args


def _minimal_environment():
    env = {
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
    return env


def execute_os_command(executable, args, cwd=".", timeout_seconds=None, *,
                       root, settings, confirm, runner):
    """Validate, preview, approve and execute one allowlisted command."""
    if not isinstance(settings, dict) or settings.get("enabled") is not True:
        return {"error": "os_command is disabled in command_config.json."}
    configured_commands = settings.get("commands", {})
    if not isinstance(configured_commands, dict):
        return {"error": "Invalid os_command.commands configuration; expected an object."}
    if executable not in ("git", "python3"):
        return {"error": "Executable is not allowlisted. Allowed executables: git, python3."}
    tokens = _validate_tokens(args)
    command_settings = configured_commands.get(executable, {})
    if not isinstance(command_settings, dict) or command_settings.get("enabled") is not True:
        return {"error": "{} is disabled in the os_command configuration.".format(executable)}

    project_root = Path(root).resolve()
    try:
        working_directory = _safe_project_path(project_root, cwd, "cwd")
        require_access(cwd, "execute", root=project_root)
        if not working_directory.is_dir():
            raise OSCommandRejected("cwd must resolve to an existing project directory.")
        if executable == "git":
            tokens = _validate_git_args(tokens, command_settings, project_root)
            binary = shutil.which("git")
            if not binary:
                raise OSCommandRejected("git executable was not found on PATH.")
        else:
            tokens = _validate_python_args(tokens, command_settings, project_root)
            binary = sys.executable
        timeout = timeout_seconds if timeout_seconds is not None else settings.get("default_timeout_seconds", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise OSCommandRejected("timeout_seconds must be a positive integer.")
        maximum = settings.get("max_timeout_seconds", 120)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            maximum = 120
        maximum = min(maximum, 120)  # hard runtime ceiling; config cannot remove it
        timeout = min(timeout, maximum)
    except (OSCommandRejected, ProjectAccessDenied, OSError, ValueError) as error:
        response = {"error": str(error)}
        if isinstance(error, OSCommandRejected) and error.whitelistable:
            response.update({"status": "whitelist_required", "whitelist_scope": error.whitelist_scope})
        return response

    # Git's dubious-ownership check is intentionally satisfied per invocation,
    # for this canonical project root only. Do not write safe.directory to the
    # user's global Git configuration.
    if executable == "git":
        prefix = command_settings.get("injected_global_options", [])
        if not isinstance(prefix, list) or not all(isinstance(item, str) for item in prefix):
            return {"error": "Invalid os_command.commands.git.injected_global_options configuration."}
        prefix = [item.replace("${PROJECT_ROOT}", str(project_root)) for item in prefix]
        command = [binary] + prefix + tokens
        injected = command_settings.get("injected_subcommand_options", {})
        if (command_settings.get("allow_unrestricted") is not True
                and isinstance(injected, dict) and tokens):
            extra = injected.get(tokens[0], [])
            if isinstance(extra, list) and all(isinstance(item, str) for item in extra):
                command = [binary] + prefix + [tokens[0]] + extra + tokens[1:]
    else:
        command = [binary] + tokens
    preview = "\n".join((
        "Full command: {}".format(shlex.join(command)),
        "Executable: {}".format(binary),
        "Arguments: {}".format(shlex.join(tokens)),
        "Working directory: {}".format(working_directory),
        "Timeout: {} seconds".format(timeout),
        "Network isolation: not provided; Python test code may access the network.",
    ))
    requires_approval = command_settings.get("requires_approval", True) is True
    if requires_approval and not confirm(preview):
        return {"error": "User declined os_command execution; no process was started.",
                "status": "rejected"}

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
        "terminate_child_processes": settings.get("terminate_child_processes") is True,
    }
    try:
        result = runner(command, working_directory, timeout, output_settings,
                        env=_minimal_environment())
    except IterationInterrupted:
        raise
    except Exception as error:
        return {"error": "os_command failed to start: {}".format(error)}

    status = ("timed_out" if result.get("timed_out") else
              "completed" if result.get("returncode") == 0 else "failed")
    return {
        "status": status,
        "executable": executable,
        "arguments": tokens,
        "cwd": str(working_directory),
        "exit_code": result.get("returncode"),
        "duration_seconds": result.get("duration_seconds"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "output_truncated": result.get("output_truncated", False),
        "stdout_total_bytes": result.get("stdout_total_bytes", 0),
        "stderr_total_bytes": result.get("stderr_total_bytes", 0),
    }


def parse_os_command_line(value):
    """Parse the manual /os-command syntax without invoking a shell."""
    try:
        parts = shlex.split(value, posix=(os.name != "nt"))
    except ValueError as error:
        raise OSCommandRejected("Could not parse command arguments: {}".format(error))
    if not parts:
        raise OSCommandRejected("Usage: /exec git status [options] or /exec python3 -m pytest [options]")
    return parts[0], parts[1:]
