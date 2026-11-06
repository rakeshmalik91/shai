import os
import sys
import json
import shlex
import ast
import re
import importlib.util
import platform
import urllib.request
import urllib.error
import pathlib
import signal
import socket
import difflib
import datetime
import urllib.parse
import select
import termios
import threading
import time
import random
import math
import tty
import _thread

from modules.ui.spinner import Spinner
from modules.ui.chat_runtime import extract_anthropic_response, response_text
from modules.commands.registry import normalize_command
from modules.commands.shai_command import resolve_shai_command
from modules.commands.help import HELP_TEXT
from modules.engine import (
    configuration, project_inspection, task_plan, git_operations,
    file_editing, provider_api, tool_execution, tool_presentation,
    history_recovery, model_orchestration,
)
from modules.engine.process_control import (
    BoundedOutputCapture, IterationInterrupted, CtrlDMonitor,
    run_bounded_interruptible_command, run_interruptible_call,
    run_interruptible_command,
)

from modules.terminal_style import colorize
from modules.tools.os_command import execute_os_command, parse_os_command_line, OSCommandRejected
from modules.tools import (
    configure_root as configure_tools_root,
    require_access, ProjectAccessDenied,
    FUNCTIONS_READ_ONLY, TOOL_DISPATCH_READ_ONLY,
)
from modules.model_routing import resolve_model as resolve_routed_model
from modules.provider_selection import (
    PROVIDERS, initialize_provider, get_available_models,
    get_provider_config, provider_api_key, build_provider_url,
    build_provider_headers, provider_timeout,
)
from modules.logging_config import init_default_logging, get_logger, log_api_payload
from modules.reasoning_handlers import display_reasoning_summary
from modules.conversation_history import ConversationHistory, input_with_history
from modules.file_change_display import print_change_preview
from modules.live_status import STATUS
from modules.context_meter import (
    estimate_messages_tokens, get_context_window, format_context_status,
    log_and_display_usage
)
from modules.agent_safeguards import (
    bound_tool_result, compact_conversation_history, validate_tool_arguments,
    ToolArgumentFailureTracker, read_file_cache_marker, marker_in_history,
    update_no_progress, tool_call_signature, format_tool_call_for_display
)

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[2]
ROOT = pathlib.Path(
    sys.argv[1] if len(sys.argv) > 1 else "."
).expanduser().resolve()
configure_tools_root(ROOT)

# In-memory task checklist for the current agent session/request.
TASK_PLAN = []

MAX_FILE_SIZE = 200_000
DEFAULT_MAX_TOOL_CALL_ITERATIONS = 80
HARD_MAX_TOOL_CALL_ITERATIONS = 1000
MAX_PLAN_CONTINUATIONS = 2
CONFIG_FILE = SCRIPT_DIR / "assets" / "shai_config.json"
TASK_FILE = SCRIPT_DIR / ".shai" / "task.json"
LOGGER = get_logger("shai.agent")
# Set by main() after provider selection; defaults keep helpers import-testable.
PROVIDER = "unknown"
MODEL = ""
# Install domain functions into this module's shared namespace. The extracted
# modules intentionally share runtime state to preserve existing monkeypatches,
# configuration overrides, task-plan state and provider integrations.
for _engine_module in (
    configuration, project_inspection, task_plan, git_operations,
    file_editing, provider_api, tool_execution, tool_presentation,
    history_recovery, model_orchestration,
):
    _engine_module.install(globals())
del _engine_module


def _call_with_routed_model(messages, task_role, request_callable):
    """Temporarily select a project route for one provider request."""
    global PROVIDER, MODEL
    default_provider, default_model = PROVIDER, MODEL
    route = resolve_routed_model(task_role, ROOT, default_provider, default_model)
    LOGGER.info(
        "Agent model route role=%s provider=%s model=%s routed=%s reason=%s",
        task_role, route["provider"], route["model"],
        route["routed"], route["reason"]
    )
    try:
        PROVIDER, MODEL = route["provider"], route["model"]
        return request_callable(messages)
    finally:
        PROVIDER, MODEL = default_provider, default_model


def api_request_for_role(messages, task_role="orchestrator"):
    """Call the provider adapter using the project-selected task model."""
    return _call_with_routed_model(messages, task_role, api_request)


def api_request_with_role_retries(messages, task_role="orchestrator"):
    """Use normal bounded retries with the selected task model."""
    return _call_with_routed_model(messages, task_role, api_request_with_retries)

DEFAULT_API_TIMEOUT_SECONDS = 300





DEFAULT_TOOL_CALL_DISPLAY = {
    "show_parameters": True,
    "max_argument_chars": 120
}





DEFAULT_TOOL_SAFEGUARDS = {
    "duplicate_call_detection": True,
    "duplicate_call_limit": 2,
    "repeat_call_warning": True,
    "repeat_call_warning_after": 2,
    "run_checks_timeout_seconds": 120,
    "git_status_timeout_seconds": 15,
    "git_diff_timeout_seconds": 15
}






API_TIMEOUT_SECONDS = load_api_timeout_seconds()






TOOL_SAFEGUARDS = load_tool_safeguards()
TOOL_CALL_DISPLAY = load_tool_call_display_settings()


DEFAULT_AGENT_SAFEGUARDS = {
    "enabled": True,
    "max_history_chars": 90000,
    "max_tool_result_chars": 8000,
    "keep_recent_turns": 4,
    "max_recent_tool_cycles": 6,
    "max_consecutive_no_progress_iterations": 3,
    "max_tool_calls_per_request": 80,
    "max_invalid_argument_attempts_per_tool": 2,
    "default_read_file_lines": 100,
    "max_read_file_lines": 500,
    "max_read_file_chars": 4000,
    "deduplicate_read_file_calls": True,
    "validate_tool_arguments": True,
    "reject_unknown_tool_arguments": True
}




AGENT_SAFEGUARDS = load_agent_safeguards()


DEFAULT_TEST_EXECUTION = {
    "timeout_seconds": 120,
    "max_output_bytes": 65536,
    "max_stdout_bytes": 49152,
    "max_stderr_bytes": 16384,
    "preserve_output_head_bytes": 4096,
    "preserve_output_tail_bytes": 8192,
    "terminate_child_processes": True
}




TEST_EXECUTION = load_test_execution_settings()








DEFAULT_RETRY_SETTINGS = {
    "enabled": True,
    "max_retries": 3,
    "initial_backoff_seconds": 1.0,
    "backoff_multiplier": 2.0,
    "max_backoff_seconds": 20.0,
    "jitter": True,
    "jitter_fraction": 0.2,
    "retry_network_errors": True,
    "retry_http_statuses": [408, 425, 429, 500, 502, 503, 504],
    "retry_invalid_json": True,
    "invalid_tool_selection_enabled": True,
    "invalid_tool_selection_max_attempts": 3,
    "invalid_tool_call_recovery": {
        "enabled": True,
        "max_recovery_attempts": 2,
        "repair_conversation_history": True,
        "preserve_user_request": True,
        "stop_on_repeated_failure": True,
        "max_tool_result_chars": 12000
    }
}




RETRY_SETTINGS = load_retry_settings()




MAX_ITERATIONS = load_max_tool_call_iterations()

DEFAULT_APPROVALS = {
    "all": False,
    "file_edit": False,
    "file_delete": False,
    "file_truncate": False,
    "execution": False,
    "git_commit": False,
    "git_push": False,
    "tools": {}
}
















# Restore the user's previous approval choice at startup.
YES_TO_ALL = load_yes_to_all()

SYSTEM_PROMPT = """
You are Shai, an autonomous coding assistant working inside a local project.

Your job is to understand the project, make requested changes, and verify them.

Rules:
- Inspect relevant files before proposing changes.
- Use tools to explore the project rather than guessing.
- Read existing code and follow its conventions.
- Make focused, minimal changes.
- Use get_file_outline, find_symbol, and find_references before broad source reads when useful.
- Prefer patch_file for exact unique text matches. If it fails due to whitespace, indentation, or a missing exact match, read the current line range and use replace_lines instead of repeating the same patch.
- Use update_task_plan for multi-step work and keep statuses current.
- Use verify_changes after meaningful code edits; report the checks actually run.
- Use edit_file only when the user explicitly requests a whole-file replacement or a new file.
- Never claim a file was changed unless the edit tool succeeded.
- Never claim tests passed unless run_checks confirms it.
- Explain important decisions and report remaining issues.
- Do not access files outside the project directory.
- Do not attempt to read secrets, API keys, credentials or environment files.
- Never expose environment variables.
- Ask for approval before changing files.
- Do not try to bypass approval.
"""

# -------------------------------------------------------------------
# API helpers
# -------------------------------------------------------------------

# -------------------------------------------------------------------
# Project safety
# -------------------------------------------------------------------




# -------------------------------------------------------------------
# Ctrl+D iteration interrupt
# -------------------------------------------------------------------









# -------------------------------------------------------------------
# Project tools
# -------------------------------------------------------------------





# -------------------------------------------------------------------
# Code intelligence and project understanding
# -------------------------------------------------------------------

IGNORED_PROJECT_DIRS = {
    ".shai_backups", "shai_backups", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".idea"
}


























# -------------------------------------------------------------------
# Git commit/push helpers
# -------------------------------------------------------------------

DEFAULT_COMMIT_MESSAGE_PATTERN = "{type}: {summary} {changes}"

COMMIT_TYPES = ("feat", "fix", "docs", "test", "chore", "refactor", "style", "perf", "ci", "build")

DEFAULT_COMMIT_SYSTEM_PROMPT = "You are a helpful assistant that generates concise, conventional commit messages."

DEFAULT_COMMIT_USER_PROMPT = (
    "Generate a concise, conventional commit message for the following changes.\n"
    "Follow the format: <type>: <summary>\n\n"
    "Types: feat, fix, docs, test, chore, refactor, style, perf, ci, build\n\n"
    "{diff_section}\n"
    "Return only the commit message, nothing else."
)

DEFAULT_COMMIT_DIFF_SECTION_WITH_DIFF = "Files changed:\n{changes}\n\nDiff:\n{diff}\n"
DEFAULT_COMMIT_DIFF_SECTION_WITHOUT_DIFF = "Changes:\n{changes}\n"



















# -------------------------------------------------------------------
# File editing
# -------------------------------------------------------------------

















# -------------------------------------------------------------------
# Test execution
# -------------------------------------------------------------------



def _load_os_command_settings():
    """Load OS command policy from its dedicated split configuration file."""
    command_file = CONFIG_FILE.parent / "command_config.json"
    try:
        with command_file.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            return {}
        settings = config.get("os_command", {})
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def os_command(executable, args, cwd=".", timeout_seconds=None):
    """Run one allowlisted OS command through the bounded execution boundary."""
    settings = _load_os_command_settings()
    LOGGER.info("os_command request executable=%s cwd=%s argument_count=%s",
                executable, cwd, len(args) if isinstance(args, list) else "invalid")

    def approve(preview):
        if approval_granted("execution", "os_command", include_all=False):
            LOGGER.info("os_command approved by explicit configuration")
            return True
        STATUS.suspend_line()
        from modules.engine.interaction import get_interaction_handler
        return get_interaction_handler().confirm_os_command(preview)

    result = execute_os_command(
        executable, args, cwd=cwd, timeout_seconds=timeout_seconds,
        root=ROOT, settings=settings, confirm=approve,
        runner=run_bounded_interruptible_command,
    )

    # When a positive Git allowlist blocks a request, offer narrowly scoped
    # persistent whitelisting in the interactive chat. Blacklist failures
    # never reach this branch and cannot be overridden here.
    if result.get("status") == "whitelist_required" and executable == "git":
        STATUS.suspend_line()
        full_command = "git " + shlex.join(args)
        from modules.engine.interaction import get_interaction_handler
        choice = get_interaction_handler().request_git_whitelist_scope(full_command, args)
        if choice in ("1", "2", "3"):
            try:
                command_config_file = CONFIG_FILE.parent / "command_config.json"
                with command_config_file.open("r", encoding="utf-8") as handle:
                    persisted = json.load(handle)
                git_profile = persisted.setdefault("os_command", {}).setdefault("commands", {}).setdefault("git", {})
                if choice == "1":
                    entries = git_profile.setdefault("approved_commands", [])
                    if args not in entries:
                        entries.append(list(args))
                    scope_label = "exact command"
                elif choice == "2":
                    entries = git_profile.setdefault("approved_subcommands", [])
                    subcommand = args[0]
                    if subcommand not in entries:
                        entries.append(subcommand)
                    scope_label = "git {} subcommand".format(subcommand)
                else:
                    git_profile["allow_unrestricted"] = True
                    scope_label = "all Git commands"
                temporary = command_config_file.with_name(command_config_file.name + ".tmp")
                with temporary.open("w", encoding="utf-8") as handle:
                    json.dump(persisted, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
                temporary.replace(command_config_file)
                print("[Shai] Whitelisted {}. Retrying the command.".format(scope_label))
                settings = _load_os_command_settings()
                result = execute_os_command(
                    executable, args, cwd=cwd, timeout_seconds=timeout_seconds,
                    root=ROOT, settings=settings, confirm=approve,
                    runner=run_bounded_interruptible_command,
                )
            except (OSError, ValueError, TypeError, KeyError) as error:
                print("[Shai] Could not save whitelist: {}".format(error))
                result = {"error": "Could not persist Git whitelist: {}".format(error), "status": "rejected"}
        else:
            result = {"error": "User declined to whitelist the Git command; no process was started.", "status": "rejected"}

    LOGGER.info("os_command result executable=%s status=%s exit_code=%s",
                executable, result.get("status", "rejected"), result.get("exit_code"))
    return result


# -------------------------------------------------------------------
# Tool declarations
# -------------------------------------------------------------------

# Canonical model-facing declarations live in assets/tools_registry.json.
from modules.tools.registry import load_tool_functions
FUNCTIONS = load_tool_functions()


TOOL_DISPATCH = {
    "list_files": list_files,
    "read_file": read_file,
    "search_files": search_files,
    "git_status": git_status,
    "git_diff": git_diff,
    "edit_file": edit_file,
    "patch_file": patch_file,
    "run_checks": run_checks,
    "replace_lines": replace_lines,
    "get_file_outline": get_file_outline,
    "find_symbol": find_symbol,
    "find_references": find_references,
    "verify_changes": verify_changes,
    "inspect_test_failure": inspect_test_failure,
    "get_project_map": get_project_map,
    "get_test_map": get_test_map,
    "update_task_plan": update_task_plan,
    "os_command": os_command
}


# -------------------------------------------------------------------
# API request
# -------------------------------------------------------------------

class APIRequestError(RuntimeError):
    def __init__(self, message, status_code=None, network_error=False,
                 invalid_json=False, invalid_tool_call=False):
        super().__init__(message)
        self.status_code = status_code
        self.network_error = network_error
        self.invalid_json = invalid_json
        self.invalid_tool_call = invalid_tool_call


class InvalidToolCallRecoveryFailure(RuntimeError):
    """Raised when provider-side invalid tool-call recovery is exhausted."""












# -------------------------------------------------------------------
# Agent loop
# -------------------------------------------------------------------

class ToolSelectionFailure(Exception):
    """Raised when the model repeatedly selects tools Shai does not expose."""







































# -------------------------------------------------------------------
# Interactive terminal
# -------------------------------------------------------------------



def main():
    """Forward to the extracted CLI runner in modules.ui.agent_cli."""
    from modules.ui.agent_cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    main()
