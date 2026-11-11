"""Pluggable interaction handler interface for Shai engine.

Decouples the core agent execution engine from terminal I/O (stdin/stdout),
allowing alternative user interfaces such as GUIs, web apps, or automated
test harnesses to handle confirmations, choices, and approvals without
touching CLI prompt logic.
"""
from __future__ import annotations

import logging
import sys

from modules.conversation_history import input_without_history

LOGGER = logging.getLogger("shai.engine.interaction")


class InteractionHandler:
    """Abstract interface for UI interaction delegates (CLI, GUI, Web)."""

    def confirm_action(self, prompt: str, category: str | None = None, tool: str | None = None) -> bool:
        """Confirm a standard tool or command action.

        Returns True to proceed, False to decline.
        """
        raise NotImplementedError

    def confirm_risky_action(self, prompt: str, category: str | None = None, tool: str | None = None) -> bool:
        """Confirm a high-risk or destructive operation (e.g. large truncations).

        Returns True to proceed, False to decline.
        """
        raise NotImplementedError

    def select_choice(self, prompt: str, choices: list[str], default: str | None = None) -> str:
        """Prompt the user to select from a list of options.

        Returns the selected option key or string.
        """
        raise NotImplementedError

    def confirm_os_command(self, preview: str) -> bool:
        """Prompt the user to approve execution of an allowlisted OS command."""
        raise NotImplementedError

    def request_command_whitelist(self, command: str) -> bool:
        """Ask whether to add an executable to the configured command allowlist."""
        raise NotImplementedError

    def request_os_command_permission(self, command: str, subcommand: str | None = None) -> str:
        """Return once, command, subcommand, exact, or deny."""
        raise NotImplementedError


class DefaultCliInteractionHandler(InteractionHandler):
    """Default interaction handler for terminal CLI environments."""

    def _prompt(self, message: str) -> str:
        if not getattr(sys.stdin, "isatty", lambda: False)():
            return ""
        try:
            return input_without_history(message).strip()
        except (EOFError, KeyboardInterrupt):
            from modules.engine.process_control import IterationInterrupted
            raise IterationInterrupted()

    def confirm_action(self, prompt: str, category: str | None = None, tool: str | None = None) -> bool:
        answer = self._prompt(prompt).lower()
        if answer in ("a", "all"):
            from modules.engine.agent_runtime import load_approval_settings, save_approval_settings
            settings = load_approval_settings()
            settings["all"] = True
            save_approval_settings(settings)
            return True
        return answer in ("y", "yes")

    def confirm_risky_action(self, prompt: str, category: str | None = None, tool: str | None = None) -> bool:
        answer = self._prompt(prompt).lower()
        return answer in ("y", "yes")

    def select_choice(self, prompt: str, choices: list[str], default: str | None = None) -> str:
        answer = self._prompt(prompt)
        return answer or (default or "")

    def confirm_os_command(self, preview: str) -> bool:
        # Backward-compatible confirmation hook for integrations that still use it.
        print("\n[Shai] os_command approval required:\n" + preview)
        return self._prompt("Run this exact command? [y/N]: ").lower() in ("y", "yes")

    def request_os_command_permission(self, command: str, subcommand: str | None = None) -> str:
        executable = command.split(None, 1)[0] if command.strip() else command
        print("\\n[Shai] Command approval required:")
        print("  " + command)
        print("  A) Allow once")
        print("  C) Allow command permanently ({})".format(executable))
        if subcommand:
            print("  S) Allow subcommand permanently ({} {})".format(executable, subcommand))
        print("  E) Allow exact command permanently ({})".format(command))
        print("  D) Deny")
        prompt_choices = "[A/C/S/E/D]" if subcommand else "[A/C/E/D]"
        while True:
            answer = self._prompt("Choose {}: ".format(prompt_choices)).strip().lower()
            choices = {
                "a": "once", "once": "once",
                "c": "command", "command": "command",
                "s": "subcommand", "subcommand": "subcommand",
                "e": "exact", "exact": "exact",
                "d": "deny", "deny": "deny", "": "deny",
            }
            choice = choices.get(answer)
            if choice == "subcommand" and not subcommand:
                print("A valid subcommand is not available for this invocation.")
                continue
            if choice:
                return choice
            print("Please choose one of the displayed letters.")

    def request_command_whitelist(self, command: str) -> bool:
        print("\n[Shai] Command is not in command_config.json's allowed_commands:")
        print("  " + command)
        answer = self._prompt("Allow this executable in future? [y/N]: ").lower()
        return answer in ("y", "yes")


_CURRENT_HANDLER: InteractionHandler = DefaultCliInteractionHandler()


def set_interaction_handler(handler: InteractionHandler) -> None:
    """Set the active UI interaction handler (e.g. for GUI integration)."""
    global _CURRENT_HANDLER
    if not isinstance(handler, InteractionHandler):
        raise TypeError("handler must be an instance of InteractionHandler")
    _CURRENT_HANDLER = handler
    LOGGER.info("Interaction handler updated to %s", handler.__class__.__name__)


def get_interaction_handler() -> InteractionHandler:
    """Return the active UI interaction handler."""
    return _CURRENT_HANDLER
