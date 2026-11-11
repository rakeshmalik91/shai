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

from modules.terminal_style import colorize
from modules.commands.shai_command import resolve_shai_command
from modules.commands.registry import normalize_command
from modules.ui.spinner import Spinner
from modules.model_routing import resolve_model as resolve_routed_model
from modules.provider_selection import (
    PROVIDERS, initialize_provider, get_provider_config,
    provider_api_key, build_provider_url, build_provider_headers,
    provider_timeout, get_available_models as shared_get_available_models,
)
from modules.logging_config import init_default_logging, get_logger, log_api_payload
from modules.reasoning_handlers import display_reasoning_summary
from modules.conversation_history import ConversationHistory, input_with_history
from modules.agent_safeguards import compact_conversation_history
from modules.context_meter import (
    estimate_messages_tokens, get_context_window, format_context_status,
    log_and_display_usage
)
from modules.tools import (
    configure_root as configure_tools_root, safe_path, list_files, read_file,
    search_files, git_status, git_diff, get_file_outline, find_symbol,
    find_references, get_project_map, get_test_map, inspect_test_failure,
    TOOL_DISPATCH_READ_ONLY, FUNCTIONS_READ_ONLY,
)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[2]
ROOT = pathlib.Path(
    sys.argv[1] if len(sys.argv) > 1 else "."
).expanduser().resolve()
configure_tools_root(ROOT)
CONFIG_FILE = SCRIPT_DIR / "assets" / "shai_config.json"
DEFAULT_API_TIMEOUT_SECONDS = 300

# Define the logger before loading configuration: API_TIMEOUT_SECONDS is
# initialized at import time and load_config() may need to report bad JSON.
LOGGER = get_logger("shai.chat")


def _merge_config_section(base, extra):
    if not isinstance(base, dict) or not isinstance(extra, dict):
        return
    for k, v in extra.items():
        if k not in base:
            base[k] = v
        elif isinstance(base[k], dict) and isinstance(v, dict):
            _merge_config_section(base[k], v)


def load_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            return {}
        for filename in (
            "provider_config.json", "command_config.json",
            "model_config.json", "cli_config.json",
            "logging_config.json",
        ):
            try:
                with (CONFIG_FILE.parent / filename).open("r", encoding="utf-8") as extra:
                    section = json.load(extra)
                if isinstance(section, dict):
                    if filename == "model_config.json":
                        value.setdefault("model_config", {})
                        _merge_config_section(value["model_config"], section)
                    else:
                        _merge_config_section(value, section)
            except (OSError, ValueError, TypeError):
                pass
        return value
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
MAX_FILE_SIZE = 200_000




# Tool dispatch for read-only tools


SYSTEM_PROMPT = (
    "You are Shai, a helpful AI assistant. Be accurate, concise, and "
    "honest. If you are unsure, say so. You are in chat mode and have "
    "access to read-only tools to inspect the local project."
)




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
    config = get_provider_config(provider)
    if config.get("auth_type") == "query" and key:
        separator = "&" if "?" in url else "?"
        url += separator + urllib.parse.urlencode({
            config.get("auth_query_param", "key"): key
        })
    headers = build_provider_headers(provider, key)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

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
        with urllib.request.urlopen(request, timeout=provider_timeout(provider, API_TIMEOUT_SECONDS)) as response:
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
    """Use the shared provider registry/configuration for model discovery."""
    return shared_get_available_models(provider)


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


def api_request(history, provider, model, task_role="chat"):
    route = resolve_routed_model(task_role, ROOT, provider, model)
    provider, model = route["provider"], route["model"]
    LOGGER.info("Chat model role=%s provider=%s model=%s routed=%s reason=%s",
                task_role, provider, model, route["routed"], route["reason"])
    LOGGER.debug("Chat API request provider=%s model=%s history_messages=%d",
                 provider, model, len(history))
    key = provider_api_key(provider)

    # Determine if provider supports tools
    supports_tools = get_provider_config(provider)["format"] in ("openai", "anthropic")

    if get_provider_config(provider)["format"] == "anthropic":
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
        build_provider_url(provider, "chat"), key, provider, body
    )


def response_text(response, provider):
    if get_provider_config(provider)["format"] == "anthropic":
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

    print("\n" + colorize("Shai - Chat", "heading"))
    print("Provider:", provider)
    print("Model:", model)
    print("Commands: /help /model /project /status /reset /exit")

    while True:
        try:
            user_text = input_with_history("\n" + colorize("You: ", "user"), history_store).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if not user_text:
            continue
        natural_command = resolve_shai_command(user_text, "chat")
        if natural_command:
            print("[Shai] Interpreted as {}.".format(natural_command))
            user_text = natural_command
        user_text = normalize_command(user_text)
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
                "/model   - Switch provider and model\n"
                "/project - Switch project folder\n"
                "/reset   - Clear conversation history\n"
                "/status  - Show provider and model\n"
                "/exit    - Exit Shai"
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

        if user_text.startswith("/project "):
            new_path = user_text[len("/project "):].strip()
            if not new_path:
                print("Usage: /project <path>")
                continue
            try:
                new_root = pathlib.Path(new_path).expanduser().resolve()
                if not new_root.is_dir():
                    print("Error: '{}' is not a directory.".format(new_path))
                    continue
            except Exception as error:
                print("Error: Invalid path '{}': {}".format(new_path, error))
                continue
            global ROOT
            ROOT = new_root
            configure_tools_root(ROOT)
            history_store = ConversationHistory(ROOT, "chat", SYSTEM_PROMPT)
            history = history_store.load()
            history_store.save(history, provider=provider, model=model)
            print("Project switched to: {}".format(ROOT))
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
        route = resolve_routed_model("chat", ROOT, provider, model)
        effective_provider, effective_model = route["provider"], route["model"]
        context_limit = get_context_window(effective_provider, effective_model, load_config())
        estimated_prompt_tokens = estimate_messages_tokens(history)
        context_status = format_context_status(
            estimated_prompt_tokens, context_limit
        )
        LOGGER.debug(
            "Chat context estimate provider=%s model=%s estimated_prompt_tokens=%d "
            "context_limit=%s history_messages=%d",
            effective_provider, effective_model, estimated_prompt_tokens, context_limit, len(history)
        )
        print("\n[Shai] {}".format(context_status))
        spinner = Spinner("Shai is thinking | " + context_status)
        spinner.start()
        LOGGER.info("Chat turn started provider=%s model=%s user_chars=%d history_messages=%d",
                    provider, model, len(user_text), len(history))
        try:
            response = api_request(history, provider, model)
            log_and_display_usage(
                LOGGER, response, get_provider_config(provider)["format"],
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
            print("\n" + colorize("Error:", "error"), error)
        finally:
            spinner.stop()

        # Handle tool calls if any
        if tool_calls:
            # Add assistant message with tool calls to history
            if get_provider_config(provider)["format"] == "anthropic":
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
                    LOGGER, response, get_provider_config(provider)["format"],
                    estimated_prompt_tokens, context_limit
                )
                display_reasoning_summary(provider, response)
                answer, tool_calls = response_text(response, provider)
                LOGGER.debug("Chat response parsed answer_chars=%d tool_calls=%d",
                             len(answer or ""), len(tool_calls))
            except Exception as error:
                LOGGER.exception("Chat turn failed provider=%s model=%s", provider, model)
                answer = None
                print("\n" + colorize("Error:", "error"), error)
            finally:
                spinner.stop()

        if answer:
            LOGGER.info("Chat turn completed answer_chars=%d", len(answer))
            print("\n" + colorize(answer, "assistant"))
            if get_provider_config(provider)["format"] == "anthropic":
                history.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                })
            else:
                history.append({"role": "assistant", "content": answer})
            history_store.save(history, provider=provider, model=model)
        elif answer == "":
            print("\n" + colorize("No text response returned.", "warning"))
            history.pop()
            history_store.save(history, provider=provider, model=model)
        else:
            # Remove the failed user turn so it doesn't poison future context.
            history.pop()
            history_store.save(history, provider=provider, model=model)


if __name__ == "__main__":
    main()
