#!/usr/bin/env python3
"""
Shai interactive chat client.

Supports OpenRouter (free models), Gemini, Anthropic, OpenAI, and Kilo Gateway.
Uses the same shai_config.json and provider/model selection behaviour as agent.py.
Standard-library only.
"""

import ast
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from modules.provider_selection import PROVIDERS, initialize_provider
from modules.logging_config import init_default_logging, get_logger, log_api_payload
from modules.reasoning_handlers import display_reasoning_summary
from modules.conversation_history import ConversationHistory, input_with_history
from modules.agent_safeguards import compact_conversation_history
from modules.context_meter import (
    estimate_messages_tokens, get_context_window, format_context_status,
    log_and_display_usage
)
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(
    sys.argv[1] if __name__ == "__main__" and len(sys.argv) > 1 else "."
).expanduser().resolve()
CONFIG_FILE = SCRIPT_DIR / "shai_config.json"
LOGGER = get_logger("shai.chat")
DEFAULT_API_TIMEOUT_SECONDS = 300
MAX_FILE_SIZE = 200_000


def safe_path(path):
    """Resolve a path relative to ROOT and ensure it stays within ROOT."""
    target = (ROOT / path).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        raise ValueError("Path escapes project root.")
    return target


def load_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        LOGGER.exception("Could not load Shai configuration; using defaults")
        return {}


def load_api_timeout_seconds():
    value = load_config().get("api_timeout_seconds", DEFAULT_API_TIMEOUT_SECONDS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        LOGGER.warning("Invalid api_timeout_seconds=%r; using %s seconds.",
                       value, DEFAULT_API_TIMEOUT_SECONDS)
        return DEFAULT_API_TIMEOUT_SECONDS
    return value


API_TIMEOUT_SECONDS = load_api_timeout_seconds()


# -------------------------------------------------------------------
# Read-only tool definitions (shared with agent.py)
# -------------------------------------------------------------------

def list_files(path):
    """List project files under a directory."""
    target = safe_path(path)
    if not target.is_dir():
        return "Not a directory."
    entries = []
    for entry in sorted(target.iterdir()):
        rel = entry.relative_to(ROOT)
        if entry.is_dir():
            entries.append(str(rel) + "/")
        else:
            entries.append(str(rel))
    return "\n".join(entries) if entries else "(empty)"


def read_file(path, offset=0, limit=100):
    """Read a project file in line-based chunks using path, offset, and limit."""
    target = safe_path(path)
    if not target.is_file():
        return "File does not exist."
    try:
        size = target.stat().st_size
    except OSError:
        return "Unable to read file."
    if size > MAX_FILE_SIZE:
        return "File is too large."
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "File is not UTF-8 text."
    except OSError as error:
        return "Unable to read file: " + str(error)
    lines = text.splitlines()
    total = len(lines)
    if offset < 0 or offset >= total:
        return "Invalid offset: {} (file has {} lines).".format(offset, total)
    if limit <= 0:
        return "Invalid limit: must be positive."
    end = min(offset + limit, total)
    chunk = lines[offset:end]
    header = "Lines {}-{} of {}:".format(offset + 1, end, total)
    return header + "\n" + "\n".join(chunk)


def search_files(query, path="."):
    """Search a project file or directory."""
    target = safe_path(path)
    if not target.exists():
        return "Path does not exist."
    results = []
    if target.is_file():
        files = [target]
    else:
        files = [f for f in target.rglob("*") if f.is_file()]
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if query.lower() in line.lower():
                rel = file_path.relative_to(ROOT)
                results.append("{}:{}: {}".format(rel, i, line.strip()))
                if len(results) >= 100:
                    return "\n".join(results) + "\n... (truncated at 100 results)"
    return "\n".join(results) if results else "No matches found."


def git_status():
    """Show current Git status."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "Git status failed: " + result.stderr.strip()
        return result.stdout.strip() or "(clean)"
    except (OSError, subprocess.TimeoutExpired) as error:
        return "Git status error: " + str(error)


def git_diff():
    """Inspect Git changes."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "Git diff failed: " + result.stderr.strip()
        return result.stdout.strip() or "(no changes)"
    except (OSError, subprocess.TimeoutExpired) as error:
        return "Git diff error: " + str(error)


def get_file_outline(path):
    """Return top-level imports, classes, functions and methods with line ranges."""
    target = safe_path(path)
    if not target.is_file():
        return "Expected a Python source file."
    try:
        text = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as error:
        return "Unable to read file: " + str(error)
    if len(text) > MAX_FILE_SIZE:
        return "Python file exceeds maximum size."
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return "Invalid Python syntax: " + str(error)
    lines = text.splitlines()
    outline = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                outline.append("import {} at line {}".format(alias.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            outline.append("class {} at line {}".format(node.name, node.lineno))
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    outline.append("  def {} at line {}".format(item.name, item.lineno))
        elif isinstance(node, ast.FunctionDef):
            # Only top-level functions
            if isinstance(node.parent, ast.Module) if hasattr(node, 'parent') else True:
                outline.append("def {} at line {}".format(node.name, node.lineno))
    return "\n".join(outline) if outline else "(no imports, classes, or functions found)"


def find_symbol(symbol, path="."):
    """Find Python definitions of a class, function, method or assignment by exact name."""
    if not symbol or not isinstance(symbol, str):
        return "symbol must be a non-empty string."
    target = safe_path(path)
    if not target.exists():
        return "Path does not exist."
    results = []
    files = [target] if target.is_file() else [f for f in target.rglob("*.py") if f.is_file()]
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if len(text) > MAX_FILE_SIZE:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == symbol:
                    rel = file_path.relative_to(ROOT)
                    results.append("{} at line {} in {}".format(node.name, node.lineno, rel))
            elif isinstance(node, ast.Assign):
                for target_node in node.targets:
                    if isinstance(target_node, ast.Name) and target_node.id == symbol:
                        rel = file_path.relative_to(ROOT)
                        results.append("{} = ... at line {} in {}".format(symbol, node.lineno, rel))
    return "\n".join(results) if results else "No definitions found for '{}'.".format(symbol)


def find_references(symbol, path="."):
    """Search project text files for usages of a symbol."""
    if not symbol or not isinstance(symbol, str):
        return "symbol must be a non-empty string."
    target = safe_path(path)
    if not target.exists():
        return "Path does not exist."
    results = []
    files = [target] if target.is_file() else [f for f in target.rglob("*.py") if f.is_file()]
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if symbol in line:
                rel = file_path.relative_to(ROOT)
                results.append("{}:{}: {}".format(rel, i, line.strip()))
                if len(results) >= 100:
                    return "\n".join(results) + "\n... (truncated at 100 results)"
    return "\n".join(results) if results else "No references found for '{}'.".format(symbol)


def get_project_map(path="."):
    """Summarize project directories, file types and likely Python entry points."""
    target = safe_path(path)
    if not target.is_dir():
        return "Project map path must be a directory."
    summary = []
    py_files = 0
    total_files = 0
    for entry in target.rglob("*"):
        if entry.is_file():
            total_files += 1
            if entry.suffix == ".py":
                py_files += 1
    summary.append("Project root: {}".format(target))
    summary.append("Total files: {}".format(total_files))
    summary.append("Python files: {}".format(py_files))
    # Find likely entry points
    entry_points = []
    for entry in target.rglob("*.py"):
        if entry.is_file():
            try:
                text = entry.read_text(encoding="utf-8")
                if "if __name__ == \"__main__\":" in text or "def main(" in text:
                    entry_points.append(str(entry.relative_to(ROOT)))
            except (UnicodeDecodeError, OSError):
                pass
    if entry_points:
        summary.append("Likely entry points:")
        for ep in entry_points[:10]:
            summary.append("  " + ep)
    return "\n".join(summary)


def get_test_map(path="."):
    """List test files and heuristically map their names to likely source modules."""
    target = safe_path(path)
    if not target.is_dir():
        return "Test map path must be a directory."
    test_files = []
    for entry in target.rglob("test_*.py"):
        if entry.is_file():
            test_files.append(str(entry.relative_to(ROOT)))
    for entry in target.rglob("*_test.py"):
        if entry.is_file():
            test_files.append(str(entry.relative_to(ROOT)))
    if not test_files:
        return "No test files found (looking for test_*.py or *_test.py)."
    result = ["Test files found:"]
    for tf in sorted(test_files):
        result.append("  " + tf)
    return "\n".join(result)


def inspect_test_failure(output):
    """Extract likely failing test names, assertion errors, tracebacks and relevant context from supplied test output."""
    if not isinstance(output, str):
        return "output must be a string"
    lines = output.splitlines()
    failures = []
    current_test = None
    for i, line in enumerate(lines):
        # Look for pytest/unittest failure patterns
        if "FAILED" in line or "FAIL:" in line or "ERROR:" in line:
            current_test = line.strip()
            failures.append(current_test)
        elif current_test and ("AssertionError" in line or "assert " in line or "Traceback" in line):
            failures.append("  " + line.strip())
    return "\n".join(failures) if failures else "No clear test failures detected in output."


# Tool dispatch for read-only tools
TOOL_DISPATCH_READ_ONLY = {
    "list_files": list_files,
    "read_file": read_file,
    "search_files": search_files,
    "git_status": git_status,
    "git_diff": git_diff,
    "get_file_outline": get_file_outline,
    "find_symbol": find_symbol,
    "find_references": find_references,
    "get_project_map": get_project_map,
    "get_test_map": get_test_map,
    "inspect_test_failure": inspect_test_failure,
}

# OpenAI-compatible function declarations for read-only tools
FUNCTIONS_READ_ONLY = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List project files under a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a project file in line-based chunks using path, offset, and limit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "offset": {"type": "integer", "description": "Zero-based line offset", "default": 0},
                    "limit": {"type": "integer", "description": "Number of lines to read", "default": 100}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search a project file or directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "path": {"type": "string", "description": "File or directory to search", "default": "."}
                },
                "required": ["query"]
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
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Inspect Git changes.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_outline",
            "description": "Return top-level imports, classes, functions and methods with line ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Python file path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": "Find Python definitions of a class, function, method or assignment by exact name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol name to find"},
                    "path": {"type": "string", "description": "File or directory to search", "default": "."}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": "Search project text files for usages of a symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol name to find references for"},
                    "path": {"type": "string", "description": "File or directory to search", "default": "."}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_map",
            "description": "Summarize project directories, file types and likely Python entry points.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project directory path", "default": "."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_test_map",
            "description": "List test files and heuristically map their names to likely source modules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project directory path", "default": "."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_test_failure",
            "description": "Extract likely failing test names, assertion errors, tracebacks and relevant context from supplied test output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output": {"type": "string", "description": "Test output to analyze"}
                },
                "required": ["output"]
            }
        }
    },
]


SYSTEM_PROMPT = (
    "You are Shai, a helpful AI assistant. Be accurate, concise, and "
    "honest. If you are unsure, say so. You are in chat mode and have "
    "access to read-only tools to inspect the local project."
)


class Spinner:
    """Simple terminal spinner, compatible with Python 3.9 and iSH."""

    def __init__(self, message="Shai is thinking", interval=0.12):
        self.message = message
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = None
        self._frames = ("|", "/", "-", "\\")

    def _run(self):
        index = 0
        while not self._stop_event.is_set():
            frame = self._frames[index % len(self._frames)]
            sys.stdout.write("\r{} {}".format(self.message, frame))
            sys.stdout.flush()
            index += 1
            self._stop_event.wait(self.interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        return self

    def stop(self):
        if self._thread:
            self._stop_event.set()
            self._thread.join()
            self._thread = None
        sys.stdout.write("\r" + (" " * (len(self.message) + 4)) + "\r")
        sys.stdout.flush()


def extract_anthropic_response(response):
    """Return (text, tool-use blocks) from an Anthropic response."""
    text_parts = []
    tool_uses = []

    for block in response.get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            value = block.get("text", "")
            if value:
                text_parts.append(value)
        elif block.get("type") == "tool_use":
            tool_uses.append(block)

    return "\n".join(text_parts), tool_uses


def request_json(url, key, provider, body=None):
    if provider == "gemini":
        headers = {}
        separator = "&" if "?" in url else "?"
        url += separator + urllib.parse.urlencode({"key": key})
    elif provider == "anthropic":
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
    else:
        headers = {"Authorization": "Bearer " + key}

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    if provider == "openrouter" and body is not None:
        headers["HTTP-Referer"] = "https://openrouter.ai/"
        headers["X-Title"] = "Shai Chat"

    request = urllib.request.Request(
        url, data=data, headers=headers,
        method="POST" if data is not None else "GET"
    )

    payload_bytes = len(data) if data is not None else 0
    if body is not None:
        log_api_payload(LOGGER, "API REQUEST", body)
    LOGGER.debug("HTTP request start provider=%s method=%s payload_bytes=%d timeout=%ss",
                 provider, request.get_method(), payload_bytes, API_TIMEOUT_SECONDS)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            LOGGER.debug("HTTP response received provider=%s status=%s bytes=%d elapsed=%.3fs",
                         provider, getattr(response, "status", "unknown"),
                         len(raw), time.monotonic() - started)
            parsed_response = json.loads(raw)
            log_api_payload(LOGGER, "API RESPONSE", parsed_response)
            return parsed_response
    except (TimeoutError, socket.timeout):
        LOGGER.exception("HTTP request timed out provider=%s after %ss",
                         provider, API_TIMEOUT_SECONDS)
        raise
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        log_api_payload(LOGGER, "API ERROR RESPONSE", detail)
        LOGGER.warning("HTTP API error provider=%s status=%s body=%s",
                       provider, error.code, detail[:1000])
        raise RuntimeError(
            "API error {}: {}".format(error.code, detail[:2000])
        )
    except urllib.error.URLError as error:
        LOGGER.exception("Network error provider=%s", provider)
        raise RuntimeError("Network error: " + str(error))


def get_available_models(provider):
    key_name = PROVIDERS[provider]["key_env"]
    key = os.environ.get(key_name)
    if not key:
        raise RuntimeError(key_name + " is not set. Source setup_keys.sh first.")

    data = request_json(
        PROVIDERS[provider]["models_url"], key, provider
    )

    if provider == "openrouter":
        models = [
            item.get("id", "")
            for item in data.get("data", [])
            if item.get("id", "").endswith(":free")
        ]
    elif provider == "gemini":
        models = []
        for item in data.get("models", []):
            if "generateContent" not in item.get(
                "supportedGenerationMethods", []
            ):
                continue
            name = item.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/"):]
            if name:
                models.append(name)
    else:
        models = [
            item.get("id", "")
            for item in data.get("data", [])
            if item.get("id")
        ]

    return sorted(set(model for model in models if model))


def choose_model(models, preferred=None):
    if not models:
        raise RuntimeError("No models are currently available.")

    print("\nAvailable models:\n")
    for number, model in enumerate(models, 1):
        marker = " [saved]" if model == preferred else ""
        print("{:3}. {}{}".format(number, model, marker))

    if preferred in models:
        default_number = models.index(preferred) + 1
        prompt = "\nChoose model [{}] (Enter keeps saved model): ".format(
            default_number
        )
    else:
        default_number = None
        prompt = "\nChoose model number: "

    while True:
        choice = input(prompt).strip()
        if not choice and default_number is not None:
            return models[default_number - 1]
        try:
            index = int(choice) - 1
            if 0 <= index < len(models):
                return models[index]
        except ValueError:
            pass
        print("Invalid selection. Enter a number from the list.")


def api_request(history, provider, model):
    LOGGER.debug("Chat API request provider=%s model=%s history_messages=%d",
                 provider, model, len(history))
    key_name = PROVIDERS[provider]["key_env"]
    key = os.environ.get(key_name)
    if not key:
        raise RuntimeError(key_name + " is not set.")

    # Determine if provider supports tools
    supports_tools = PROVIDERS[provider]["format"] in ("openai", "anthropic")

    if PROVIDERS[provider]["format"] == "anthropic":
        messages = [
            item for item in history
            if item.get("role") != "system"
        ]
        body = {
            "model": model,
            "max_tokens": 8192,
            "system": SYSTEM_PROMPT,
            "messages": messages,
        }
        if supports_tools:
            body["tools"] = FUNCTIONS_READ_ONLY
    else:
        body = {
            "model": model,
            "messages": history,
        }
        if provider == "openai":
            body["max_completion_tokens"] = 8192
        else:
            body["temperature"] = 0.2
            body["max_tokens"] = 8192
        if supports_tools:
            body["tools"] = FUNCTIONS_READ_ONLY

    return request_json(
        PROVIDERS[provider]["url"], key, provider, body
    )


def response_text(response, provider):
    if PROVIDERS[provider]["format"] == "anthropic":
        text, tool_uses = extract_anthropic_response(response)
        if tool_uses:
            # Convert Anthropic tool_uses to OpenAI-compatible format
            tool_calls = []
            for tu in tool_uses:
                tool_calls.append({
                    "id": tu.get("id", "call_" + str(hash(str(tu)))),
                    "type": "function",
                    "function": {
                        "name": tu.get("name"),
                        "arguments": json.dumps(tu.get("input", {}))
                    }
                })
            return text, tool_calls
        return text, []

    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError("API returned no response choices.")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    tool_calls = message.get("tool_calls", [])

    if isinstance(content, str):
        return content, tool_calls

    # Some OpenAI-compatible providers return an array of text blocks.
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                pieces.append(block.get("text", ""))
        return "\n".join(piece for piece in pieces if piece), tool_calls

    return str(content) if content else "", tool_calls


def history_for_new_provider(history):
    """Retain plain-text conversation context across provider changes."""
    cleaned = []
    for item in history:
        role = item.get("role")
        if role == "system":
            cleaned.append(item)
        elif role in ("user", "assistant"):
            content = item.get("content")
            if isinstance(content, list):
                text_parts = [
                    block.get("text", "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                content = "\n".join(part for part in text_parts if part)
            if content:
                cleaned.append({"role": role, "content": content})
    if not cleaned or cleaned[0].get("role") != "system":
        cleaned.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    return cleaned


def main():
    init_default_logging(load_config())
    LOGGER.info("Starting Shai chat api_timeout=%ss", API_TIMEOUT_SECONDS)
    try:
        provider, model = initialize_provider(auto_select=True)
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled.")
        return
    except Exception as error:
        print("\nStartup error:", error)
        return

    LOGGER.info("Selected provider=%s model=%s", provider, model)
    history_store = ConversationHistory(pathlib.Path.cwd(), "chat", SYSTEM_PROMPT)
    history = history_store.load()
    previous_provider = history_store.metadata().get("provider")
    if previous_provider and previous_provider != provider:
        history = history_for_new_provider(history)
        print("[Shai] Previous provider changed; retained text history.")
    history_store.save(history, provider=provider, model=model)

    print("\nShai - Chat")
    print("Provider:", provider)
    print("Model:", model)
    print("Commands: /help /model /status /reset /exit")

    while True:
        try:
            user_text = input_with_history("\nYou: ", history_store).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if not user_text:
            continue
        if user_text == "/exit":
            print("Goodbye.")
            break
        if user_text in ("/model", "/switch", "/switch-model"):
            old_provider, old_model = provider, model
            try:
                provider, model = initialize_provider()
                LOGGER.info("Provider/model switched provider=%s model=%s",
                            provider, model)
            except (KeyboardInterrupt, EOFError):
                provider, model = old_provider, old_model
                print("\nModel switch cancelled; keeping {} / {}.".format(
                    provider, model
                ))
                continue
            except Exception as error:
                provider, model = old_provider, old_model
                print("\nCould not switch model: {}".format(error))
                continue
            history = history_for_new_provider(history)
            history_store.save(history, provider=provider, model=model)
            LOGGER.debug("Provider switch retained history_messages=%d", len(history))
            print("\nSwitched to: {} / {}".format(provider, model))
            continue
        if user_text == "/help":
            print(
                "\nChat with Shai normally.\n"
                "/model  - Switch provider and model\n"
                "/reset  - Clear conversation history\n"
                "/status - Show provider and model\n"
                "/exit   - Exit Shai"
            )
            continue
        if user_text == "/status":
            print("Provider:", provider)
            print("Model:", model)
            continue
        if user_text == "/reset":
            LOGGER.info("Conversation history reset")
            history = [{"role": "system", "content": SYSTEM_PROMPT}]
            history_store.clear()
            print("Conversation history cleared.")
            continue

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
        history_store.save(history, provider=provider, model=model)
        context_limit = get_context_window(provider, model, load_config())
        estimated_prompt_tokens = estimate_messages_tokens(history)
        context_status = format_context_status(
            estimated_prompt_tokens, context_limit
        )
        LOGGER.debug(
            "Chat context estimate provider=%s model=%s estimated_prompt_tokens=%d "
            "context_limit=%s history_messages=%d",
            provider, model, estimated_prompt_tokens, context_limit, len(history)
        )
        print("\n[Shai] {}".format(context_status))
        spinner = Spinner("Shai is thinking | " + context_status)
        spinner.start()
        LOGGER.info("Chat turn started provider=%s model=%s user_chars=%d history_messages=%d",
                    provider, model, len(user_text), len(history))
        try:
            response = api_request(history, provider, model)
            log_and_display_usage(
                LOGGER, response, PROVIDERS[provider]["format"],
                estimated_prompt_tokens, context_limit
            )
            display_reasoning_summary(provider, response)
            answer, tool_calls = response_text(response, provider)
            LOGGER.debug("Chat response parsed answer_chars=%d tool_calls=%d",
                         len(answer or ""), len(tool_calls))
        except Exception as error:
            LOGGER.exception("Chat turn failed provider=%s model=%s", provider, model)
            answer = None
            tool_calls = []
            print("\nError:", error)
        finally:
            spinner.stop()

        # Handle tool calls if any
        if tool_calls:
            # Add assistant message with tool calls to history
            if PROVIDERS[provider]["format"] == "anthropic":
                # For Anthropic, we need to format differently
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
                # OpenAI-compatible format
                message = {"role": "assistant", "content": answer or ""}
                if tool_calls:
                    message["tool_calls"] = tool_calls
                history.append(message)
            history_store.save(history, provider=provider, model=model)

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
            history_store.save(history, provider=provider, model=model)

            # Continue the conversation to get the final answer
            # Make another API call with the tool results
            spinner = Spinner("Shai is thinking | " + context_status)
            spinner.start()
            try:
                response = api_request(history, provider, model)
                log_and_display_usage(
                    LOGGER, response, PROVIDERS[provider]["format"],
                    estimated_prompt_tokens, context_limit
                )
                display_reasoning_summary(provider, response)
                answer, tool_calls = response_text(response, provider)
                LOGGER.debug("Chat response parsed answer_chars=%d tool_calls=%d",
                             len(answer or ""), len(tool_calls))
            except Exception as error:
                LOGGER.exception("Chat turn failed provider=%s model=%s", provider, model)
                answer = None
                print("\nError:", error)
            finally:
                spinner.stop()

        if answer:
            LOGGER.info("Chat turn completed answer_chars=%d", len(answer))
            print("\n" + answer)
            if PROVIDERS[provider]["format"] == "anthropic":
                history.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                })
            else:
                history.append({"role": "assistant", "content": answer})
            history_store.save(history, provider=provider, model=model)
        elif answer == "":
            print("\nNo text response returned.")
            history.pop()
            history_store.save(history, provider=provider, model=model)
        else:
            # Remove the failed user turn so it doesn't poison future context.
            history.pop()
            history_store.save(history, provider=provider, model=model)


if __name__ == "__main__":
    main()