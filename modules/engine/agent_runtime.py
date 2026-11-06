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
    FUNCTIONS_READ_ONLY, TOOL_DISPATCH_READ_ONLY,
)
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
    ".git", ".shai_backups", ".venv", "venv", "node_modules",
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
        print("\n[Shai] os_command approval required:")
        print(preview)
        try:
            answer = input("Run this exact command? [y/N]: ").strip().lower()
        except EOFError:
            raise IterationInterrupted()
        return answer in ("y", "yes")

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
        print("\n[Shai] Git command blocked by the configured allowlist:")
        print("  " + full_command)
        print("\nWhitelist this command? Choose the scope:")
        print("  1. This exact command and parameters only")
        print("  2. All parameters for git {}".format(args[0] if args else "(no subcommand)"))
        print("  3. All Git commands and subcommands (unrestricted)")
        print("  0. No, keep it blocked")
        try:
            choice = input("Whitelist scope [0-3]: ").strip()
        except EOFError:
            raise IterationInterrupted()
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

FUNCTIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List project files under a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 project file in line-based chunks. ALWAYS supply "
                "all three arguments: path, offset, and limit. For the first "
                "chunk use offset=0. offset is a zero-based line offset and "
                "limit is the number of lines to return (1-500). If the result "
                "shows More content available, make another read_file call "
                "with the SAME path and limit and offset set to the exact "
                "next_offset shown. Never repeat an earlier offset unless "
                "the user explicitly asks you to reread it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500}
                },
                "required": ["path", "offset", "limit"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search for a case-insensitive text string. path may be either "
                "a single project file (for example agent.py) or a project "
                "directory (for example modules). Use a file path when searching "
                "one known file; use a directory for recursive search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"}
                },
                "required": ["query", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show current Git status.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show unstaged Git changes.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Propose replacing a project file. "
                "The user must approve the displayed diff."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": (
                "Make a targeted section edit by replacing one exact, unique "
                "text block. Prefer this over regenerating an entire file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"}
                },
                "required": ["path", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_checks",
            "description": "Run an approved Python test or syntax check.",
            "parameters": {
                "type": "object",
                "properties": {
                    "check": {
                        "type": "string",
                        "enum": ["pytest", "unittest", "compileall"]
                    },
                    "path": {"type": "string"}
                },
                "required": ["check", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": "Replace a 1-based inclusive line range in a project file. Prefer this when exact patch text is failing due to indentation or whitespace. Read the file first, use its actual line numbers, and provide the complete replacement text for the selected range. The user must approve the displayed diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "new_text": {"type": "string"}
                },
                "required": ["path", "start_line", "end_line", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_outline",
            "description": "For a Python file, return top-level imports, classes, functions and methods with line ranges. Use this before reading a large source file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": "Find Python definitions of a class, function, method or assignment by exact name under a project file or directory.",
            "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "path": {"type": "string"}}, "required": ["symbol", "path"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": "Search project text files for usages of a symbol. This is a textual search, not a semantic language-server reference search.",
            "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "path": {"type": "string"}}, "required": ["symbol", "path"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify_changes",
            "description": "Run Python compileall and optionally unittest or pytest in a project directory. Requires user approval and uses configured time/output limits.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "test_check": {"type": "string", "enum": ["unittest", "pytest", "none"]}}, "required": ["path", "test_check"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_test_failure",
            "description": "Extract likely failing test names, assertion errors, tracebacks and relevant context from supplied test output. Does not execute tests.",
            "parameters": {"type": "object", "properties": {"output": {"type": "string"}}, "required": ["output"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_map",
            "description": "Summarize project directories, file types and likely Python entry points. Skips generated and dependency directories.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_test_map",
            "description": "List test files and heuristically map their names to likely source modules. Treat mappings as hints, not proof.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_task_plan",
            "description": "Create or update the visible task checklist. Supply the complete current list; statuses are pending, in_progress, completed, or blocked. Update it as work progresses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked"]}
                            },
                            "required": ["title", "status"]
                        }
                    }
                },
                "required": ["steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "os_command",
            "description": (
                "Run a tightly allowlisted operating-system command in the project. "
                "This is NOT a shell: use executable=git with read-only subcommands "
                "(status, diff, log, branch --show-current, rev-parse, show), or "
                "executable=python3 with -m pytest, -m unittest discover, or -m compileall. "
                "No pipes, redirects, shell expansion, scripts, arbitrary executables, "
                "network commands, or privileged operations are supported."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "executable": {"type": "string", "enum": ["git", "python3"]},
                    "args": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
                    "cwd": {"type": "string", "default": "."},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}
                },
                "required": ["executable", "args"]
            }
        }
    }

]

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
    global PROVIDER, MODEL, ROOT

    init_default_logging(load_config())
    LOGGER.info("Starting Shai coding agent root=%s api_timeout=%ss",
                ROOT, API_TIMEOUT_SECONDS)
    PROVIDER, MODEL = initialize_provider(auto_select=True)
    LOGGER.info("Selected provider=%s model=%s", PROVIDER, MODEL)

    cleanup_old_backups()

    _load_task_plan()

    history_store = ConversationHistory(ROOT, "agent", SYSTEM_PROMPT)
    history = history_store.load()
    previous_provider = history_store.metadata().get("provider")
    if previous_provider and previous_provider != PROVIDER:
        history = history_for_new_provider(history)
        print("[Shai] Previous provider changed; retained text history and cleared old tool exchanges.")
    history_store.save(history, provider=PROVIDER, model=MODEL)

    # Clear terminal at startup
    os.system("cls" if os.name == "nt" else "clear")

    print("\n" + colorize("Shai - Coding Agent", "heading"))
    print("Project:", ROOT)
    print("Provider:", PROVIDER)
    print("Model:", MODEL)
    print("Commands: /help /model /project /status /files /diff /reset /ask /code /todo /commit /push /exec /exit")
    print("Press Ctrl+D during an iteration to stop it and return here.")

    # Mode: "agent" (with tools) or "ask" (chat only, no tools)
    mode = "agent"

    while True:
        try:
            user_text = input_with_history("\n" + colorize("You: ", "user"), history_store).strip()

            if not user_text:
                continue

            natural_command = resolve_shai_command(user_text, mode)
            if natural_command:
                print("[Shai] Interpreted as {}.".format(natural_command))
                user_text = natural_command
            user_text = normalize_command(user_text)

            if user_text == "/exit":
                print("Goodbye.")
                break

            if user_text in ("/model", "/switch", "/switch-model"):
                old_provider, old_model = PROVIDER, MODEL
                try:
                    new_provider, new_model = initialize_provider()
                except (KeyboardInterrupt, EOFError):
                    print("\n[Shai] Model switch cancelled; keeping {} / {}.".format(
                        old_provider, old_model
                    ))
                    continue
                except Exception as error:
                    print("\n[Shai] Could not switch model: {}".format(error))
                    print("[Shai] Keeping {} / {}.".format(old_provider, old_model))
                    continue

                PROVIDER, MODEL = new_provider, new_model
                LOGGER.info("Provider/model switched provider=%s model=%s",
                            PROVIDER, MODEL)
                history = history_for_new_provider(history)
                history_store.save(history, provider=PROVIDER, model=MODEL)
                print("\n[Shai] Switched to: {} / {}".format(PROVIDER, MODEL))
                print("[Shai] Text conversation context retained; old tool-call records cleared for provider compatibility.")
                continue

            if user_text == "/help":
                print(HELP_TEXT)
                continue

            if user_text == "/status":
                try:
                    print(git_status())
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text == "/files":
                print(list_files())
                continue

            if user_text == "/diff":
                try:
                    print(git_diff())
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text == "/reset":
                LOGGER.info("Conversation history reset")
                history = [{
                    "role": "system",
                    "content": SYSTEM_PROMPT
                }]
                history_store.clear()
                # Clear terminal
                os.system("cls" if os.name == "nt" else "clear")
                print("Conversation history cleared.")
                continue

            if user_text.startswith("/project "):
                new_path = user_text[len("/project "):].strip()
                if not new_path:
                    print("[Shai] Usage: /project <path>")
                    continue
                try:
                    new_root = pathlib.Path(new_path).expanduser().resolve()
                    if not new_root.is_dir():
                        print("[Shai] Error: '{}' is not a directory.".format(new_path))
                        continue
                except Exception as error:
                    print("[Shai] Error: Invalid path '{}': {}".format(new_path, error))
                    continue
                ROOT = new_root
                configure_tools_root(ROOT)
                history_store = ConversationHistory(ROOT, "agent", SYSTEM_PROMPT)
                history = history_store.load()
                history_store.save(history, provider=PROVIDER, model=MODEL)
                print("[Shai] Project switched to: {}".format(ROOT))
                continue

            if user_text == "/ask":
                if mode == "ask":
                    print("[Shai] Already in ask mode.")
                else:
                    mode = "ask"
                    print("[Shai] Switched to ask mode (chat only, no tools).")
                continue

            if user_text in ("/read-only", "/ro"):
                if mode == "read-only":
                    print("[Shai] Already in read-only mode.")
                else:
                    mode = "read-only"
                    print("[Shai] Switched to read-only mode (chat with read-only tools).")
                continue

            if user_text == "/code":
                if mode == "agent":
                    print("[Shai] Already in code mode.")
                else:
                    mode = "agent"
                    print("[Shai] Switched to code mode (agent with tools).")
                continue

            if user_text in ("/todo", "/task"):
                if not TASK_PLAN:
                    print("[Shai] No tasks in the current plan.")
                else:
                    print("Task plan:")
                    for item in TASK_PLAN:
                        status_char = {"pending": " ", "in_progress": "~", "completed": "x", "blocked": "!"}[item["status"]]
                        print("  [{}] {}".format(status_char, item["title"]))
                continue

            if user_text == "/commit-push":
                try:
                    print(git_commit_push())
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text == "/push":
                try:
                    print(git_push())
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text.startswith("/commit"):
                try:
                    parts = user_text.split()
                    message_only = "--message-only" in parts
                    print(git_commit(message_only=message_only))
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text.startswith(("/exec ", "/os-command ")) or user_text in ("/exec", "/os-command"):
                if mode != "agent":
                    print("[Shai] os_command is available only in code mode. Switch with /code first.")
                    continue
                command_text = user_text.split(None, 1)[1] if len(user_text.split(None, 1)) > 1 else ""
                try:
                    executable, arguments = parse_os_command_line(command_text)
                    result = os_command(executable, arguments)
                    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
                except IterationInterrupted:
                    print("\n[Shai] OS command interrupted.")
                except OSCommandRejected as error:
                    print(colorize("[Shai] {}".format(error), "error"))
                except Exception as error:
                    LOGGER.exception("Manual os_command failed")
                    print(colorize("[Shai] OS command failed: {}".format(error), "error"))
                continue

            try:
                if mode == "ask":
                    # Chat mode: simple request without tools
                    history.append({"role": "user", "content": user_text})
                    safeguards = load_config().get("agent_safeguards", {})
                    if safeguards.get("enabled", True) is not False:
                        def history_limit(key, default):
                            value = safeguards.get(key, default)
                            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                                return default
                            return value
                        history = compact_conversation_history(
                            history,
                            max_chars=history_limit("max_history_chars", 90000),
                            max_tool_result_chars=history_limit("max_tool_result_chars", 8000),
                            keep_recent_turns=history_limit("keep_recent_turns", 4),
                            max_recent_tool_cycles=history_limit("max_recent_tool_cycles", 6)
                        )
                    history_store.save(history, provider=PROVIDER, model=MODEL)
                    context_limit = get_context_window(PROVIDER, MODEL, load_config())
                    estimated_prompt_tokens = estimate_messages_tokens(history)
                    context_status = format_context_status(
                        estimated_prompt_tokens, context_limit
                    )
                    LOGGER.debug(
                        "Ask mode context estimate provider=%s model=%s estimated_prompt_tokens=%d "
                        "context_limit=%s history_messages=%d",
                        PROVIDER, MODEL, estimated_prompt_tokens, context_limit, len(history)
                    )
                    print("\n" + colorize("[Shai]", "system") + " {}".format(context_status))
                    spinner = Spinner("Shai is thinking | " + context_status)
                    spinner.start()
                    LOGGER.info("Ask mode turn started provider=%s model=%s user_chars=%d history_messages=%d",
                                PROVIDER, MODEL, len(user_text), len(history))
                    try:
                        response = api_request(history)
                        log_and_display_usage(
                            LOGGER, response, get_provider_config(PROVIDER)["format"],
                            estimated_prompt_tokens, context_limit
                        )
                        display_reasoning_summary(PROVIDER, response)
                        answer = response_text(response, PROVIDER)
                        LOGGER.debug("Ask mode response parsed answer_chars=%d", len(answer or ""))
                    except Exception as error:
                        LOGGER.exception("Ask mode turn failed provider=%s model=%s", PROVIDER, MODEL)
                        answer = None
                        print("\n" + colorize("Error:", "error"), error)
                    finally:
                        spinner.stop()

                    if answer:
                        LOGGER.info("Ask mode turn completed answer_chars=%d", len(answer))
                        print("\n" + answer)
                        history.append({"role": "assistant", "content": answer})
                        history_store.save(history, provider=PROVIDER, model=MODEL)

                elif mode == "read-only":
                    # Read-only mode: chat with read-only tools
                    history.append({"role": "user", "content": user_text})
                    safeguards = load_config().get("agent_safeguards", {})
                    if safeguards.get("enabled", True) is not False:
                        def history_limit(key, default):
                            value = safeguards.get(key, default)
                            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                                return default
                            return value
                        history = compact_conversation_history(
                            history,
                            max_chars=history_limit("max_history_chars", 90000),
                            max_tool_result_chars=history_limit("max_tool_result_chars", 8000),
                            keep_recent_turns=history_limit("keep_recent_turns", 4),
                            max_recent_tool_cycles=history_limit("max_recent_tool_cycles", 6)
                        )
                    history_store.save(history, provider=PROVIDER, model=MODEL)
                    context_limit = get_context_window(PROVIDER, MODEL, load_config())
                    estimated_prompt_tokens = estimate_messages_tokens(history)
                    context_status = format_context_status(
                        estimated_prompt_tokens, context_limit
                    )
                    LOGGER.debug(
                        "Read-only mode context estimate provider=%s model=%s estimated_prompt_tokens=%d "
                        "context_limit=%s history_messages=%d",
                        PROVIDER, MODEL, estimated_prompt_tokens, context_limit, len(history)
                    )
                    print("\n" + colorize("[Shai]", "system") + " {}".format(context_status))
                    spinner = Spinner("Shai is thinking | " + context_status)
                    spinner.start()
                    LOGGER.info("Read-only mode turn started provider=%s model=%s user_chars=%d history_messages=%d",
                                PROVIDER, MODEL, len(user_text), len(history))
                    try:
                        # Use api_request with read-only tools
                        response = api_request(history, PROVIDER, MODEL)
                        log_and_display_usage(
                            LOGGER, response, get_provider_config(PROVIDER)["format"],
                            estimated_prompt_tokens, context_limit
                        )
                        display_reasoning_summary(PROVIDER, response)
                        answer, tool_calls = response_text(response, PROVIDER)
                        LOGGER.debug("Read-only mode response parsed answer_chars=%d tool_calls=%d",
                                     len(answer or ""), len(tool_calls))
                    except Exception as error:
                        LOGGER.exception("Read-only mode turn failed provider=%s model=%s", PROVIDER, MODEL)
                        answer = None
                        tool_calls = []
                        print("\n" + colorize("Error:", "error"), error)
                    finally:
                        spinner.stop()

                    # Handle tool calls if any
                    if tool_calls:
                        # Add assistant message with tool calls to history
                        if get_provider_config(PROVIDER)["format"] == "anthropic":
                            content = []
                            if answer:
                                content.append({"type": "text", "text": answer})
                            for tc in tool_calls:
                                content.append({
                                    "type": "tool_use",
                                    "id": tc["id"],
                                    "name": tc["function"]["name"],
                                    "input": json.loads(tc["function"]["arguments"])
                                })
                            history.append({"role": "assistant", "content": content})
                        else:
                            message = {"role": "assistant", "content": answer or ""}
                            if tool_calls:
                                message["tool_calls"] = tool_calls
                            history.append(message)
                        history_store.save(history, provider=PROVIDER, model=MODEL)

                        # Execute tool calls
                        tool_results = []
                        for tc in tool_calls:
                            call_id = tc["id"]
                            function_data = tc["function"]
                            name = function_data.get("name")
                            raw_args = function_data.get("arguments", "{}")

                            try:
                                args = json.loads(raw_args)
                                if not isinstance(args, dict):
                                    raise ValueError("Tool arguments must be an object.")
                            except (json.JSONDecodeError, ValueError) as e:
                                result = {"error": "Invalid tool arguments: " + str(e)}
                            else:
                                if name in TOOL_DISPATCH_READ_ONLY:
                                    try:
                                        result = TOOL_DISPATCH_READ_ONLY[name](**args)
                                    except Exception as e:
                                        result = {"error": str(e)}
                                else:
                                    result = {"error": "Unknown tool: " + name}

                            # Format tool result
                            if isinstance(result, dict) and "error" in result:
                                tool_content = "Error: " + result["error"]
                            else:
                                tool_content = str(result)

                            tool_results.append({
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": tool_content
                            })

                        # Add tool results to history
                        history.extend(tool_results)
                        history_store.save(history, provider=PROVIDER, model=MODEL)

                        # Continue the conversation to get the final answer
                        spinner = Spinner("Shai is thinking | " + context_status)
                        spinner.start()
                        try:
                            response = api_request(history, PROVIDER, MODEL)
                            log_and_display_usage(
                                LOGGER, response, get_provider_config(PROVIDER)["format"],
                                estimated_prompt_tokens, context_limit
                            )
                            display_reasoning_summary(PROVIDER, response)
                            answer, tool_calls = response_text(response, PROVIDER)
                            LOGGER.debug("Read-only mode response parsed answer_chars=%d tool_calls=%d",
                                         len(answer or ""), len(tool_calls))
                        except Exception as error:
                            LOGGER.exception("Read-only mode turn failed provider=%s model=%s", PROVIDER, MODEL)
                            answer = None
                            print("\n" + colorize("Error:", "error"), error)
                        finally:
                            spinner.stop()

                    if answer:
                        LOGGER.info("Read-only mode turn completed answer_chars=%d", len(answer))
                        print("\n" + answer)
                        if get_provider_config(PROVIDER)["format"] == "anthropic":
                            history.append({
                                "role": "assistant",
                                "content": [{"type": "text", "text": answer}],
                            })
                        else:
                            history.append({"role": "assistant", "content": answer})
                        history_store.save(history, provider=PROVIDER, model=MODEL)
                    elif answer == "":
                        print("\nNo text response returned.")
                        history.pop()
                        history_store.save(history, provider=PROVIDER, model=MODEL)
                    else:
                        history.pop()
                        history_store.save(history, provider=PROVIDER, model=MODEL)

                else:
                    # Agent mode: full ask_model with tools
                    ask_model(history, user_text)
            except Exception as error:
                LOGGER.exception("Unhandled agent request error")
                print("\n" + colorize("Error:", "error"), error)
            finally:
                history_store.save(history, provider=PROVIDER, model=MODEL)

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break






if __name__ == "__main__":
    main()
