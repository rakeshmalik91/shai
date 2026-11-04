import os
import sys
import json
import ast
import re
import importlib.util
import platform
import urllib.request
import urllib.error
import pathlib
import subprocess
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

from chat import Spinner, extract_anthropic_response
from modules.provider_selection import PROVIDERS, initialize_provider
from modules.logging_config import init_default_logging, get_logger, log_api_payload
from modules.reasoning_handlers import display_reasoning_summary
from modules.conversation_history import ConversationHistory, input_with_history
from modules.file_change_display import print_change_preview
from modules.agent_safeguards import (
    bound_tool_result, compact_conversation_history, validate_tool_arguments,
    ToolArgumentFailureTracker, read_file_cache_marker, marker_in_history,
    update_no_progress, tool_call_signature, format_tool_call_for_display
)

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(
    sys.argv[1] if __name__ == "__main__" and len(sys.argv) > 1 else "."
).expanduser().resolve()

# In-memory task checklist for the current agent session/request.
TASK_PLAN = []

MAX_FILE_SIZE = 200_000
DEFAULT_MAX_TOOL_CALL_ITERATIONS = 12
CONFIG_FILE = SCRIPT_DIR / "shai_config.json"
LOGGER = get_logger("shai.agent")
# Set by main() after provider selection; defaults keep helpers import-testable.
PROVIDER = "unknown"
MODEL = ""
DEFAULT_API_TIMEOUT_SECONDS = 300


def load_api_timeout_seconds():
    """Read the provider API socket timeout from shai_config.json."""
    config = load_config()
    value = config.get("api_timeout_seconds", DEFAULT_API_TIMEOUT_SECONDS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        LOGGER.warning(
            "Invalid api_timeout_seconds=%r; using %s seconds.",
            value, DEFAULT_API_TIMEOUT_SECONDS
        )
        return DEFAULT_API_TIMEOUT_SECONDS
    return value



DEFAULT_TOOL_CALL_DISPLAY = {
    "show_parameters": True,
    "max_argument_chars": 2000
}


def load_tool_call_display_settings():
    settings = dict(DEFAULT_TOOL_CALL_DISPLAY)
    supplied = load_config().get("tool_call_display", {})
    if isinstance(supplied, dict):
        settings.update(supplied)
    settings["show_parameters"] = settings["show_parameters"] is True
    settings["max_argument_chars"] = positive_int_setting(
        settings, "max_argument_chars",
        DEFAULT_TOOL_CALL_DISPLAY["max_argument_chars"]
    )
    return settings



DEFAULT_TOOL_SAFEGUARDS = {
    "duplicate_call_detection": True,
    "duplicate_call_limit": 2,
    "repeat_call_warning": True,
    "repeat_call_warning_after": 2,
    "run_checks_timeout_seconds": 120,
    "git_status_timeout_seconds": 15,
    "git_diff_timeout_seconds": 15
}


def load_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}


API_TIMEOUT_SECONDS = load_api_timeout_seconds()


def positive_int_setting(config, key, default):
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        print(
            "Warning: invalid {} in shai_config.json; using {}.".format(
                key, default
            )
        )
        return default
    return value


def load_tool_safeguards():
    config = load_config()
    settings = dict(DEFAULT_TOOL_SAFEGUARDS)
    supplied = config.get("tool_safeguards", {})
    if isinstance(supplied, dict):
        settings.update(supplied)

    settings["duplicate_call_detection"] = (
        settings["duplicate_call_detection"] is True
    )
    settings["repeat_call_warning"] = (
        settings["repeat_call_warning"] is True
    )
    for key in (
        "duplicate_call_limit",
        "repeat_call_warning_after",
        "run_checks_timeout_seconds",
        "git_status_timeout_seconds",
        "git_diff_timeout_seconds"
    ):
        settings[key] = positive_int_setting(
            settings, key, DEFAULT_TOOL_SAFEGUARDS[key]
        )
    return settings


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


def load_agent_safeguards():
    config = load_config()
    supplied = config.get("agent_safeguards", {})
    settings = dict(DEFAULT_AGENT_SAFEGUARDS)
    if isinstance(supplied, dict):
        settings.update(supplied)
    settings["enabled"] = settings.get("enabled") is not False
    for key in (
        "max_history_chars", "max_tool_result_chars", "keep_recent_turns",
        "max_recent_tool_cycles", "max_consecutive_no_progress_iterations",
        "max_tool_calls_per_request", "max_invalid_argument_attempts_per_tool",
        "default_read_file_lines", "max_read_file_lines", "max_read_file_chars"
    ):
        value = settings.get(key, DEFAULT_AGENT_SAFEGUARDS[key])
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            value = DEFAULT_AGENT_SAFEGUARDS[key]
        settings[key] = value
    settings["deduplicate_read_file_calls"] = (
        settings.get("deduplicate_read_file_calls") is True
    )
    settings["max_invalid_argument_attempts_per_tool"] = min(
        settings["max_invalid_argument_attempts_per_tool"], 5
    )
    settings["max_read_file_lines"] = min(settings["max_read_file_lines"], 500)
    settings["default_read_file_lines"] = min(
        settings["default_read_file_lines"], settings["max_read_file_lines"]
    )
    for key in (
        "validate_tool_arguments", "reject_unknown_tool_arguments"
    ):
        settings[key] = settings.get(key) is True
    return settings


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


def load_test_execution_settings():
    """Load bounded test-runner settings from shai_config.json."""
    supplied = load_config().get("test_execution", {})
    settings = dict(DEFAULT_TEST_EXECUTION)
    if isinstance(supplied, dict):
        settings.update(supplied)
        if "timeout_seconds" not in supplied:
            settings["timeout_seconds"] = TOOL_SAFEGUARDS[
                "run_checks_timeout_seconds"
            ]

    for key in (
        "timeout_seconds", "max_stdout_bytes",
        "max_stderr_bytes", "preserve_output_head_bytes",
        "preserve_output_tail_bytes"
    ):
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            settings[key] = DEFAULT_TEST_EXECUTION[key]

    for key in ("terminate_child_processes",):
        settings[key] = settings.get(key) is True

    total = settings.get("max_output_bytes")
    if isinstance(total, bool) or not isinstance(total, int) or total < 2:
        total = DEFAULT_TEST_EXECUTION["max_output_bytes"]
    settings["max_output_bytes"] = total

    # Keep the combined stream limits within the configured total.
    stdout_limit = settings["max_stdout_bytes"]
    stderr_limit = settings["max_stderr_bytes"]
    if stdout_limit + stderr_limit > total:
        stdout_limit = min(stdout_limit, max(1, total - 1))
        stderr_limit = min(stderr_limit, total - stdout_limit)
        settings["max_stdout_bytes"] = stdout_limit
        settings["max_stderr_bytes"] = max(1, stderr_limit)
    else:
        settings["max_stdout_bytes"] = stdout_limit
        settings["max_stderr_bytes"] = stderr_limit
    return settings


TEST_EXECUTION = load_test_execution_settings()


class BoundedOutputCapture:
    """Capture small output completely; for large output retain head and tail."""

    def __init__(self, max_bytes, head_bytes, tail_bytes):
        self.max_bytes = max(1, int(max_bytes))
        self.head_bytes = min(max(0, int(head_bytes)), self.max_bytes)
        self.tail_bytes = min(
            max(0, int(tail_bytes)), self.max_bytes - self.head_bytes
        )
        self.total_bytes = 0
        self._complete = bytearray()
        self._head = None
        self._tail = bytearray()

    def feed(self, chunk):
        if not chunk:
            return
        self.total_bytes += len(chunk)

        if self._head is None:
            if len(self._complete) + len(chunk) <= self.max_bytes:
                self._complete.extend(chunk)
                return

            combined = bytes(self._complete) + chunk
            self._head = combined[:self.head_bytes]
            if self.tail_bytes:
                self._tail.extend(combined[-self.tail_bytes:])
            self._complete = bytearray()
            return

        if self.tail_bytes:
            self._tail.extend(chunk)
            if len(self._tail) > self.tail_bytes:
                del self._tail[:-self.tail_bytes]

    @property
    def truncated(self):
        return self.total_bytes > self.max_bytes

    def get_bytes(self):
        if self._head is None:
            return bytes(self._complete)
        return bytes(self._head) + bytes(self._tail)

    def get_text(self):
        value = self.get_bytes().decode("utf-8", errors="replace")
        if self.truncated:
            value += (
                "\n... OUTPUT TRUNCATED: captured beginning and end only; "
                "original stream was {} bytes ...\n"
            ).format(self.total_bytes)
        return value


def _terminate_process_tree(process, process_group):
    """Terminate the test process and, when possible, its child processes."""
    if process.poll() is not None and not process_group:
        return

    if process_group:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        # Kill any descendants that ignored SIGTERM, even if the group leader
        # (the test runner itself) has already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    else:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_bounded_interruptible_command(
    command, cwd, timeout, output_settings
):
    """Run a command with bounded stdout/stderr, timeout and Ctrl+D support."""
    terminate_children = output_settings["terminate_child_processes"]
    process_group = bool(terminate_children and hasattr(os, "killpg"))
    popen_options = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0
    }
    if process_group:
        popen_options["start_new_session"] = True

    process = subprocess.Popen(command, **popen_options)
    stdout_capture = BoundedOutputCapture(
        output_settings["max_stdout_bytes"],
        output_settings["preserve_output_head_bytes"],
        output_settings["preserve_output_tail_bytes"]
    )
    stderr_capture = BoundedOutputCapture(
        output_settings["max_stderr_bytes"],
        output_settings["preserve_output_head_bytes"],
        output_settings["preserve_output_tail_bytes"]
    )

    def drain(stream, capture):
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                capture.feed(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    readers = [
        threading.Thread(
            target=drain, args=(process.stdout, stdout_capture),
            name="shai-test-stdout", daemon=True
        ),
        threading.Thread(
            target=drain, args=(process.stderr, stderr_capture),
            name="shai-test-stderr", daemon=True
        )
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    timed_out = False
    interrupted = False

    try:
        with CtrlDMonitor() as monitor:
            while process.poll() is None:
                if monitor.cancelled.is_set():
                    interrupted = True
                    _terminate_process_tree(process, process_group)
                    break

                if timeout and time.monotonic() - started >= timeout:
                    timed_out = True
                    _terminate_process_tree(process, process_group)
                    break
                time.sleep(0.05)
    except BaseException:
        _terminate_process_tree(process, process_group)
        raise

    if interrupted:
        for reader in readers:
            reader.join(timeout=1.0)
        raise IterationInterrupted()

    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process, process_group)

    for reader in readers:
        reader.join(timeout=2.0)

    # If descendants inherited the pipes, do not wait forever for their EOF.
    readers_still_open = any(reader.is_alive() for reader in readers)
    if readers_still_open and process_group:
        _terminate_process_tree(process, process_group)
        for reader in readers:
            reader.join(timeout=1.0)

    stdout_text = stdout_capture.get_text()
    stderr_text = stderr_capture.get_text()
    truncated = stdout_capture.truncated or stderr_capture.truncated

    return {
        "timed_out": timed_out,
        "interrupted": interrupted,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "returncode": process.returncode,
        "output_truncated": truncated,
        "stdout_total_bytes": stdout_capture.total_bytes,
        "stderr_total_bytes": stderr_capture.total_bytes,
        "duration_seconds": round(time.monotonic() - started, 2)
    }


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


def load_retry_settings():
    config = load_config()
    supplied = config.get("retry", {})
    settings = dict(DEFAULT_RETRY_SETTINGS)
    if isinstance(supplied, dict):
        settings.update(supplied)

    for key in ("enabled", "jitter", "retry_network_errors",
                "retry_invalid_json", "invalid_tool_selection_enabled"):
        settings[key] = settings[key] is True

    max_retries = settings.get("max_retries", DEFAULT_RETRY_SETTINGS["max_retries"])
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
        max_retries = DEFAULT_RETRY_SETTINGS["max_retries"]
    settings["max_retries"] = max_retries
    settings["invalid_tool_selection_max_attempts"] = positive_int_setting(
        settings, "invalid_tool_selection_max_attempts",
        DEFAULT_RETRY_SETTINGS["invalid_tool_selection_max_attempts"]
    )

    for key in ("initial_backoff_seconds", "backoff_multiplier",
                "max_backoff_seconds", "jitter_fraction"):
        value = settings.get(key, DEFAULT_RETRY_SETTINGS[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            value = DEFAULT_RETRY_SETTINGS[key]
        value = float(value)
        if not math.isfinite(value) or value < 0:
            value = DEFAULT_RETRY_SETTINGS[key]
        settings[key] = value

    if settings["backoff_multiplier"] < 1:
        settings["backoff_multiplier"] = 1.0
    if settings["jitter_fraction"] > 1:
        settings["jitter_fraction"] = 1.0

    statuses = settings.get("retry_http_statuses")
    if not isinstance(statuses, list):
        statuses = DEFAULT_RETRY_SETTINGS["retry_http_statuses"]
    # HTTP 400 is deliberately never retried. A malformed tool-call request
    # cannot be repaired by sending the same payload again.
    settings["retry_http_statuses"] = [
        item for item in statuses
        if isinstance(item, int) and not isinstance(item, bool)
        and 100 <= item <= 599 and item != 400
    ]

    recovery_defaults = DEFAULT_RETRY_SETTINGS["invalid_tool_call_recovery"]
    supplied_recovery = settings.get("invalid_tool_call_recovery", {})
    recovery = dict(recovery_defaults)
    if isinstance(supplied_recovery, dict):
        recovery.update(supplied_recovery)
    recovery["enabled"] = recovery.get("enabled") is True
    recovery["repair_conversation_history"] = (
        recovery.get("repair_conversation_history") is True
    )
    recovery["preserve_user_request"] = (
        recovery.get("preserve_user_request") is not False
    )
    recovery["stop_on_repeated_failure"] = (
        recovery.get("stop_on_repeated_failure") is not False
    )
    recovery["max_recovery_attempts"] = positive_int_setting(
        recovery, "max_recovery_attempts",
        recovery_defaults["max_recovery_attempts"]
    )
    recovery["max_tool_result_chars"] = positive_int_setting(
        recovery, "max_tool_result_chars",
        recovery_defaults["max_tool_result_chars"]
    )
    settings["invalid_tool_call_recovery"] = recovery
    return settings


RETRY_SETTINGS = load_retry_settings()


def load_max_tool_call_iterations():
    """Read the agent iteration limit from shai_config.json."""
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        value = config.get(
            "max_tool_call_iterations",
            DEFAULT_MAX_TOOL_CALL_ITERATIONS
        ) if isinstance(config, dict) else DEFAULT_MAX_TOOL_CALL_ITERATIONS
        # bool is a subclass of int, so reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError
        return value
    except FileNotFoundError:
        return DEFAULT_MAX_TOOL_CALL_ITERATIONS
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print(
            "Warning: invalid max_tool_call_iterations in shai_config.json; "
            "using default {}.".format(DEFAULT_MAX_TOOL_CALL_ITERATIONS)
        )
        return DEFAULT_MAX_TOOL_CALL_ITERATIONS


MAX_ITERATIONS = load_max_tool_call_iterations()

def load_yes_to_all():
    """Restore the persisted approval preference from shai_config.json."""
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        return config.get("yes_to_all", False) is True if isinstance(config, dict) else False
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return False


def save_yes_to_all(enabled):
    """Persist yes_to_all without disturbing other Shai configuration."""
    try:
        config = {}
        if CONFIG_FILE.exists():
            with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                existing = json.load(handle)
            if isinstance(existing, dict):
                config = existing

        config["yes_to_all"] = bool(enabled)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
        with temporary_file.open("w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        temporary_file.replace(CONFIG_FILE)
    except (OSError, ValueError, TypeError) as error:
        print("Warning: could not save yes_to_all setting: {}".format(error))


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

def safe_path(value):
    path = (ROOT / value).resolve()

    try:
        path.relative_to(ROOT)
    except ValueError:
        raise ValueError("Path is outside the project directory.")

    if ".git" in path.parts:
        raise ValueError("Access to .git is not allowed.")

    if path.name.startswith(".env"):
        raise ValueError("Access to environment files is not allowed.")

    if path.name in ("credentials.json", "secrets.json"):
        raise ValueError("Access to sensitive files is not allowed.")

    return path

def relative_path(path):
    return str(path.relative_to(ROOT))


# -------------------------------------------------------------------
# Ctrl+D iteration interrupt
# -------------------------------------------------------------------

class IterationInterrupted(Exception):
    """Raised when the user presses Ctrl+D during an agent iteration."""


class CtrlDMonitor:
    """Watch for Ctrl+D (or Ctrl+C) while Shai is processing.

    Other keystrokes are ignored while this monitor is active.
    """

    def __init__(self):
        self.fd = None
        self.original_settings = None
        self.stop_event = threading.Event()
        self.cancelled = threading.Event()
        self.thread = None
        self.enabled = False

    def __enter__(self):
        try:
            if not sys.stdin.isatty():
                return self
            self.fd = sys.stdin.fileno()
            self.original_settings = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            self.enabled = True
            self.thread = threading.Thread(
                target=self._watch, name="shai-ctrl-d", daemon=True
            )
            self.thread.start()
        except (AttributeError, OSError, termios.error):
            self._restore()
        return self

    def _watch(self):
        while not self.stop_event.is_set():
            try:
                readable, _, _ = select.select([self.fd], [], [], 0.1)
                if not readable:
                    continue
                value = os.read(self.fd, 1)
                if value in (b"\x04", b"\x03"):
                    self.cancelled.set()
                    return
            except (OSError, ValueError):
                return

    def _restore(self):
        if self.enabled and self.original_settings is not None:
            try:
                termios.tcsetattr(
                    self.fd, termios.TCSADRAIN, self.original_settings
                )
            except (OSError, termios.error):
                pass
        self.enabled = False

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.3)
        self._restore()
        return False


def run_interruptible_call(function, *args):
    """Run a blocking API call in a daemon thread so Ctrl+D stays responsive."""
    result = {}

    def target():
        try:
            result["value"] = function(*args)
        except BaseException as error:
            result["error"] = error

    with CtrlDMonitor() as monitor:
        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(timeout=0.1)
            if monitor.cancelled.is_set():
                raise IterationInterrupted()

    if "error" in result:
        raise result["error"]
    return result.get("value")


def run_interruptible_command(command, cwd, timeout):
    """Run a command with a timeout and terminate it on Ctrl+D."""
    process = subprocess.Popen(
        command, cwd=str(cwd), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True
    )
    started = time.monotonic()
    try:
        with CtrlDMonitor() as monitor:
            while True:
                if monitor.cancelled.is_set():
                    if process.poll() is None:
                        process.kill()
                    process.communicate()
                    raise IterationInterrupted()

                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    process.kill()
                    stdout, stderr = process.communicate()
                    return {
                        "timed_out": True, "stdout": stdout,
                        "stderr": stderr, "returncode": process.returncode
                    }
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(0.2, remaining)
                    )
                    return {
                        "timed_out": False, "stdout": stdout,
                        "stderr": stderr, "returncode": process.returncode
                    }
                except subprocess.TimeoutExpired:
                    continue
    except IterationInterrupted:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise


# -------------------------------------------------------------------
# Project tools
# -------------------------------------------------------------------

def list_files(path="."):
    LOGGER.debug("list_files requested path=%s", path)
    directory = safe_path(path)

    if not directory.is_dir():
        return "Not a directory."

    results = []

    ignored = {
        ".git", "node_modules", "__pycache__",
        ".venv", "venv", ".idea"
    }

    for item in sorted(directory.rglob("*")):
        if any(part in ignored for part in item.parts):
            continue

        if item.is_file():
            try:
                item.resolve().relative_to(ROOT)
            except ValueError:
                continue

            results.append(relative_path(item))

        if len(results) >= 300:
            results.append("...file listing truncated...")
            break

    LOGGER.debug("list_files complete path=%s files=%d", path, len(results))
    return "\n".join(results) if results else "No files found."

def read_file(path, offset=0, limit=None):
    """Read a bounded, line-addressable UTF-8 text chunk from a project file.

    offset is a zero-based line offset. limit is the maximum number of lines.
    Large files can be read sequentially using the returned next_offset.
    """
    LOGGER.debug("read_file requested path=%s offset=%s limit=%s", path, offset, limit)
    file_path = safe_path(path)

    if not file_path.is_file():
        return "File does not exist."

    if file_path.stat().st_size > MAX_FILE_SIZE:
        return "File is too large to read (maximum {} bytes).".format(MAX_FILE_SIZE)

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return "Invalid offset: use a non-negative, zero-based line number."
    if limit is None:
        limit = AGENT_SAFEGUARDS["default_read_file_lines"]
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return "Invalid limit: use a positive number of lines."
    maximum_lines = AGENT_SAFEGUARDS["max_read_file_lines"]
    if limit > maximum_lines:
        return "Requested limit {} exceeds the configured maximum of {} lines.".format(
            limit, maximum_lines
        )

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "File is not UTF-8 text."
    except OSError as error:
        return "Unable to read file: {}".format(error)

    lines = content.splitlines(True)
    total_lines = len(lines)
    if offset > total_lines:
        return "Offset {} is past the end of the file ({} lines).".format(
            offset, total_lines
        )
    if offset == total_lines and total_lines > 0:
        return "Offset {} is at the end of the file ({} lines).".format(
            offset, total_lines
        )

    max_chars = AGENT_SAFEGUARDS["max_read_file_chars"]
    selected = []
    used_chars = 0
    next_offset = offset
    line_was_truncated = False
    for line_index in range(offset, min(total_lines, offset + limit)):
        line = lines[line_index]
        remaining = max_chars - used_chars
        if len(line) > remaining:
            if not selected and remaining > 0:
                selected.append(line[:remaining])
                used_chars += remaining
                next_offset = line_index + 1
                line_was_truncated = True
            break
        selected.append(line)
        used_chars += len(line)
        next_offset = line_index + 1

    relative = relative_path(file_path)
    marker = read_file_cache_marker(relative, offset, limit)
    if selected:
        first_line = offset + 1
        last_line = offset + len(selected)
        header = (
            "{}\n[Lines {}-{} of {}. Returned {} line(s).]\n"
        ).format(marker, first_line, last_line, total_lines, len(selected))
        result = header + "".join(selected)
    else:
        result = "{}\n[The file is empty.]".format(marker)

    has_more = next_offset < total_lines
    if has_more:
        result += (
            "\n\n[More content available. Continue with read_file(path={!r}, "
            "offset={}, limit={}).]"
        ).format(relative, next_offset, limit)
    if line_was_truncated:
        result += (
            "\n[One unusually long line was truncated at the configured "
            "{}-character chunk limit.]"
        ).format(max_chars)
    LOGGER.debug(
        "read_file complete path=%s offset=%d next_offset=%d total_lines=%d "
        "returned_lines=%d chars=%d more=%s",
        relative, offset, next_offset, total_lines, len(selected),
        len(result), has_more
    )
    return result

def search_files(query, path="."):
    """Search either one project file or all files beneath a directory."""
    LOGGER.debug("search_files query=%r path=%s", query, path)
    target = safe_path(path)
    if not target.exists():
        return {"error": "Search path does not exist: {}. Use a project-relative file or directory path.".format(path)}
    if not target.is_file() and not target.is_dir():
        return {"error": "Search path is neither a regular file nor a directory: {}.".format(path)}

    ignored = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea"}
    files = [target] if target.is_file() else target.rglob("*")
    results = []
    scanned = 0
    for file_path in files:
        if not file_path.is_file():
            continue
        if any(part in ignored for part in file_path.parts) or file_path.name.startswith(".env"):
            continue
        try:
            file_path.resolve().relative_to(ROOT)
            if file_path.stat().st_size > MAX_FILE_SIZE:
                continue
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        scanned += 1
        for number, line in enumerate(content.splitlines(), 1):
            if query.lower() in line.lower():
                results.append("{}:{}: {}".format(
                    relative_path(file_path), number, line.strip()[:250]
                ))
                if len(results) >= 100:
                    results.append("...search results truncated...")
                    LOGGER.debug("search_files truncated query=%r path=%s scanned=%d", query, path, scanned)
                    return "\n".join(results)
    LOGGER.debug("search_files complete query=%r path=%s scanned=%d matches=%d", query, path, scanned, len(results))
    return "\n".join(results) if results else "No matches found in {} ({} file(s) scanned).".format(path, scanned)


# -------------------------------------------------------------------
# Code intelligence and project understanding
# -------------------------------------------------------------------

IGNORED_PROJECT_DIRS = {
    ".git", ".shai_backups", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".idea"
}


def _project_files(path=".", suffixes=None, max_files=2000):
    """Yield safe project files beneath a file or directory."""
    target = safe_path(path)
    if not target.exists():
        raise ValueError("Path does not exist: {}".format(path))
    if target.is_file():
        candidates = [target]
    elif target.is_dir():
        candidates = target.rglob("*")
    else:
        raise ValueError("Path is not a regular file or directory: {}".format(path))

    yielded = 0
    for candidate in candidates:
        if not candidate.is_file():
            continue
        if any(part in IGNORED_PROJECT_DIRS for part in candidate.parts):
            continue
        if candidate.name.startswith(".env"):
            continue
        try:
            candidate.resolve().relative_to(ROOT)
        except ValueError:
            continue
        if suffixes and candidate.suffix.lower() not in suffixes:
            continue
        yield candidate
        yielded += 1
        if yielded >= max_files:
            break


def _python_ast(path):
    file_path = safe_path(path)
    if not file_path.is_file() or file_path.suffix.lower() != ".py":
        raise ValueError("Expected a Python source file.")
    if file_path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError("Python file exceeds the configured size limit.")
    content = file_path.read_text(encoding="utf-8")
    try:
        return content, ast.parse(content, filename=str(file_path))
    except SyntaxError as exc:
        return content, exc


def get_file_outline(path):
    """Return imports, classes, functions and line ranges for a Python file."""
    content, tree = _python_ast(path)
    if isinstance(tree, SyntaxError):
        return "Syntax error at line {}: {}".format(tree.lineno, tree.msg)
    rows = ["File outline: {}".format(path)]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            rows.append("{} {} (lines {}-{})".format(
                kind, node.name, node.lineno,
                getattr(node, "end_lineno", node.lineno)
            ))
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        rows.append("  method {} (lines {}-{})".format(
                            child.name, child.lineno,
                            getattr(child, "end_lineno", child.lineno)
                        ))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            try:
                text = ast.get_source_segment(content, node) or ""
            except Exception:
                text = ""
            rows.append("import {}".format(text.strip()))
    return "\n".join(rows) if len(rows) > 1 else "No top-level declarations found."


def find_symbol(symbol, path="."):
    """Find Python class/function/method/assignment definitions by name."""
    if not isinstance(symbol, str) or not symbol.strip():
        return "symbol must be a non-empty string."
    matches = []
    for file_path in _project_files(path, suffixes={".py"}):
        try:
            _, tree = _python_ast(relative_path(file_path))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(tree, SyntaxError):
            continue
        for node in ast.walk(tree):
            name = None
            kind = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name, kind = node.name, "function"
            elif isinstance(node, ast.ClassDef):
                name, kind = node.name, "class"
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == symbol:
                        name, kind = target.id, "assignment"
                        break
            if name == symbol:
                matches.append("{}:{}: {} {}".format(
                    relative_path(file_path), getattr(node, "lineno", 1),
                    kind, name
                ))
    return "\n".join(matches) if matches else "No Python definition found for {!r}.".format(symbol)


def find_references(symbol, path="."):
    """Find textual references to a symbol in project text files."""
    if not isinstance(symbol, str) or not symbol.strip():
        return "symbol must be a non-empty string."
    return search_files(symbol, path)


def get_project_map(path="."):
    """Summarize project directories, file types and common entry points."""
    target = safe_path(path)
    if not target.is_dir():
        return "Project map path must be a directory."
    files = list(_project_files(path, max_files=2000))
    dirs = set()
    extensions = {}
    entrypoints = []
    for file_path in files:
        rel = pathlib.Path(relative_path(file_path))
        for parent in rel.parents:
            if str(parent) != ".":
                dirs.add(str(parent))
        ext = file_path.suffix.lower() or "[no extension]"
        extensions[ext] = extensions.get(ext, 0) + 1
        if file_path.name in ("main.py", "agent.py", "chat.py", "__main__.py", "app.py"):
            entrypoints.append(str(rel))
    rows = [
        "Project map: {}".format(path),
        "Files scanned: {}".format(len(files)),
        "Directories: {}".format(", ".join(sorted(dirs)[:80]) or "(root only)"),
        "File types: {}".format(", ".join(
            "{} {}".format(ext, count)
            for ext, count in sorted(extensions.items(), key=lambda item: (-item[1], item[0]))
        ) or "(none)"),
        "Likely entry points: {}".format(", ".join(sorted(entrypoints)) or "(not detected)")
    ]
    return "\n".join(rows)


def get_test_map(path="."):
    """Find test files and heuristically map them to source modules."""
    target = safe_path(path)
    if not target.is_dir():
        return "Test map path must be a directory."
    files = list(_project_files(path, max_files=2000))
    test_files = [
        f for f in files
        if f.name.startswith("test_") or f.name.endswith("_test.py")
        or "tests" in f.parts or "test" in f.parts
    ]
    source_files = [
        f for f in files if f.suffix.lower() == ".py" and f not in test_files
    ]
    rows = ["Test files found: {}".format(len(test_files))]
    for test_file in sorted(test_files)[:150]:
        stem = test_file.stem
        candidate = stem[5:] if stem.startswith("test_") else stem[:-5] if stem.endswith("_test") else ""
        matches = [
            relative_path(source) for source in source_files
            if candidate and (
                source.stem == candidate or
                candidate in source.stem or source.stem in candidate
            )
        ]
        rows.append("{} -> {}".format(
            relative_path(test_file), ", ".join(matches[:5]) or "source mapping not obvious"
        ))
    return "\n".join(rows)


def inspect_test_failure(output):
    """Extract likely failing tests, assertions, errors and traceback locations."""
    if not isinstance(output, str):
        return "output must be a string containing test output."
    lines = output.splitlines()
    patterns = (
        re.compile(r"^(FAIL|ERROR):\s+(.+)$"),
        re.compile(r"^FAILED\s+(.+)$"),
        re.compile(r"^E\s+(.+)$"),
        re.compile(r"^(.+\.py:\d+):\s*(.+)$"),
        re.compile(r"^AssertionError:.*$"),
        re.compile(r"^(?:ImportError|ModuleNotFoundError|SyntaxError|TypeError|ValueError):.*$")
    )
    selected = []
    for index, line in enumerate(lines):
        if any(pattern.search(line.strip()) for pattern in patterns):
            start = max(0, index - 2)
            end = min(len(lines), index + 4)
            for context_line in lines[start:end]:
                if context_line not in selected:
                    selected.append(context_line)
    if not selected:
        return "No common test failure markers found. Provide the complete test output or traceback."
    return "Potential test failure details:\n" + "\n".join(selected[:100])


def verify_changes(path=".", test_check="unittest"):
    """Run Python compilation and a selected test suite with bounded execution."""
    directory = safe_path(path)
    if not directory.is_dir():
        return "Verification path must be a directory."
    if test_check not in ("unittest", "pytest", "none"):
        return "test_check must be unittest, pytest, or none."
    commands = [
        ("compileall", ["python3", "-m", "compileall", "-q", "."])
    ]
    if test_check == "unittest":
        commands.append(("unittest", [
            "python3", "-m", "unittest", "discover", "-s", "test", "-p", "test_*.py"
        ]))
    elif test_check == "pytest":
        commands.append(("pytest", ["python3", "-m", "pytest", "-q"]))
    if not confirm_action(
        "Run verification ({} ) in {}? [y/N/a=all]: ".format(
            ", ".join(name for name, _ in commands), path
        )
    ):
        return "User declined verification."
    reports = []
    for name, command in commands:
        result = run_bounded_interruptible_command(
            command, directory, TEST_EXECUTION["timeout_seconds"], TEST_EXECUTION
        )
        status = (
            "TIMEOUT" if result["timed_out"] else
            "PASS" if result["returncode"] == 0 else
            "FAIL (exit {})".format(result["returncode"])
        )
        reports.append("{}: {}\n{}".format(
            name, status, result["stdout"] + result["stderr"]
        ))
        if result["timed_out"] or result["returncode"] != 0:
            break
    return "\n\n".join(reports)


def update_task_plan(steps):
    """Set the visible in-memory task checklist for the current agent task."""
    global TASK_PLAN
    if not isinstance(steps, list) or not steps:
        return "steps must be a non-empty array of task objects."
    updated = []
    for item in steps:
        if not isinstance(item, dict):
            return "Each step must be an object with title and status."
        title = item.get("title")
        status = item.get("status", "pending")
        if not isinstance(title, str) or not title.strip():
            return "Each task step needs a non-empty title."
        if status not in ("pending", "in_progress", "completed", "blocked"):
            return "Invalid task status for {!r}.".format(title)
        updated.append({"title": title.strip(), "status": status})
    TASK_PLAN = updated
    return "Task plan updated:\n" + "\n".join(
        "[{}] {}".format(
            {"pending": " ", "in_progress": "~", "completed": "x", "blocked": "!"}[item["status"]],
            item["title"]
        ) for item in TASK_PLAN
    )

def git_status():
    LOGGER.debug("Running git status cwd=%s timeout=%ss",
                 ROOT, TOOL_SAFEGUARDS["git_status_timeout_seconds"])
    result = run_interruptible_command(
        ["git", "status", "--short", "--branch"], ROOT,
        TOOL_SAFEGUARDS["git_status_timeout_seconds"]
    )
    output = result["stdout"] + result["stderr"]
    if result["timed_out"]:
        return "Git status timed out after {} seconds.\n{}".format(
            TOOL_SAFEGUARDS["git_status_timeout_seconds"], output
        )
    return output


def git_diff():
    LOGGER.debug("Running git diff cwd=%s timeout=%ss",
                 ROOT, TOOL_SAFEGUARDS["git_diff_timeout_seconds"])
    result = run_interruptible_command(
        ["git", "diff", "--"], ROOT,
        TOOL_SAFEGUARDS["git_diff_timeout_seconds"]
    )
    output = result["stdout"] + result["stderr"]
    if result["timed_out"]:
        return "Git diff timed out after {} seconds.\n{}".format(
            TOOL_SAFEGUARDS["git_diff_timeout_seconds"], output
        )
    return output

# -------------------------------------------------------------------
# File editing
# -------------------------------------------------------------------

def confirm_action(prompt):
    """Ask user for confirmation with yes/no/all options."""
    global YES_TO_ALL
    if YES_TO_ALL:
        return True
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        raise IterationInterrupted()
    if answer in ("a", "all"):
        YES_TO_ALL = True
        save_yes_to_all(True)
        return True
    return answer in ("y", "yes")


def _explicit_file_approval(prompt):
    """Risk approvals must not be bypassed by the session-wide yes-to-all flag."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        raise IterationInterrupted()
    return answer in ("y", "yes")


def _validate_file_content(file_path, content):
    """Run lightweight format validation before replacing a file."""
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".py":
            compile(content, str(file_path), "exec")
        elif suffix == ".json":
            json.loads(content)
        elif suffix in (".yaml", ".yml"):
            # No YAML dependency in iSH; basic non-empty check only.
            if content and not content.endswith("\n"):
                return "YAML text must end with a newline."
    except (SyntaxError, ValueError, TypeError) as exc:
        return "Validation failed; original file was preserved: {}".format(exc)
    return None


def _atomic_replace_file(file_path, content):
    """Back up the original, write/validate a temp file, then atomically replace."""
    import tempfile
    import shutil
    file_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if file_path.exists():
        backup_dir = ROOT / ".shai_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_dir / (file_path.name + "." + stamp + ".bak")
        shutil.copy2(str(file_path), str(backup_path))

    fd, temp_name = tempfile.mkstemp(
        prefix=".shai-tmp-", suffix=file_path.suffix,
        dir=str(file_path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path = pathlib.Path(temp_name)
        validation_error = _validate_file_content(file_path, temp_path.read_text(encoding="utf-8"))
        if validation_error:
            temp_path.unlink()
            if backup_path and backup_path.exists():
                backup_path.unlink()
            raise ValueError(validation_error)
        os.replace(str(temp_path), str(file_path))
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return backup_path


def _file_change_risk(old_content, new_content):
    old_bytes = len(old_content.encode("utf-8"))
    new_bytes = len(new_content.encode("utf-8"))
    old_lines = len(old_content.splitlines())
    new_lines = len(new_content.splitlines())
    removed_lines = max(0, old_lines - new_lines)
    deletion_ratio = (removed_lines / float(max(old_lines, 1)))
    size_reduction = ((old_bytes - new_bytes) / float(max(old_bytes, 1)))
    return {
        "old_bytes": old_bytes, "new_bytes": new_bytes,
        "old_lines": old_lines, "new_lines": new_lines,
        "removed_lines": removed_lines,
        "deletion_ratio": deletion_ratio,
        "size_reduction": size_reduction
    }


def patch_file(path, old_text, new_text):
    """Replace one exact section, refusing ambiguous or missing matches."""
    file_path = safe_path(path)
    if not file_path.exists() or not file_path.is_file():
        return "Target file does not exist."
    try:
        original = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "Cannot read target safely: {}".format(exc)
    if not old_text:
        return "old_text must not be empty."
    count = original.count(old_text)
    if count != 1:
        return ("Patch not applied: old_text must match exactly once (found {}). "
                "No file changes were made. Re-read the current file; check indentation, "
                "whitespace and line endings. For a reliable positional edit, use "
                "replace_lines with the current 1-based line range.").format(count)
    updated = original.replace(old_text, new_text, 1)
    diff = "".join(difflib.unified_diff(
        original.splitlines(True), updated.splitlines(True),
        fromfile="before/" + path, tofile="after/" + path
    ))
    risk = _file_change_risk(original, updated)
    print_change_preview(
        diff,
        "Targeted patch",
        "Lines: {} -> {} ({} removed); bytes: {} -> {}".format(
            risk["old_lines"], risk["new_lines"], risk["removed_lines"],
            risk["old_bytes"], risk["new_bytes"]
        ),
        CONFIG_FILE,
    )
    risky = risk["deletion_ratio"] > 0.25 or risk["size_reduction"] > 0.25
    if risky:
        print("Risk warning: patch removes {} of {} lines ({:.1%}); file size changes {} -> {} bytes.".format(
            risk["removed_lines"], risk["old_lines"], risk["deletion_ratio"],
            risk["old_bytes"], risk["new_bytes"]))
        if not _explicit_file_approval("This is a risky edit. Explicitly approve? [y/N]: "):
            return "User declined risky patch. File was not changed."
    try:
        backup = _atomic_replace_file(file_path, updated)
    except Exception as exc:
        return "Patch failed; original file preserved: {}".format(exc)
    return "Successfully patched {}. Backup: {}".format(path, str(backup) if backup else "not needed")


def replace_lines(path, start_line, end_line, new_text):
    """Replace a 1-based inclusive line range, avoiding fragile text matching."""
    if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1:
        return "start_line must be a positive 1-based line number."
    if isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < start_line:
        return "end_line must be an integer greater than or equal to start_line."
    if not isinstance(new_text, str):
        return "new_text must be a string."

    file_path = safe_path(path)
    if not file_path.is_file():
        return "Target file does not exist."
    if file_path.stat().st_size > MAX_FILE_SIZE:
        return "Existing file is too large."
    try:
        original = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "Cannot read target safely: {}".format(exc)

    lines = original.splitlines(True)
    if end_line > len(lines):
        return "Line range {}-{} exceeds file length ({} lines).".format(
            start_line, end_line, len(lines)
        )

    replacement = new_text
    if replacement and not replacement.endswith(("\n", "\r")):
        replacement += "\n"
    updated = "".join(lines[:start_line - 1]) + replacement + "".join(lines[end_line:])
    if updated == original:
        return "No changes: replacement is identical to the selected lines."

    diff = "".join(difflib.unified_diff(
        original.splitlines(True), updated.splitlines(True),
        fromfile="before/" + path, tofile="after/" + path
    ))
    risk = _file_change_risk(original, updated)
    print_change_preview(
        diff, "Line-range replacement",
        "Replaced lines {}-{}; file lines: {} -> {}; bytes: {} -> {}".format(
            start_line, end_line, risk["old_lines"], risk["new_lines"],
            risk["old_bytes"], risk["new_bytes"]
        ),
        CONFIG_FILE,
    )
    risky = risk["deletion_ratio"] > 0.25 or risk["size_reduction"] > 0.25
    if risky:
        print("Risk warning: patch removes {} of {} lines ({:.1%}).".format(
            risk["removed_lines"], risk["old_lines"], risk["deletion_ratio"]
        ))
        approved = _explicit_file_approval(
            "This is a risky edit. Explicitly approve? [y/N]: "
        )
    else:
        approved = confirm_action("Apply this line-range replacement? [y/N/a=all]: ")
    if not approved:
        return "User declined line-range replacement. File was not changed."

    try:
        backup = _atomic_replace_file(file_path, updated)
    except Exception as exc:
        LOGGER.exception("Line-range replacement failed path=%s", path)
        return "Replacement failed; original preserved: {}".format(exc)
    return "Successfully replaced lines {}-{} in {}. Backup: {}".format(
        start_line, end_line, path, str(backup) if backup else "not needed"
    )


def edit_file(path, content):
    """Replace an entire file; risky reductions require explicit approval."""
    if not isinstance(content, str):
        return "File content must be text."
    LOGGER.info("File replacement requested path=%s new_content_chars=%d",
                path, len(content))
    file_path = safe_path(path)
    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return "Proposed file is too large."

    old_content = ""
    if file_path.exists():
        if not file_path.is_file():
            return "Target is not a regular file."
        if file_path.stat().st_size > MAX_FILE_SIZE:
            return "Existing file is too large."
        try:
            old_content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "Cannot safely edit a non-UTF-8 file."

    risk = _file_change_risk(old_content, content)
    diff = "".join(difflib.unified_diff(
        old_content.splitlines(True), content.splitlines(True),
        fromfile="before/" + path, tofile="after/" + path
    ))
    print_change_preview(
        diff,
        "Proposed changes (full-file replacement)",
        "Lines: {} -> {} ({} removed); bytes: {} -> {}".format(
            risk["old_lines"], risk["new_lines"], risk["removed_lines"],
            risk["old_bytes"], risk["new_bytes"]
        ),
        CONFIG_FILE,
    )

    risky = (risk["deletion_ratio"] > 0.25 or
             risk["size_reduction"] > 0.25)
    if risky:
        print("HIGH-RISK REPLACEMENT: {:.1%} of lines removed; file size reduced by {:.1%}.".format(
            risk["deletion_ratio"], risk["size_reduction"]))
        approved = _explicit_file_approval(
            "Explicitly approve this large deletion? [y/N]: "
        )
    else:
        approved = confirm_action("Apply this full-file replacement? [y/N/a=all]: ")
    if not approved:
        LOGGER.info("File edit declined path=%s", path)
        return "User declined the edit. File was not changed."

    try:
        backup = _atomic_replace_file(file_path, content)
    except Exception as exc:
        LOGGER.exception("Atomic file replacement failed path=%s", path)
        return "File replacement failed; original preserved: {}".format(exc)

    return "Successfully replaced {}. Backup: {}".format(
        path, str(backup) if backup else "not needed"
    )


# -------------------------------------------------------------------
# Test execution
# -------------------------------------------------------------------

def run_checks(check, path="."):
    LOGGER.info("run_checks requested check=%r path=%s timeout=%ss",
                check, path, TEST_EXECUTION["timeout_seconds"])
    directory = safe_path(path)

    if not directory.is_dir():
        return "Check path is not a directory."

    commands = {
        "pytest": ["python3", "-m", "pytest", "-q"],
        "unittest": ["python3", "-m", "unittest", "discover"],
        "compileall": ["python3", "-m", "compileall", "-q", "."]
    }

    if check not in commands:
        return "Unsupported check."

    command = commands[check]

    print("\nRunning:", " ".join(command))
    if not confirm_action("Allow execution? [y/N/a=all]: "):
        return "User declined command execution."

    timeout = TEST_EXECUTION["timeout_seconds"]
    result = run_bounded_interruptible_command(
        command, directory, timeout, TEST_EXECUTION
    )
    output = result["stdout"] + result["stderr"]
    if result["timed_out"]:
        status = "TIMEOUT"
    elif result["returncode"] == 0:
        status = "COMPLETED (exit code 0)"
    else:
        status = "FAILED (exit code {})".format(result["returncode"])

    details = [
        "Test status: {}".format(status),
        "Duration: {} seconds".format(result["duration_seconds"]),
        "Output bytes: stdout={}, stderr={}".format(
            result["stdout_total_bytes"], result["stderr_total_bytes"]
        )
    ]
    if result["output_truncated"]:
        details.append(
            "Output was truncated to the configured head/tail limits. "
            "The process continued running; output capture was bounded."
        )
    if output:
        details.append("Output:\n" + output)
    return "\n".join(details)


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
    "update_task_plan": update_task_plan
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


def is_invalid_tool_call_error(detail):
    """Recognize provider/gateway errors that specifically reject tool calls."""
    value = str(detail).lower()
    markers = (
        "invalid tool call provided",
        "invalid tool call",
        "invalid tool_calls",
        "malformed tool call",
        "malformed tool_calls",
        "tool call is invalid",
        "tool_calls is invalid",
    )
    return any(marker in value for marker in markers)


def api_request_with_retries(messages):
    """Retry transient API failures using configurable exponential backoff."""
    settings = RETRY_SETTINGS
    retries_used = 0

    while True:
        try:
            return api_request(messages)
        except APIRequestError as error:
            # Invalid tool-call errors are semantic/request-history failures,
            # not transient transport failures. Let the agent repair history.
            if error.invalid_tool_call or error.status_code == 400:
                raise
            if not settings["enabled"] or retries_used >= settings["max_retries"]:
                raise

            retryable = False
            if error.network_error and settings["retry_network_errors"]:
                retryable = True
            elif error.invalid_json and settings["retry_invalid_json"]:
                retryable = True
            elif error.status_code in settings["retry_http_statuses"]:
                retryable = True

            if not retryable:
                raise

            delay = min(
                settings["initial_backoff_seconds"],
                settings["max_backoff_seconds"]
            )
            for _ in range(retries_used):
                delay = min(
                    delay * settings["backoff_multiplier"],
                    settings["max_backoff_seconds"]
                )
            if settings["jitter"] and delay > 0:
                delay += random.uniform(
                    0, delay * settings["jitter_fraction"]
                )

            retries_used += 1
            LOGGER.warning("API retry %d/%d in %.1fs provider=%s reason=%s",
                           retries_used, settings["max_retries"], delay,
                           PROVIDER, str(error)[:300])
            print(
                "\n[Shai] API request failed ({}). Retry {}/{} in {:.1f}s.".format(
                    str(error)[:180], retries_used,
                    settings["max_retries"], delay
                )
            )
            time.sleep(delay)


def sanitize_openai_compatible_history(messages):
    """Remove Cohere/OpenRouter-incompatible reasoning blocks before sending.

    Some OpenAI-compatible providers return assistant ``content`` as a list
    containing private ``thinking`` blocks. Replaying those blocks (especially
    alongside ``tool_calls: []``) causes Cohere to reject the *next* request.
    Keep visible text, preserve real non-empty tool calls, and omit empty
    tool_calls arrays. The caller's in-memory history is not mutated.
    """
    sanitized = []
    removed_thinking = 0
    removed_empty_tool_calls = 0
    for original in messages:
        if not isinstance(original, dict):
            continue
        message = dict(original)

        if message.get("role") == "assistant":
            if message.get("tool_calls") == []:
                message.pop("tool_calls", None)
                removed_empty_tool_calls += 1

            content = message.get("content")
            if isinstance(content, list):
                removed_thinking += sum(
                    1 for block in content
                    if isinstance(block, dict) and block.get("type") == "thinking"
                )
                visible_text = _block_text(content).strip()
                message["content"] = visible_text or (
                    "[Previous assistant reasoning omitted.]"
                )

        sanitized.append(message)
    if removed_thinking or removed_empty_tool_calls:
        LOGGER.debug("Sanitized request history thinking_blocks=%d empty_tool_calls=%d",
                     removed_thinking, removed_empty_tool_calls)
    return sanitized


def build_kilo_tools():
    """Build the OpenAI-compatible tool payload expected by Kilo.

    Keep each function's human-readable description in the nested
    function object. Kilo uses these descriptions to guide tool selection;
    sending only names and parameter schemas weakens tool routing.
    """
    descriptions = {
        "list_files": "List project files under a directory.",
        "read_file": next((item["function"].get("description", "") for item in FUNCTIONS if item.get("function", {}).get("name") == "read_file"), "Read a project file in line-based chunks using path, offset, and limit."),
        "search_files": next((item["function"].get("description", "") for item in FUNCTIONS if item.get("function", {}).get("name") == "search_files"), "Search a project file or directory."),
        "git_status": "Show current Git status.",
    }
    kilo_tools = []
    for item in FUNCTIONS:
        function = item.get("function", {})
        parameters = function.get("parameters", {})
        kilo_function = {
            "name": function.get("name", ""),
            "description": descriptions.get(
                function.get("name"), function.get("description", "")
            ),
            "parameters": parameters,
        }
        kilo_tools.append({"type": "function", "function": kilo_function})
    return kilo_tools


def api_request(messages):
    key = os.environ.get(PROVIDERS[PROVIDER]["key_env"])

    if not key:
        raise RuntimeError(
            PROVIDERS[PROVIDER]["key_env"] + " is not set."
        )

    if PROVIDERS[PROVIDER]["format"] == "anthropic":
        anthropic_tools = []

        for item in FUNCTIONS:
            function = item["function"]
            anthropic_tools.append({
                "name": function["name"],
                "description": function.get("description", ""),
                "input_schema": function["parameters"]
            })

        # Anthropic accepts the system prompt separately and does not
        # accept a "system" role inside the messages array.
        api_messages = [
            item for item in messages
            if item.get("role") != "system"
        ]

        body = {
            "model": MODEL,
            "max_tokens": 8192,
            "system": SYSTEM_PROMPT,
            "messages": api_messages,
            "tools": anthropic_tools
        }

        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
    else:
        tools_payload = (
            build_kilo_tools()
            if PROVIDER in ("kilo", "kilo-free")
            else FUNCTIONS
        )
        body = {
            "model": MODEL,
            "messages": sanitize_openai_compatible_history(messages),
            "tools": tools_payload,
            "tool_choice": "auto"
        }

        if PROVIDER == "openai":
            body["max_completion_tokens"] = 8192
        else:
            body["temperature"] = 0.2
            body["max_tokens"] = 8192

        headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json"
        }

        if PROVIDER == "openrouter":
            headers["HTTP-Referer"] = "https://openrouter.ai/"
            headers["X-Title"] = "Shai Coding Agent"

    log_api_payload(LOGGER, "API REQUEST", body)
    request_data = json.dumps(body).encode("utf-8")
    LOGGER.debug(
        "API request start provider=%s model=%s messages=%d payload_bytes=%d timeout=%ss",
        PROVIDER, MODEL, len(body.get("messages", [])), len(request_data),
        API_TIMEOUT_SECONDS
    )
    request = urllib.request.Request(
        PROVIDERS[PROVIDER]["url"],
        data=request_data,
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
        LOGGER.debug("API response received provider=%s status=%s bytes=%d",
                     PROVIDER, getattr(response, "status", "unknown"), len(raw))
        try:
            parsed_response = json.loads(raw)
            log_api_payload(LOGGER, "API RESPONSE", parsed_response)
            return parsed_response
        except (ValueError, TypeError) as error:
            raise APIRequestError(
                "API returned invalid JSON: {}".format(error),
                invalid_json=True
            )

    except (TimeoutError, socket.timeout) as error:
        LOGGER.exception("API request timed out provider=%s model=%s after %ss",
                         PROVIDER, MODEL, API_TIMEOUT_SECONDS)
        raise APIRequestError(
            "API request timed out after {} seconds.".format(API_TIMEOUT_SECONDS),
            network_error=True
        ) from error
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        log_api_payload(LOGGER, "API ERROR RESPONSE", detail)
        LOGGER.warning("API HTTP error provider=%s status=%s body=%s",
                       PROVIDER, error.code, detail[:1000])
        invalid_tool_call = is_invalid_tool_call_error(detail)
        raise APIRequestError(
            "API error {}: {}".format(error.code, detail[:2000]),
            status_code=error.code,
            invalid_tool_call=invalid_tool_call
        )

    except urllib.error.URLError as error:
        LOGGER.exception("API network error provider=%s model=%s", PROVIDER, MODEL)
        raise APIRequestError(
            "Network error: " + str(error), network_error=True
        )


# -------------------------------------------------------------------
# Agent loop
# -------------------------------------------------------------------

class ToolSelectionFailure(Exception):
    """Raised when the model repeatedly selects tools Shai does not expose."""


def resolve_tool_name(name):
    """Resolve harmless provider/model naming variations without guessing."""
    if not isinstance(name, str):
        return None
    candidate = name.strip()
    for prefix in ("functions.", "tools.", "tool."):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
            break
    if candidate in TOOL_DISPATCH:
        return candidate

    normalized = candidate.replace("-", "_").replace(" ", "_")
    if normalized in TOOL_DISPATCH:
        return normalized

    # Permit only very close, unambiguous typos (e.g. run_check -> run_checks).
    matches = difflib.get_close_matches(
        normalized, list(TOOL_DISPATCH), n=2, cutoff=0.94
    )
    if len(matches) == 1:
        return matches[0]
    return None


def handle_tool_selection(name, args, call_tracker, invalid_tool_counter,
                          invalid_args_tracker=None, history=None):
    """Validate tool names and disable tools after repeated bad arguments."""
    if invalid_args_tracker is None:
        invalid_args_tracker = ToolArgumentFailureTracker(
            AGENT_SAFEGUARDS["max_invalid_argument_attempts_per_tool"]
        )
    resolved_name = resolve_tool_name(name)
    if resolved_name is None:
        invalid_tool_counter["count"] += 1
        count = invalid_tool_counter["count"]
        limit = RETRY_SETTINGS["invalid_tool_selection_max_attempts"]
        result = {
            "error": "Invalid tool selection: {!r} is not an available tool.".format(name),
            "allowed_tools": sorted(TOOL_DISPATCH),
            "instruction": (
                "Choose an exact tool name from allowed_tools. Do not invent "
                "tool names or arguments. This is invalid selection attempt "
                "{}/{}. If no tool is appropriate, answer the user directly."
            ).format(count, limit)
        }
        should_abort = (
            RETRY_SETTINGS["invalid_tool_selection_enabled"]
            and count >= limit
        )
        if should_abort:
            result["instruction"] += (
                " Retry limit reached; Shai will stop this request and "
                "return to the prompt to avoid looping."
            )
        return result, should_abort

    if invalid_args_tracker.is_disabled(resolved_name):
        return {
            "error": "Tool {!r} is disabled for the rest of this request after repeated invalid arguments.".format(resolved_name),
            "instruction": (
                "Do not call this tool again in the current request. Use the "
                "previous tool results, another available tool, or explain "
                "the limitation to the user."
            ),
            "tool_disabled_for_request": True
        }, False

    if AGENT_SAFEGUARDS["validate_tool_arguments"]:
        validation_error = validate_tool_arguments(
            resolved_name, args, FUNCTIONS,
            reject_unknown=AGENT_SAFEGUARDS["reject_unknown_tool_arguments"]
        )
        if validation_error:
            count, disabled = invalid_args_tracker.record(
                resolved_name, validation_error
            )
            limit = AGENT_SAFEGUARDS["max_invalid_argument_attempts_per_tool"]
            instruction = (
                "Correct the arguments to match the advertised schema. "
                "Do not add unsupported parameters. Validation warning "
                "{}/{} for this tool; the tool remains available and you may "
                "retry with corrected parameters."
            ).format(count, limit)
            if disabled:
                instruction = (
                    "This tool is now disabled for the rest of this request. "
                    "Do not call it again. Use existing results, another tool, "
                    "or explain the limitation."
                )
            return {
                "error": "Invalid arguments for {}: {}".format(
                    resolved_name, validation_error
                ),
                "instruction": instruction,
                "tool_disabled_for_request": disabled
            }, False

    result = execute_tool_with_safeguards(
        resolved_name, args, call_tracker, history=history
    )
    if resolved_name != name:
        if isinstance(result, dict):
            result = dict(result)
            result["tool_name_normalized_from"] = name
            result["tool_name_executed"] = resolved_name
    return result, False



def _tool_result_is_error(name, result):
    """Recognize actionable tool failures returned as human-readable strings."""
    prefixes = {
        "list_files": ("Not a directory.",),
        "read_file": (
            "File does not exist.", "File is too large", "Invalid offset:",
            "Invalid limit:", "Requested limit", "Offset ",
            "File is not UTF-8 text.", "Unable to read file:",
        ),
        "patch_file": (
            "Patch not applied:", "Target file does not exist.",
            "Cannot read target safely:", "Patch failed;",
        ),
        "replace_lines": (
            "start_line must", "end_line must", "new_text must",
            "Target file does not exist.", "Existing file is too large.",
            "Line range ", "Cannot read target safely:", "Replacement failed;",
        ),
        "edit_file": (
            "File content must", "Target is not a regular file.",
            "Existing file is too large.", "Cannot safely edit",
            "Validation failed", "File replacement failed;",
        ),
        "run_checks": ("Check path is not a directory.", "Unsupported check."),
        "verify_changes": ("Verification path must", "test_check must"),
        "get_file_outline": ("Expected a Python source file.", "Python file exceeds"),
        "find_symbol": ("symbol must be a non-empty string.",),
        "find_references": ("symbol must be a non-empty string.",),
        "get_project_map": ("Project map path must be a directory.",),
        "get_test_map": ("Test map path must be a directory.",),
        "inspect_test_failure": ("output must be a string",),
        "update_task_plan": ("steps must be", "Each step must be", "Each task step", "Invalid task status"),
    }
    return any(result.startswith(prefix) for prefix in prefixes.get(name, ()))

def execute_tool(name, args):
    function = TOOL_DISPATCH.get(name)
    LOGGER.debug("Tool execution start name=%s argument_keys=%s",
                 name, sorted(args.keys()) if isinstance(args, dict) else "invalid")

    if function is None:
        return {"error": "Unknown tool: " + str(name)}

    try:
        started = time.time()
        result = function(**args)
        LOGGER.debug("Tool execution complete name=%s elapsed=%.3fs result_type=%s",
                     name, time.time() - started, type(result).__name__)
        if isinstance(result, dict) and result.get("error"):
            return result
        if isinstance(result, str) and _tool_result_is_error(name, result):
            LOGGER.warning("Tool returned failure name=%s details=%s", name, result[:1000])
            failure = {"error": result}
            if name == "patch_file" and result.startswith("Patch not applied:"):
                failure["suggested_tool"] = "replace_lines"
                failure["instruction"] = (
                    "Do not repeat this exact patch. Read the current file lines, "
                    "then use replace_lines with the current 1-based line range."
                )
            return failure
        return {"result": result}
    except IterationInterrupted:
        raise
    except Exception as error:
        LOGGER.exception("Tool execution failed name=%s", name)
        return {"error": str(error)}

def _tool_call_signature(name, args):
    """Compatibility wrapper; all normalized parameters participate."""
    return tool_call_signature(
        name, args, AGENT_SAFEGUARDS["default_read_file_lines"]
    )


def invalidate_cached_reads_for_path(call_tracker, changed_path):
    """Invalidate cached read_file pages after a successful file modification."""
    try:
        changed = relative_path(safe_path(changed_path))
    except Exception:
        changed = os.path.normpath(str(changed_path))
    for signature, entry in list(call_tracker.items()):
        if not isinstance(entry, dict):
            continue
        params = entry.get("parameters")
        if not isinstance(params, dict) or "path" not in params:
            continue
        try:
            payload = json.loads(signature)
        except (TypeError, ValueError):
            continue
        if payload.get("name") != "read_file":
            continue
        try:
            cached_path = relative_path(safe_path(params.get("path", "")))
        except Exception:
            cached_path = os.path.normpath(str(params.get("path", "")))
        if cached_path == changed:
            del call_tracker[signature]


def execute_tool_with_safeguards(name, args, call_tracker, history=None):
    """Execute calls normally; stop only an identical call stuck on the same error."""
    signature = _tool_call_signature(name, args)
    previous = call_tracker.get(signature)
    count = previous.get("count", 0) if isinstance(previous, dict) else 0
    previous_error_count = previous.get("consecutive_error_count", 0) if isinstance(previous, dict) else 0
    result = execute_tool(name, args)
    is_error = isinstance(result, dict) and bool(result.get("error"))
    error_count = previous_error_count + 1 if is_error else 0
    count += 1

    if (name in ("edit_file", "patch_file") and not is_error
            and isinstance(args, dict) and args.get("path")):
        invalidate_cached_reads_for_path(call_tracker, args["path"])

    warning_after = TOOL_SAFEGUARDS["repeat_call_warning_after"]
    if count >= warning_after and isinstance(result, dict):
        result = dict(result)
        result["repeat_call_warning"] = (
            "This exact tool call has been executed {} times. Identical calls are allowed, "
            "but check whether the result is adding new information."
        ).format(count)
        result["repeat_call_parameters"] = dict(args) if isinstance(args, dict) else args

    # Three identical failed calls in this request indicate a stuck tool cycle.
    # Successful repeats and different parameter sets remain unrestricted.
    if is_error and error_count >= 3:
        result = dict(result)
        result["stop_request"] = True
        result["instruction"] = (
            "This exact tool call has failed three consecutive times with the same parameters. "
            "Stop retrying it. Choose a different path/tool or explain the blocker to the user."
        )
        LOGGER.warning("Stopping repeated failing tool call name=%s count=%d parameters=%s error=%s",
                       name, error_count, json.dumps(args, sort_keys=True, default=str), result.get("error"))

    call_tracker[signature] = {
        "count": count,
        "consecutive_error_count": error_count,
        "result": result,
        "parameters": dict(args) if isinstance(args, dict) else args,
    }
    LOGGER.debug("Tool repeat verification name=%s count=%d consecutive_errors=%d signature=%s parameters=%s",
                 name, count, error_count, signature, json.dumps(args, sort_keys=True, default=str))
    return result


def _block_text(value):
    """Convert provider content blocks into readable text for recovery."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
            elif block.get("type") == "tool_result":
                nested = _block_text(block.get("content", ""))
                label = "Previous tool result"
                if block.get("is_error"):
                    label += " (error)"
                parts.append(label + ":\n" + nested)
        return "\n".join(parts)
    if value is None:
        return ""
    return str(value)


def display_tool_call(name, args):
    """Display effective tool parameters, including pagination, before execution."""
    display_args = dict(args) if isinstance(args, dict) else args
    if name == "read_file" and isinstance(display_args, dict):
        display_args.setdefault("offset", 0)
        display_args.setdefault(
            "limit", AGENT_SAFEGUARDS["default_read_file_lines"]
        )
    action_labels = {
        "search_files": "Searching project files",
        "read_file": "Reading project file",
        "list_files": "Listing project files",
        "edit_file": "Preparing file edit",
        "patch_file": "Applying file patch",
        "run_checks": "Running project checks",
        "git_status": "Checking Git status",
        "git_diff": "Inspecting Git changes",
    }
    print("\n[Shai] {}…".format(action_labels.get(name, "Running {}".format(name))))
    if TOOL_CALL_DISPLAY["show_parameters"]:
        print(format_tool_call_for_display(
            name, display_args, TOOL_CALL_DISPLAY["max_argument_chars"]
        ))
    else:
        print("[Shai tool] " + str(name))
    sys.stdout.flush()


def display_assistant_message_content(message):
    """Display user-facing assistant text, including text paired with tools."""
    if not isinstance(message, dict):
        return False
    assistant_text = _block_text(message.get("content", ""))
    if not assistant_text.strip():
        return False
    print("\n" + assistant_text)
    sys.stdout.flush()
    return True


def _bounded_recovery_text(value, limit):
    text = _block_text(value)
    if len(text) <= limit:
        return text
    head = max(1, limit * 2 // 3)
    tail = max(1, limit - head)
    return (text[:head] + "\n...[tool result shortened during recovery]...\n"
            + text[-tail:])


def repair_tool_history(history):
    """Remove provider-specific tool protocol records but retain their results.

    OpenAI-style assistant tool_calls/tool messages and Anthropic-style
    tool_use/tool_result blocks are converted to ordinary conversational text.
    This prevents a malformed tool-call exchange from being resubmitted while
    keeping prior observations available to the model.

    Returns (repaired_history, number_of_tool_records_removed).
    """
    settings = RETRY_SETTINGS.get("invalid_tool_call_recovery", {})
    result_limit = settings.get("max_tool_result_chars", 12000)
    repaired = []
    removed = 0

    def append_user_text(text):
        text = str(text or "").strip()
        if not text:
            return
        repaired.append({"role": "user", "content": text})

    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content", "")

        if role == "assistant" and message.get("tool_calls"):
            removed += len(message.get("tool_calls", []))
            assistant_text = _block_text(content)
            repaired.append({
                "role": "assistant",
                "content": assistant_text or
                    "[Previous tool-call request omitted during recovery.]"
            })
            continue

        if role == "assistant" and isinstance(content, list):
            tool_uses = [
                block for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
            if tool_uses:
                removed += len(tool_uses)
                assistant_text = _block_text(content)
                # _block_text intentionally ignores tool_use blocks.
                repaired.append({
                    "role": "assistant",
                    "content": assistant_text or
                        "[Previous tool-use request omitted during recovery.]"
                })
                continue

            # Some providers (notably Cohere through OpenRouter) return
            # assistant content arrays containing private `thinking` blocks.
            # These cannot safely be replayed as ordinary conversation history:
            # an empty tool_calls array plus a thinking-only block is rejected
            # by the provider. Keep visible text, discard non-text reasoning,
            # and make the message plain text before retrying.
            assistant_text = _block_text(content)
            repaired.append({
                "role": "assistant",
                "content": assistant_text or
                    "[Previous assistant reasoning omitted during recovery.]"
            })
            continue

        if role == "assistant":
            # An empty tool_calls array is not a meaningful tool request and
            # can trigger provider-specific history validation errors.
            message = dict(message)
            if message.get("tool_calls") == []:
                message.pop("tool_calls", None)

        if role == "tool":
            removed += 1
            name = message.get("name") or "unknown tool"
            text = _bounded_recovery_text(content, result_limit)
            append_user_text("[Previous tool result from {}]\n{}".format(
                name, text
            ))
            continue

        if role == "user" and isinstance(content, list):
            had_tool_result = False
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    removed += 1
                    had_tool_result = True
                    text = _bounded_recovery_text(
                        block.get("content", ""), result_limit
                    )
                    label = "[Previous tool result]"
                    if block.get("is_error"):
                        label = "[Previous tool result: error]"
                    append_user_text(label + "\n" + text)
                elif block.get("type") == "text" and block.get("text"):
                    append_user_text(block["text"])
            if had_tool_result:
                continue

        repaired.append(message)

    return repaired, removed


def _invalid_tool_call_recovery_note(attempt, maximum):
    return (
        "Recovery instruction from Shai (attempt {}/{}): the provider rejected "
        "the previous tool-call request as invalid. The malformed tool protocol "
        "history has been removed; prior tool results and the original user "
        "request have been retained. Use only these exact available tool names: "
        "{}. Generate valid JSON object arguments matching each tool schema. "
        "If a tool is not needed, answer directly without a tool call. Do not "
        "repeat the malformed tool call."
    ).format(attempt, maximum, ", ".join(sorted(TOOL_DISPATCH)))


def _ask_model_impl(history, user_text):
    call_tracker = {}
    invalid_tool_counter = {"count": 0}
    invalid_args_tracker = ToolArgumentFailureTracker(
        AGENT_SAFEGUARDS["max_invalid_argument_attempts_per_tool"]
    )
    invalid_tool_recovery_attempts = 0
    no_progress_count = 0
    recent_tool_batches = []
    total_tool_calls = 0
    history.append({
        "role": "user",
        "content": user_text
    })

    LOGGER.info("Agent request started provider=%s model=%s user_chars=%d history_messages=%d",
                PROVIDER, MODEL, len(user_text), len(history))
    for iteration in range(MAX_ITERATIONS):
        if AGENT_SAFEGUARDS["enabled"]:
            original_chars = len(json.dumps(history, ensure_ascii=False))
            compacted = compact_conversation_history(
                history,
                max_chars=AGENT_SAFEGUARDS["max_history_chars"],
                max_tool_result_chars=AGENT_SAFEGUARDS["max_tool_result_chars"],
                keep_recent_turns=AGENT_SAFEGUARDS["keep_recent_turns"],
                max_recent_tool_cycles=AGENT_SAFEGUARDS["max_recent_tool_cycles"]
            )
            history[:] = compacted
            compacted_chars = len(json.dumps(history, ensure_ascii=False))
            if compacted_chars < original_chars:
                LOGGER.warning(
                    "Compacted conversation history chars=%d->%d messages=%d",
                    original_chars, compacted_chars, len(history)
                )
        iteration_tool_signatures = []
        iteration_tool_errors = False
        iteration_tool_successes = 0
        tool_limit_reached = False
        LOGGER.debug("Agent iteration %d/%d history_messages=%d history_chars=%d",
                     iteration + 1, MAX_ITERATIONS, len(history),
                     len(json.dumps(history, ensure_ascii=False)))
        spinner = Spinner("Shai is thinking")
        spinner.start()

        try:
            try:
                response = run_interruptible_call(
                    api_request_with_retries, history
                )
            except APIRequestError as error:
                if not error.invalid_tool_call:
                    raise

                recovery = RETRY_SETTINGS["invalid_tool_call_recovery"]
                maximum = recovery["max_recovery_attempts"]
                if not recovery["enabled"] or invalid_tool_recovery_attempts >= maximum:
                    raise InvalidToolCallRecoveryFailure(
                        "Provider rejected tool calls as invalid; recovery "
                        "attempt limit ({}) was reached. HTTP retries were "
                        "skipped because retrying the same malformed request "
                        "cannot fix it. Original request is retained."
                        .format(maximum)
                    )

                LOGGER.warning("Provider rejected tool-call history; starting recovery attempt=%d",
                               invalid_tool_recovery_attempts + 1)
                removed = 0
                if recovery["repair_conversation_history"]:
                    repaired, removed = repair_tool_history(history)
                    history[:] = repaired

                if (
                    recovery["stop_on_repeated_failure"]
                    and invalid_tool_recovery_attempts > 0
                    and removed == 0
                ):
                    raise InvalidToolCallRecoveryFailure(
                        "Provider rejected the regenerated request again, "
                        "and no additional malformed tool-history records "
                        "could be repaired. Stopping instead of resending "
                        "the same request."
                    )

                invalid_tool_recovery_attempts += 1
                history.append({
                    "role": "user",
                    "content": _invalid_tool_call_recovery_note(
                        invalid_tool_recovery_attempts, maximum
                    )
                })
                print(
                    "\n[Shai] Provider rejected an invalid tool call. "
                    "Skipped HTTP retry; repaired {} tool-history record(s). "
                    "Recovery {}/{} will regenerate the request.".format(
                        removed, invalid_tool_recovery_attempts, maximum
                    )
                )
                continue
        finally:
            spinner.stop()

        display_reasoning_summary(PROVIDER, response)

        # A length/max-token finish means the response may be incomplete.
        # Never execute partial tool arguments or accept partial file content.
        provider_format = PROVIDERS[PROVIDER]["format"]
        if provider_format == "anthropic":
            stop_reason = response.get("stop_reason")
            if stop_reason in ("max_tokens", "model_context_window_exceeded"):
                LOGGER.warning("Stopping request because provider response was truncated stop_reason=%s", stop_reason)
                print("\\n[Shai] Response was truncated by the provider ({}). "
                      "No tool calls or file edits from this response were applied. "
                      "The request has been stopped to avoid a retry loop; "
                      "try asking for a smaller change.".format(stop_reason))
                history.append({
                    "role": "assistant",
                    "content": "[Shai stopped because the provider truncated the response; no partial tool calls were executed.]"
                })
                return
        else:
            response_choices = response.get("choices", [])
            choice = response_choices[0] if response_choices else {}
            finish_reason = choice.get("finish_reason")
            native_finish_reason = choice.get("native_finish_reason")
            normalized_finish = str(finish_reason or "").lower()
            normalized_native_finish = str(native_finish_reason or "").lower()
            truncated_reason = (
                normalized_finish if normalized_finish in ("length", "max_tokens")
                else normalized_native_finish
                if normalized_native_finish in ("length", "max_tokens")
                else None
            )
            if truncated_reason:
                LOGGER.warning(
                    "Stopping request because provider response was truncated "
                    "finish_reason=%s native_finish_reason=%s",
                    finish_reason, native_finish_reason
                )
                print("\\n[Shai] Response was truncated by the provider ({}). "
                      "No tool calls or file edits from this response were applied. "
                      "The request has been stopped to avoid a retry loop; "
                      "try asking for a smaller change.".format(truncated_reason))
                history.append({
                    "role": "assistant",
                    "content": "[Shai stopped because the provider truncated the response; no partial tool calls were executed.]"
                })
                return

        if provider_format == "anthropic":
            text, tool_uses = extract_anthropic_response(response)
            assistant_content = response.get("content", [])

            history.append({
                "role": "assistant",
                "content": assistant_content
            })

            if text:
                print("\n" + text)

            if not tool_uses:
                if not text:
                    print("\nNo text response returned.")
                return

            tool_results = []
            abort_for_invalid_tool = False

            for tool_use in tool_uses:
                name = tool_use.get("name")
                args = tool_use.get("input", {})
                tool_use_id = tool_use.get("id")
                iteration_tool_signatures.append(_tool_call_signature(name, args))
                total_tool_calls += 1

                display_tool_call(name, args)

                if total_tool_calls > AGENT_SAFEGUARDS["max_tool_calls_per_request"]:
                    result = {"error": "Per-request tool-call limit reached; stopping this request."}
                    tool_limit_reached = True
                elif not isinstance(args, dict):
                    result = {"error": "Tool arguments must be an object."}
                else:
                    result, should_abort = handle_tool_selection(
                        name, args, call_tracker, invalid_tool_counter,
                        invalid_args_tracker=invalid_args_tracker, history=history
                    )
                    abort_for_invalid_tool = abort_for_invalid_tool or should_abort
                    if isinstance(result, dict) and not result.get("error"):
                        iteration_tool_successes += 1
                if isinstance(result, dict) and result.get("error"):
                    iteration_tool_errors = True
                if isinstance(result, dict) and result.get("error"):
                    print("[Tool error] {}".format(result.get("error")))
                if isinstance(result, dict) and result.get("stop_request"):
                    tool_limit_reached = True
                    print("\n[Shai] Repeated tool failure detected. Stopping this request; see the debug log for details.")

                tool_result = {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": bound_tool_result(
                        result, AGENT_SAFEGUARDS["max_tool_result_chars"]
                    )
                }
                if isinstance(result, dict) and "allowed_tools" in result:
                    tool_result["is_error"] = True
                tool_results.append(tool_result)

            history.append({
                "role": "user",
                "content": tool_results
            })
            if abort_for_invalid_tool:
                raise ToolSelectionFailure(
                    "The model repeatedly selected unavailable tools."
                )

        else:
            choices = response.get("choices", [])

            if not choices:
                print("API returned no response.")
                return

            message = choices[0].get("message", {})
            history.append(message)

            # OpenAI-compatible responses may contain BOTH user-facing text
            # and tool_calls. Display the text before executing those tools;
            # otherwise Kilo's explanations disappear and the user sees only
            # the tool activity.
            has_assistant_text = display_assistant_message_content(message)
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                if not has_assistant_text:
                    print("\nNo text response returned.")
                return

            abort_for_invalid_tool = False
            for call in tool_calls:
                call_id = call.get("id")
                function_data = call.get("function", {})
                name = function_data.get("name")
                raw_args = function_data.get("arguments", "{}")

                call_signature_added = False
                try:
                    args = json.loads(raw_args)

                    if not isinstance(args, dict):
                        raise ValueError(
                            "Tool arguments must be an object."
                        )

                    display_tool_call(name, args)
                    iteration_tool_signatures.append(_tool_call_signature(name, args))
                    call_signature_added = True
                    total_tool_calls += 1
                    if total_tool_calls > AGENT_SAFEGUARDS["max_tool_calls_per_request"]:
                        result = {"error": "Per-request tool-call limit reached; stopping this request."}
                        tool_limit_reached = True
                    else:
                        result, should_abort = handle_tool_selection(
                            name, args, call_tracker, invalid_tool_counter,
                            invalid_args_tracker=invalid_args_tracker, history=history
                        )
                        abort_for_invalid_tool = abort_for_invalid_tool or should_abort
                        if isinstance(result, dict) and not result.get("error"):
                            iteration_tool_successes += 1
                    if isinstance(result, dict) and result.get("error"):
                        iteration_tool_errors = True
                    if isinstance(result, dict) and result.get("error"):
                        print("[Tool error] {}".format(result.get("error")))
                    if isinstance(result, dict) and result.get("stop_request"):
                        tool_limit_reached = True
                        print("\n[Shai] Repeated tool failure detected. Stopping this request; see the debug log for details.")

                except IterationInterrupted:
                    raise
                except Exception as error:
                    if not call_signature_added:
                        display_tool_call(name, {"raw_arguments": raw_args})
                        iteration_tool_signatures.append(
                            _tool_call_signature(name, raw_args)
                        )
                        total_tool_calls += 1
                    iteration_tool_errors = True
                    result = {"error": str(error)}

                history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": bound_tool_result(
                        result, AGENT_SAFEGUARDS["max_tool_result_chars"]
                    )
                })

            if abort_for_invalid_tool:
                raise ToolSelectionFailure(
                    "The model repeatedly selected unavailable tools."
                )

        if iteration_tool_signatures:
            batch_signature = tuple(iteration_tool_signatures)
            no_progress_count, repeated_batch = update_no_progress(
                recent_tool_batches, batch_signature, no_progress_count,
                iteration_tool_errors, iteration_tool_successes
            )
            LOGGER.info(
                "Agent progress check no_progress=%d repeated_batch=%s "
                "tool_errors=%s successful_calls=%d total_tool_calls=%d",
                no_progress_count, repeated_batch, iteration_tool_errors,
                iteration_tool_successes, total_tool_calls
            )
            if tool_limit_reached:
                print("\n[Shai] Stopped this request after a repeated tool failure or tool-call safety limit.")
                return
            if total_tool_calls >= AGENT_SAFEGUARDS["max_tool_calls_per_request"]:
                print("\n[Shai] Per-request tool-call limit reached. Stopping to prevent an unbounded loop.")
                return
            if no_progress_count >= AGENT_SAFEGUARDS["max_consecutive_no_progress_iterations"]:
                LOGGER.warning("Stopping agent after %d consecutive no-progress iterations",
                               no_progress_count)
                print("\n[Shai] The agent is repeating tool calls or receiving errors without progress. "
                      "Stopping this request to prevent a loop.")
                return

    print("\nAgent stopped: maximum tool-call iterations reached.")


def ask_model(history, user_text):
    """Run one request; Ctrl+D cancels it and returns to the prompt."""
    LOGGER.debug("ask_model called history_messages=%d user_chars=%d",
                 len(history), len(user_text))
    history_length = len(history)
    try:
        _ask_model_impl(history, user_text)
    except IterationInterrupted:
        del history[history_length:]
        print("\n[Shai] Iteration interrupted. Returning to chat prompt.")
    except KeyboardInterrupt:
        del history[history_length:]
        print("\n[Shai] Iteration interrupted. Returning to chat prompt.")
    except InvalidToolCallRecoveryFailure as error:
        # Keep the user's original request, but do not leave recovery instructions
        # or provider-specific tool protocol records in the conversation.
        del history[history_length + 1:]
        history.append({
            "role": "assistant",
            "content": (
                "Shai could not recover from the provider's invalid tool-call "
                "response. The malformed tool-call history was removed; the "
                "original request is retained. You can retry, switch model "
                "with /model, or continue with a simpler request. Details: {}"
            ).format(error)
        })
        print("\n[Shai] {}".format(error))
        print("[Shai] Stopped retrying. The original request is retained and the invalid tool exchange was removed.")
    except ToolSelectionFailure as error:
        # Keep the user's original request, but discard malformed assistant/tool
        # messages so a later "retry" does not inherit a poisoned tool exchange.
        del history[history_length + 1:]
        history.append({
            "role": "user",
            "content": (
                "Recovery note from Shai: the previous attempt repeatedly "
                "selected an unavailable tool. Do not repeat that tool call. "
                "Only these tools exist: {}. If none is suitable, respond "
                "without calling a tool."
            ).format(", ".join(sorted(TOOL_DISPATCH)))
        })
        print("\n[Shai] {}".format(error))
        print("[Shai] Invalid tool-call history was cleared and a recovery note was retained. You can retry or rephrase.")


# -------------------------------------------------------------------
# Interactive terminal
# -------------------------------------------------------------------

def history_for_new_provider(history):
    """Keep readable conversation context while dropping provider-specific
    tool-call exchanges that may not be portable between APIs.
    """
    cleaned = []
    for item in history:
        role = item.get("role")
        if role == "system":
            cleaned.append(item)
        elif role in ("user", "assistant") and item.get("content"):
            cleaned.append({"role": role, "content": item["content"]})
    if not cleaned or cleaned[0].get("role") != "system":
        cleaned.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    return cleaned


def main():
    global PROVIDER, MODEL

    init_default_logging(load_config())
    LOGGER.info("Starting Shai coding agent root=%s api_timeout=%ss",
                ROOT, API_TIMEOUT_SECONDS)
    PROVIDER, MODEL = initialize_provider()
    LOGGER.info("Selected provider=%s model=%s", PROVIDER, MODEL)

    history_store = ConversationHistory(ROOT, "agent", SYSTEM_PROMPT)
    history = history_store.load()
    previous_provider = history_store.metadata().get("provider")
    if previous_provider and previous_provider != PROVIDER:
        history = history_for_new_provider(history)
        print("[Shai] Previous provider changed; retained text history and cleared old tool exchanges.")
    history_store.save(history, provider=PROVIDER, model=MODEL)

    print("\nShai - Coding Agent")
    print("Project:", ROOT)
    print("Provider:", PROVIDER)
    print("Model:", MODEL)
    print("Max tool-call iterations:", MAX_ITERATIONS)
    print("History:", history_store.path)
    print("\nProvider and model are selected interactively at startup.")
    print("Commands: /help /model /status /files /diff /reset /exit")
    print("Press Ctrl+D during an iteration to stop it and return here.")

    while True:
        try:
            user_text = input_with_history("\nYou: ", history_store).strip()

            if not user_text:
                continue

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
                print("""
Examples:
  Explain the project structure.
  Find where the login screen is implemented.
  Fix the bug in main.py.
  Add error handling to the API client.
  Review my current Git changes.
  Run the Python tests.

Commands:
  /model   - Switch provider and model
  /switch  - Alias for /model
  /status  - Git status
  /files   - List project files
  /diff    - Show Git diff
  /reset   - Clear conversation history
  /exit    - Exit Shai

Press Ctrl+D while Shai is processing to cancel the current iteration
and return to the chat prompt.

Configure max_tool_call_iterations, tool_safeguards, and retry in
shai_config.json. Retry settings control API retry count, exponential
backoff, jitter, retryable HTTP statuses, and invalid tool selection recovery.

File edits and test execution require approval.
Type 'a' or 'all' at any prompt to approve all future actions.
""")
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
                print("Conversation history cleared.")
                continue

            try:
                ask_model(history, user_text)
            except Exception as error:
                LOGGER.exception("Unhandled agent request error")
                print("\nError:", error)
            finally:
                history_store.save(history, provider=PROVIDER, model=MODEL)

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break


if __name__ == "__main__":
    main()