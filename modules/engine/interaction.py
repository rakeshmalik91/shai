"""Pluggable interaction handler interface for Shai engine.

Decouples the core agent execution engine from terminal I/O (stdin/stdout),
allowing alternative user interfaces such as GUIs, web apps, or automated
test harnesses to handle confirmations, choices, and approvals without
touching CLI prompt logic.
"""
from __future__ import annotations

import logging
import sys

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

    def request_git_whitelist_scope(self, command: str, args: list[str]) -> str:
        """Prompt the user to select a whitelisting scope when Git command is blocked.

        Returns:
            '1' for exact command,
            '2' for subcommand,
            '3' for unrestricted,
            '0' to keep blocked.
        """
        raise NotImplementedError


class DefaultCliInteractionHandler(InteractionHandler):
    """Default interaction handler for terminal CLI environments."""

    def _prompt(self, message: str) -> str:
        if not getattr(sys.stdin, "isatty", lambda: False)():
            return ""
        try:
            return input(message).strip()
        except (EOFError, KeyboardInterrupt):
            from modules.engine.process_control import IterationInterrupted
            raise IterationInterrupted()

    def confirm_action(self, prompt: str, category: str | None = None, tool: str | None = None) -> bool:
        answer = self._prompt(prompt).lower()
        if answer in ("a", "all"):
            from modules.engine.configuration import load_approval_settings, save_approval_settings
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
        print("\n[Shai] os_command approval required:\n" + preview)
        answer = self._prompt("Run this exact command? [y/N]: ").lower()
        return answer in ("y", "yes")

    def request_git_whitelist_scope(self, command: str, args: list[str]) -> str:
        full_command = "git " + " ".join(args)
        print("\n[Shai] Git command blocked by the configured allowlist:")
        print("  " + full_command)
        print("\nWhitelist this command? Choose the scope:")
        print("  1. This exact command and parameters only")
        print("  2. All parameters for git {}".format(args[0] if args else "(no subcommand)"))
        print("  3. All Git commands and subcommands (unrestricted)")
        print("  0. No, keep it blocked")
        choice = self._prompt("Whitelist scope [0-3]: ")
        return choice if choice in ("1", "2", "3") else "0"


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
