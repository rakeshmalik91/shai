import os
import sys
import json
import urllib.request
import urllib.error
import pathlib
import subprocess
import difflib
import urllib.parse

from chat import Spinner, extract_anthropic_response

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "shai_config.json"

DEFAULT_PROVIDER = "openrouter"
DEFAULT_OPENROUTER_MODEL = "cohere/north-mini-code:free"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"

ROOT = pathlib.Path(
    sys.argv[1] if len(sys.argv) > 1 else "."
).expanduser().resolve()

PROVIDER_ARG = (
    sys.argv[2].lower()
    if len(sys.argv) > 2
    else None
)

MAX_FILE_SIZE = 200_000
MAX_ITERATIONS = 12

PROVIDERS = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "format": "openai"
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_env": "GEMINI_API_KEY",
        "format": "openai"
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "models_url": "https://api.anthropic.com/v1/models?limit=1000",
        "key_env": "ANTHROPIC_API_KEY",
        "format": "anthropic"
    },
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "models_url": "https://api.openai.com/v1/models",
        "key_env": "OPENAI_API_KEY",
        "format": "openai"
    },
    "kilo": {
        "url": "https://ai-gateway.kilo.ai/api/v1/chat/completions",
        "models_url": "https://ai-gateway.kilo.ai/api/v1/models",
        "key_env": "KILO_API_KEY",
        "format": "openai"
    }
}

SYSTEM_PROMPT = """
You are Shai, an autonomous coding assistant working inside a local project.

Your job is to understand the project, make requested changes, and verify them.

Rules:
- Inspect relevant files before proposing changes.
- Use tools to explore the project rather than guessing.
- Read existing code and follow its conventions.
- Make focused, minimal changes.
- Never claim a file was changed unless edit_file succeeded.
- Never claim tests passed unless run_checks confirms it.
- Explain important decisions and report remaining issues.
- Do not access files outside the project directory.
- Do not attempt to read secrets, API keys, credentials or environment files.
- Never expose environment variables.
- Ask for approval before changing files.
- Do not try to bypass approval.
"""

# -------------------------------------------------------------------
# Configuration persistence
# -------------------------------------------------------------------

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(provider, model):
    config = {
        "provider": provider,
        "model": model
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# -------------------------------------------------------------------
# API helpers
# -------------------------------------------------------------------

def http_get(url, key, provider):
    if provider == "gemini":
        separator = "&" if "?" in url else "?"
        url += separator + urllib.parse.urlencode({"key": key})
        headers = {}
    elif provider == "anthropic":
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01"
        }
    else:
        headers = {
            "Authorization": "Bearer " + key
        }

    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "Model-list API error {}: {}".format(error.code, detail[:1500])
        )
    except urllib.error.URLError as error:
        raise RuntimeError("Network error while loading models: " + str(error))


def get_available_models(provider):
    key = os.environ.get(PROVIDERS[provider]["key_env"])

    if not key:
        raise RuntimeError(
            PROVIDERS[provider]["key_env"] + " is not set."
        )

    if provider == "openrouter":
        data = http_get(
            "https://openrouter.ai/api/v1/models",
            key,
            provider
        )

        models = []

        for item in data.get("data", []):
            model_id = item.get("id", "")

            if model_id.endswith(":free"):
                models.append(model_id)

        return sorted(set(models))

    if provider == "gemini":
        data = http_get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            key,
            provider
        )

        models = []

        for item in data.get("models", []):
            methods = item.get("supportedGenerationMethods", [])

            if "generateContent" not in methods:
                continue

            model_id = item.get("name", "")

            if model_id.startswith("models/"):
                model_id = model_id[len("models/"):]

            models.append(model_id)

        return sorted(set(models))

    if provider in ("anthropic", "openai", "kilo"):
        data = http_get(
            PROVIDERS[provider]["models_url"],
            key,
            provider
        )
        return sorted(set(
            item.get("id", "")
            for item in data.get("data", [])
            if item.get("id")
        ))

    raise ValueError("Unknown provider.")


def choose_model(models, preferred=None):
    if not models:
        raise RuntimeError("No available models found.")

    if preferred in models:
        return preferred

    print("\nAvailable models:\n")

    for index, model in enumerate(models, 1):
        print("{:3}. {}".format(index, model))

    while True:
        choice = input("\nChoose model number: ").strip()

        try:
            index = int(choice) - 1

            if 0 <= index < len(models):
                return models[index]

        except ValueError:
            pass

        print("Invalid selection. Try again.")


def initialize_provider():
    config = load_config()

    if PROVIDER_ARG:
        provider = PROVIDER_ARG
    else:
        provider = config.get("provider", DEFAULT_PROVIDER)

    if provider not in PROVIDERS:
        raise ValueError(
            "Invalid provider. Use: openrouter, gemini, anthropic, openai or kilo"
        )

    print("Provider:", provider)
    spinner = Spinner("Loading available models")
    spinner.start()
    try:
        models = get_available_models(provider)
    finally:
        spinner.stop()

    if not models:
        raise RuntimeError(
            "No models are currently available for " + provider + "."
        )

    saved_provider = config.get("provider")
    saved_model = config.get("model")

    # Reuse a saved model only when the saved provider matches and
    # the model is still available. On first run, after deleting the
    # config, or when the saved model is unavailable, ask the user.
    if saved_provider == provider and saved_model in models:
        model = saved_model
        print("Using saved model:", model)
    else:
        if saved_model and saved_provider == provider:
            print("\\nPreviously selected model is unavailable:")
            print(saved_model)
            print("Please choose another model.")
        else:
            print("\\nFirst-time setup: choose a model.")

        model = choose_model(models)

    # Save only after the user has selected a model successfully.
    save_config(provider, model)

    print("Selected model:", model)

    return provider, model


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
# Project tools
# -------------------------------------------------------------------

def list_files(path="."):
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

    return "\n".join(results) if results else "No files found."


def read_file(path):
    file_path = safe_path(path)

    if not file_path.is_file():
        return "File does not exist."

    if file_path.stat().st_size > MAX_FILE_SIZE:
        return "File is too large to read."

    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "File is not UTF-8 text."


def search_files(query, path="."):
    directory = safe_path(path)

    if not directory.is_dir():
        return "Not a directory."

    ignored = {
        ".git", "node_modules", "__pycache__",
        ".venv", "venv", ".idea"
    }

    results = []

    for file_path in directory.rglob("*"):
        if not file_path.is_file():
            continue

        if any(part in ignored for part in file_path.parts):
            continue

        if file_path.name.startswith(".env"):
            continue

        try:
            file_path.resolve().relative_to(ROOT)

            if file_path.stat().st_size > MAX_FILE_SIZE:
                continue

            content = file_path.read_text(encoding="utf-8")

        except (OSError, UnicodeDecodeError, ValueError):
            continue

        for number, line in enumerate(content.splitlines(), 1):
            if query.lower() in line.lower():
                results.append(
                    "{}:{}: {}".format(
                        relative_path(file_path),
                        number,
                        line.strip()[:250]
                    )
                )

                if len(results) >= 100:
                    results.append("...search results truncated...")
                    return "\n".join(results)

    return "\n".join(results) if results else "No matches found."


def git_status():
    result = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=15
    )

    return result.stdout + result.stderr


def git_diff():
    result = subprocess.run(
        ["git", "diff", "--"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=15
    )

    return result.stdout + result.stderr


# -------------------------------------------------------------------
# File editing
# -------------------------------------------------------------------

def edit_file(path, content):
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

    diff = difflib.unified_diff(
        old_content.splitlines(True),
        content.splitlines(True),
        fromfile="before/" + path,
        tofile="after/" + path
    )

    print("\n--- Proposed changes ---")
    print("".join(diff) or "(No changes)")
    print("------------------------")

    answer = input("Apply this edit? [y/N]: ").strip().lower()

    if answer not in ("y", "yes"):
        return "User declined the edit. File was not changed."

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    return "Successfully updated " + path


# -------------------------------------------------------------------
# Test execution
# -------------------------------------------------------------------

def run_checks(check, path="."):
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
    answer = input("Allow execution? [y/N]: ").strip().lower()

    if answer not in ("y", "yes"):
        return "User declined command execution."

    try:
        result = subprocess.run(
            command,
            cwd=str(directory),
            capture_output=True,
            text=True,
            timeout=120
        )

        output = result.stdout + result.stderr

        return (
            "Exit code: {}\n{}".format(result.returncode, output)
            if output else "Exit code: {}".format(result.returncode)
        )

    except subprocess.TimeoutExpired:
        return "Command timed out after 120 seconds."


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
            "description": "Read a UTF-8 text file in the project.",
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
            "name": "search_files",
            "description": "Search project files for a text string.",
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
    }
]

TOOL_DISPATCH = {
    "list_files": list_files,
    "read_file": read_file,
    "search_files": search_files,
    "git_status": git_status,
    "git_diff": git_diff,
    "edit_file": edit_file,
    "run_checks": run_checks
}


# -------------------------------------------------------------------
# API request
# -------------------------------------------------------------------

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
        body = {
            "model": MODEL,
            "messages": messages,
            "tools": FUNCTIONS,
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

    request = urllib.request.Request(
        PROVIDERS[PROVIDER]["url"],
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "API error {}: {}".format(error.code, detail[:2000])
        )

    except urllib.error.URLError as error:
        raise RuntimeError("Network error: " + str(error))


# -------------------------------------------------------------------
# Agent loop
# -------------------------------------------------------------------

def execute_tool(name, args):
    function = TOOL_DISPATCH.get(name)

    if function is None:
        return {"error": "Unknown tool: " + str(name)}

    try:
        return {"result": function(**args)}
    except Exception as error:
        return {"error": str(error)}


def ask_model(history, user_text):
    history.append({
        "role": "user",
        "content": user_text
    })

    for iteration in range(MAX_ITERATIONS):
        spinner = Spinner("Shai is thinking")
        spinner.start()

        try:
            response = api_request(history)
        finally:
            spinner.stop()

        if PROVIDERS[PROVIDER]["format"] == "anthropic":
            text, tool_uses = extract_anthropic_response(response)
            assistant_content = response.get("content", [])

            history.append({
                "role": "assistant",
                "content": assistant_content
            })

            if text:
                print("\\n" + text)

            if not tool_uses:
                if not text:
                    print("\\nNo text response returned.")
                return

            tool_results = []

            for tool_use in tool_uses:
                name = tool_use.get("name")
                args = tool_use.get("input", {})
                tool_use_id = tool_use.get("id")

                print("\\n[Shai tool] " + str(name))
                print(json.dumps(args, ensure_ascii=False)[:500])

                if not isinstance(args, dict):
                    result = {"error": "Tool arguments must be an object."}
                else:
                    result = execute_tool(name, args)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result, ensure_ascii=False)
                })

            history.append({
                "role": "user",
                "content": tool_results
            })

        else:
            choices = response.get("choices", [])

            if not choices:
                print("API returned no response.")
                return

            message = choices[0].get("message", {})
            history.append(message)

            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                content = message.get("content")

                if content:
                    print("\\n" + str(content))
                else:
                    print("\\nNo text response returned.")

                return

            for call in tool_calls:
                call_id = call.get("id")
                function_data = call.get("function", {})
                name = function_data.get("name")
                raw_args = function_data.get("arguments", "{}")

                print("\\n[Shai tool] " + str(name))

                try:
                    args = json.loads(raw_args)

                    if not isinstance(args, dict):
                        raise ValueError(
                            "Tool arguments must be an object."
                        )

                    print(json.dumps(args, ensure_ascii=False)[:500])
                    result = execute_tool(name, args)

                except Exception as error:
                    result = {"error": str(error)}

                history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result, ensure_ascii=False)
                })

    print("\\nAgent stopped: maximum tool-call iterations reached.")


# -------------------------------------------------------------------
# Interactive terminal
# -------------------------------------------------------------------

def main():
    global PROVIDER, MODEL

    PROVIDER, MODEL = initialize_provider()

    history = [{
        "role": "system",
        "content": SYSTEM_PROMPT
    }]

    print("\nShai - Coding Agent")
    print("Project:", ROOT)
    print("Provider:", PROVIDER)
    print("Model:", MODEL)
    print("\nProviders: openrouter, gemini, anthropic, openai, kilo")
    print("Commands: /help /status /files /diff /reset /exit")

    while True:
        try:
            user_text = input("\nYou: ").strip()

            if not user_text:
                continue

            if user_text == "/exit":
                print("Goodbye.")
                break

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
  /status  - Git status
  /files   - List project files
  /diff    - Show Git diff
  /reset   - Clear conversation history
  /exit    - Exit Shai

File edits and test execution require approval.
""")
                continue

            if user_text == "/status":
                print(git_status())
                continue

            if user_text == "/files":
                print(list_files())
                continue

            if user_text == "/diff":
                print(git_diff())
                continue

            if user_text == "/reset":
                history = [{
                    "role": "system",
                    "content": SYSTEM_PROMPT
                }]
                print("Conversation history cleared.")
                continue

            try:
                ask_model(history, user_text)
            except Exception as error:
                print("\nError:", error)

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break


if __name__ == "__main__":
    main()