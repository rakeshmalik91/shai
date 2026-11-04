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
import socket
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
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "shai_config.json"
LOGGER = get_logger("shai.chat")
DEFAULT_API_TIMEOUT_SECONDS = 300


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
        provider, model = initialize_provider()
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
    print("History:", history_store.path)
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
        history_store.save(history, provider=provider, model=model)
        spinner = Spinner("Shai is thinking")
        spinner.start()
        LOGGER.info("Chat turn started provider=%s model=%s user_chars=%d history_messages=%d",
                    provider, model, len(user_text), len(history))
        try:
            response = api_request(history, provider, model)
            display_reasoning_summary(provider, response)
            answer = response_text(response, provider)
            LOGGER.debug("Chat response parsed answer_chars=%d", len(answer or ""))
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