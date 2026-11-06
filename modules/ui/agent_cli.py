"""Interactive command-line interface for the Shai coding agent.

Owns the terminal REPL loop, slash-command processing, ANSI styling, and user
input handling. The execution engine (modules.engine) is cleanly decoupled from
this terminal UI, allowing alternative frontends (e.g. GUI or Web UI) to embed
the engine directly.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import shlex
import sys

from modules.terminal_style import colorize
from modules.commands.registry import normalize_command
from modules.commands.shai_command import resolve_shai_command
from modules.commands.help import HELP_TEXT
from modules.model_routing import resolve_model as resolve_routed_model
from modules.engine import agent_runtime as engine
from modules.engine.interaction import (
    DefaultCliInteractionHandler,
    set_interaction_handler,
)
from modules.engine.process_control import IterationInterrupted
from modules.tools.os_command import parse_os_command_line, OSCommandRejected
from modules.tools import (
    configure_root as configure_tools_root,
    TOOL_DISPATCH_READ_ONLY,
)
from modules.provider_selection import (
    initialize_provider, get_provider_config,
)
from modules.logging_config import init_default_logging, get_logger
from modules.reasoning_handlers import display_reasoning_summary
from modules.conversation_history import ConversationHistory, input_with_history
from modules.agent_safeguards import compact_conversation_history
from modules.context_meter import (
    estimate_messages_tokens, get_context_window, format_context_status,
    log_and_display_usage
)
from modules.ui.spinner import Spinner
from modules.ui.chat_runtime import response_text

LOGGER = get_logger("shai.cli")


def main() -> int:
    """Run the interactive coding agent CLI REPL."""
    init_default_logging(engine.load_config())
    set_interaction_handler(DefaultCliInteractionHandler())

    LOGGER.info(
        "Starting Shai coding agent CLI root=%s api_timeout=%ss",
        engine.ROOT, engine.API_TIMEOUT_SECONDS
    )
    provider, model = initialize_provider(auto_select=True)
    engine.PROVIDER = provider
    engine.MODEL = model
    LOGGER.info("Selected provider=%s model=%s", engine.PROVIDER, engine.MODEL)

    engine.cleanup_old_backups()
    engine._load_task_plan()

    history_store = ConversationHistory(engine.ROOT, "agent", engine.SYSTEM_PROMPT)
    history = history_store.load()
    previous_provider = history_store.metadata().get("provider")
    if previous_provider and previous_provider != engine.PROVIDER:
        history = engine.history_for_new_provider(history)
        print("[Shai] Previous provider changed; retained text history and cleared old tool exchanges.")
    history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)

    # Clear terminal at startup
    os.system("cls" if os.name == "nt" else "clear")

    print("\n" + colorize("Shai - Coding Agent", "heading"))
    print("Project:", engine.ROOT)
    print("Provider:", engine.PROVIDER)
    print("Model:", engine.MODEL)
    print("Commands: /help /model /project /status /files /diff /reset /ask /code /todo /commit /push /exec /exit")
    print("Press Ctrl+D during an iteration to stop it and return here.")

    mode = "agent"

    while True:
        try:
            prompt_label = "\n" + colorize("[{}] >> ".format(mode), "prompt") if mode != "agent" else "\n" + colorize("You: ", "user")
            user_text = input_with_history(prompt_label, history_store).strip()

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
                old_provider, old_model = engine.PROVIDER, engine.MODEL
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

                engine.PROVIDER, engine.MODEL = new_provider, new_model
                LOGGER.info("Provider/model switched provider=%s model=%s",
                            engine.PROVIDER, engine.MODEL)
                history = engine.history_for_new_provider(history)
                history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)
                print("\n[Shai] Switched to: {} / {}".format(engine.PROVIDER, engine.MODEL))
                print("[Shai] Text conversation context retained; old tool-call records cleared for provider compatibility.")
                continue

            if user_text == "/help":
                print(HELP_TEXT)
                continue

            if user_text == "/status":
                try:
                    print(engine.git_status())
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text == "/files":
                print(engine.list_files())
                continue

            if user_text == "/diff":
                try:
                    print(engine.git_diff())
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text == "/reset":
                LOGGER.info("Conversation history reset")
                history = [{"role": "system", "content": engine.SYSTEM_PROMPT}]
                history_store.clear()
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
                engine.ROOT = new_root
                configure_tools_root(engine.ROOT)
                history_store = ConversationHistory(engine.ROOT, "agent", engine.SYSTEM_PROMPT)
                history = history_store.load()
                history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)
                print("[Shai] Project switched to: {}".format(engine.ROOT))
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
                if not engine.TASK_PLAN:
                    print("[Shai] No tasks in the current plan.")
                else:
                    print("Task plan:")
                    for item in engine.TASK_PLAN:
                        status_char = {"pending": " ", "in_progress": "~", "completed": "x", "blocked": "!"}[item["status"]]
                        print("  [{}] {}".format(status_char, item["title"]))
                continue

            if user_text == "/commit-push":
                try:
                    print(engine.git_commit_push())
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text == "/push":
                try:
                    print(engine.git_push())
                except IterationInterrupted:
                    print("\n[Shai] Command interrupted.")
                continue

            if user_text.startswith("/commit"):
                try:
                    parts = user_text.split()
                    message_only = "--message-only" in parts
                    print(engine.git_commit(message_only=message_only))
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
                    result = engine.os_command(executable, arguments)
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
                    history.append({"role": "user", "content": user_text})
                    safeguards = engine.load_config().get("agent_safeguards", {})
                    if safeguards.get("enabled", True) is not False:
                        def history_limit(key, default):
                            val = safeguards.get(key, default)
                            return default if isinstance(val, bool) or not isinstance(val, int) or val < 1 else val
                        history = compact_conversation_history(
                            history,
                            max_chars=history_limit("max_history_chars", 90000),
                            max_tool_result_chars=history_limit("max_tool_result_chars", 8000),
                            keep_recent_turns=history_limit("keep_recent_turns", 4),
                            max_recent_tool_cycles=history_limit("max_recent_tool_cycles", 6)
                        )
                    history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)
                    route = resolve_routed_model("chat", engine.ROOT, engine.PROVIDER, engine.MODEL)
                    effective_provider, effective_model = route["provider"], route["model"]
                    context_limit = get_context_window(effective_provider, effective_model, engine.load_config())
                    estimated_prompt_tokens = estimate_messages_tokens(history)
                    context_status = format_context_status(estimated_prompt_tokens, context_limit)
                    print("\n" + colorize("[Shai]", "system") + " {}".format(context_status))
                    spinner = Spinner("Shai is thinking | " + context_status)
                    spinner.start()
                    try:
                        response = engine.api_request_for_role(history, "chat")
                        log_and_display_usage(
                            LOGGER, response, get_provider_config(effective_provider)["format"],
                            estimated_prompt_tokens, context_limit
                        )
                        display_reasoning_summary(effective_provider, response)
                        answer = response_text(response, effective_provider)
                    except Exception as error:
                        LOGGER.exception("Ask mode turn failed")
                        answer = None
                        print("\n" + colorize("Error:", "error"), error)
                    finally:
                        spinner.stop()

                    if answer:
                        print("\n" + answer)
                        history.append({"role": "assistant", "content": answer})
                        history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)

                elif mode == "read-only":
                    history.append({"role": "user", "content": user_text})
                    safeguards = engine.load_config().get("agent_safeguards", {})
                    if safeguards.get("enabled", True) is not False:
                        def history_limit(key, default):
                            val = safeguards.get(key, default)
                            return default if isinstance(val, bool) or not isinstance(val, int) or val < 1 else val
                        history = compact_conversation_history(
                            history,
                            max_chars=history_limit("max_history_chars", 90000),
                            max_tool_result_chars=history_limit("max_tool_result_chars", 8000),
                            keep_recent_turns=history_limit("keep_recent_turns", 4),
                            max_recent_tool_cycles=history_limit("max_recent_tool_cycles", 6)
                        )
                    history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)
                    route = resolve_routed_model("orchestrator", engine.ROOT, engine.PROVIDER, engine.MODEL)
                    effective_provider, effective_model = route["provider"], route["model"]
                    context_limit = get_context_window(effective_provider, effective_model, engine.load_config())
                    estimated_prompt_tokens = estimate_messages_tokens(history)
                    context_status = format_context_status(estimated_prompt_tokens, context_limit)
                    print("\n" + colorize("[Shai]", "system") + " {}".format(context_status))
                    spinner = Spinner("Shai is thinking | " + context_status)
                    spinner.start()
                    try:
                        response = engine.api_request_for_role(history, "orchestrator")
                        log_and_display_usage(
                            LOGGER, response, get_provider_config(effective_provider)["format"],
                            estimated_prompt_tokens, context_limit
                        )
                        display_reasoning_summary(effective_provider, response)
                        answer, tool_calls = response_text(response, effective_provider)
                    except Exception as error:
                        LOGGER.exception("Read-only mode turn failed")
                        answer = None
                        tool_calls = []
                        print("\n" + colorize("Error:", "error"), error)
                    finally:
                        spinner.stop()

                    if tool_calls:
                        if get_provider_config(engine.PROVIDER)["format"] == "anthropic":
                            content = []
                            if answer:
                                content.append({"type": "text", "text": answer})
                            for tc in tool_calls:
                                content.append({
                                    "type": "tool_use", "id": tc["id"],
                                    "name": tc["function"]["name"],
                                    "input": json.loads(tc["function"]["arguments"])
                                })
                            history.append({"role": "assistant", "content": content})
                        else:
                            message = {"role": "assistant", "content": answer or ""}
                            if tool_calls:
                                message["tool_calls"] = tool_calls
                            history.append(message)
                        history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)

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
                            except Exception as e:
                                result = {"error": "Invalid tool arguments: " + str(e)}
                            else:
                                if name in TOOL_DISPATCH_READ_ONLY:
                                    try:
                                        result = TOOL_DISPATCH_READ_ONLY[name](**args)
                                    except Exception as e:
                                        result = {"error": str(e)}
                                else:
                                    result = {"error": "Unknown tool: " + name}

                            tool_content = "Error: " + result["error"] if isinstance(result, dict) and "error" in result else str(result)
                            tool_results.append({
                                "role": "tool", "tool_call_id": call_id, "content": tool_content
                            })

                        history.extend(tool_results)
                        history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)
                        route = resolve_routed_model("orchestrator", engine.ROOT, engine.PROVIDER, engine.MODEL)
                        effective_provider, effective_model = route["provider"], route["model"]
                        context_limit = get_context_window(effective_provider, effective_model, engine.load_config())
                        estimated_prompt_tokens = estimate_messages_tokens(history)
                        context_status = format_context_status(estimated_prompt_tokens, context_limit)
                        spinner = Spinner("Shai is thinking | " + context_status)
                        spinner.start()
                        try:
                            response = engine.api_request_for_role(history, "orchestrator")
                            log_and_display_usage(
                                LOGGER, response, get_provider_config(effective_provider)["format"],
                                estimated_prompt_tokens, context_limit
                            )
                            display_reasoning_summary(effective_provider, response)
                            answer, _ = response_text(response, effective_provider)
                        except Exception as error:
                            LOGGER.exception("Read-only follow-up failed")
                            answer = None
                            print("\n" + colorize("Error:", "error"), error)
                        finally:
                            spinner.stop()

                    if answer:
                        print("\n" + answer)
                        history.append({"role": "assistant", "content": answer})
                        history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)
                else:
                    engine.ask_model(history, user_text)
            except Exception as error:
                LOGGER.exception("Unhandled agent request error")
                print("\n" + colorize("Error:", "error"), error)
            finally:
                history_store.save(history, provider=engine.PROVIDER, model=engine.MODEL)

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
