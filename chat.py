#!/usr/bin/env python3
"""
Shai interactive chat client.

Supports OpenRouter (free models), Gemini, Anthropic, OpenAI, and Kilo Gateway.
Uses the same shai_config.json and provider/model selection behaviour as agent.py.
Standard-library only.
"""

import json
import os
import pathlib
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "shai_config.json"

DEFAULT_PROVIDER = "openrouter"
DEFAULT_OPENROUTER_MODEL = "cohere/north-mini-code:free"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"

PROVIDERS = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "models_url": "https://openrouter.ai/api/v1/models",
        "key_env": "OPENROUTER_API_KEY",
        "format": "openai",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "key_env": "GEMINI_API_KEY",
        "format": "openai",
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "models_url": "https://api.anthropic.com/v1/models?limit=1000",
        "key_env": "ANTHROPIC_API_KEY",
        "format": "anthropic",
    },
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "models_url": "https://api.openai.com/v1/models",
        "key_env": "OPENAI_API_KEY",
        "format": "openai",
    },
    "kilo": {
        "url": "https://ai-gateway.kilo.ai/api/v1/chat/completions",
        "models_url": "https://ai-gateway.kilo.ai/api/v1/models",
        "key_env": "KILO_API_KEY",
        "format": "openai",
    },
}

SYSTEM_PROMPT = (
    "You are Shai, a helpful AI assistant. Be accurate, concise, and "
    "honest. If you are unsure, say so. You are in chat mode and do not "
    "have tools to inspect or modify local files."
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


def load_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(provider, model):
    temporary = CONFIG_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump({"provider": provider, "model": model}, handle, indent=2)
    temporary.replace(CONFIG_FILE)


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


def initialize_provider():
    config = load_config()
    requested_provider = sys.argv[1].lower() if len(sys.argv) > 1 else None
    provider = requested_provider or config.get("provider", DEFAULT_PROVIDER)

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
        raise RuntimeError("No models are currently available for " + provider)

    saved_provider = config.get("provider")
    saved_model = config.get("model")

    # Same rule as agent.py: reuse only a valid saved model for this provider.
    if saved_provider == provider and saved_model in models:
        model = saved_model
        print("Using saved model:", model)
    else:
        if saved_model and saved_provider == provider:
            print("Previously selected model is unavailable:", saved_model)
        else:
            print("First-time setup: choose a model.")
        model = choose_model(models)

    save_config(provider, model)
    print("Selected model:", model)
    return provider, model


def api_request(history, provider, model):
    key_name = PROVIDERS[provider]["key_env"]
    key = os.environ.get(key_name)
    if not key:
        raise RuntimeError(key_name + " is not set.")

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

    return request_json(
        PROVIDERS[provider]["url"], key, provider, body
    )


def response_text(response, provider):
    if PROVIDERS[provider]["format"] == "anthropic":
        text, tool_uses = extract_anthropic_response(response)
        if tool_uses:
            raise RuntimeError(
                "The selected Anthropic response requested tool use, "
                "but chat mode does not provide tools."
            )
        return text

    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError("API returned no response choices.")

    message = choices[0].get("message", {})
    content = message.get("content", "")

    if isinstance(content, str):
        return content

    # Some OpenAI-compatible providers return an array of text blocks.
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                pieces.append(block.get("text", ""))
        return "\n".join(piece for piece in pieces if piece)

    return str(content) if content else ""


def main():
    try:
        provider, model = initialize_provider()
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled.")
        return
    except Exception as error:
        print("\nStartup error:", error)
        return

    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("\nShai - Chat")
    print("Provider:", provider)
    print("Model:", model)
    print("Commands: /help /reset /status /exit")

    while True:
        try:
            user_text = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if not user_text:
            continue
        if user_text == "/exit":
            print("Goodbye.")
            break
        if user_text == "/help":
            print(
                "\nChat with Shai normally.\n"
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
            history = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("Conversation history cleared.")
            continue

        history.append({"role": "user", "content": user_text})
        spinner = Spinner("Shai is thinking")
        spinner.start()
        try:
            response = api_request(history, provider, model)
            answer = response_text(response, provider)
        except Exception as error:
            answer = None
            print("\nError:", error)
        finally:
            spinner.stop()

        if answer:
            print("\n" + answer)
            if PROVIDERS[provider]["format"] == "anthropic":
                history.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                })
            else:
                history.append({"role": "assistant", "content": answer})
        elif answer == "":
            print("\nNo text response returned.")
            history.pop()
        else:
            # Remove the failed user turn so it doesn't poison future context.
            history.pop()


if __name__ == "__main__":
    main()
