#!/usr/bin/env python3
"""Shared persistent conversation and terminal input history for Shai.

Conversation messages are stored separately by mode in .shai/history.json.
Prompt history is shared by chat and agent so Up/Down recalls prior inputs.
Only the Python standard library is required; readline is used when available.
"""

import json
import os
import pathlib
import sys
import tempfile


DEFAULT_MAX_INPUT_HISTORY = 500


class ConversationHistory:
    """Persist API conversation messages and interactive prompt history."""

    def __init__(self, project_root, mode, system_prompt,
                 filename=".shai/history.json", max_input_history=DEFAULT_MAX_INPUT_HISTORY):
        self.path = pathlib.Path(project_root).expanduser().resolve() / filename
        self.mode = str(mode)
        self.system_prompt = system_prompt
        self.max_input_history = max(1, int(max_input_history))
        self._data = self._read()
        self._configure_readline()

    def _empty_data(self):
        return {
            "version": 1,
            "conversations": {"agent": [], "chat": []},
            "conversation_meta": {"agent": {}, "chat": {}},
            "input_history": []
        }

    def _read(self):
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("history root must be an object")
        except (OSError, ValueError, TypeError):
            data = self._empty_data()

        data.setdefault("version", 1)
        conversations = data.get("conversations")
        if not isinstance(conversations, dict):
            conversations = {}
        data["conversations"] = conversations
        for name in ("agent", "chat"):
            if not isinstance(conversations.get(name), list):
                conversations[name] = []
        metadata = data.get("conversation_meta")
        if not isinstance(metadata, dict):
            metadata = {}
        data["conversation_meta"] = metadata
        for name in ("agent", "chat"):
            if not isinstance(metadata.get(name), dict):
                metadata[name] = {}
        inputs = data.get("input_history")
        if not isinstance(inputs, list):
            inputs = []
        data["input_history"] = [
            item for item in inputs if isinstance(item, str) and item.strip()
        ][-self.max_input_history:]
        return data

    def _configure_readline(self):
        """Load saved prompts into readline, enabling native Up/Down history."""
        try:
            import readline
            readline.clear_history()
            for item in self._data["input_history"]:
                readline.add_history(item)
            self._readline = readline
        except (ImportError, AttributeError):
            self._readline = None

    def _refresh(self):
        """Merge updates made by another Shai mode/process before writing."""
        self._data = self._read()

    def load(self):
        """Return this mode's saved messages, or a fresh system message."""
        self._refresh()
        messages = self._data["conversations"].get(self.mode, [])
        if not messages:
            return [{"role": "system", "content": self.system_prompt}]
        # Return a deep copy so callers cannot mutate the in-memory store
        # until save() is explicitly called.
        return json.loads(json.dumps(messages, ensure_ascii=False))

    def metadata(self):
        """Return provider metadata associated with this mode's history."""
        self._refresh()
        return dict(self._data["conversation_meta"].get(self.mode, {}))

    def save(self, messages, provider=None, model=None):
        """Persist the full provider message history for this mode."""
        if not isinstance(messages, list):
            raise TypeError("conversation history must be a list")
        # Validate JSON serializability before replacing the last good file.
        serialized = json.loads(json.dumps(messages, ensure_ascii=False))
        self._refresh()
        self._data["conversations"][self.mode] = serialized
        metadata = self._data["conversation_meta"][self.mode]
        if provider is not None:
            metadata["provider"] = str(provider)
        if model is not None:
            metadata["model"] = str(model)
        self._write()

    def clear(self):
        """Clear only the current mode's conversation, not prompt history."""
        self._refresh()
        self._data["conversations"][self.mode] = []
        self._write()

    def add_input(self, value):
        value = str(value).strip()
        if not value:
            return
        self._refresh()

        entries = self._data["input_history"]
        if value.startswith("/"):
            # input() may already have added this command to readline.
            self._remove_current_readline_line(value)
            return

        if entries and entries[-1] == value:
            # Avoid duplicate consecutive prompts in both persistent and
            # in-memory readline history.
            self._remove_current_readline_line(value)
            return

        entries.append(value)
        del entries[:-self.max_input_history]
        self._ensure_readline_line(value)
        self._write()

    def _readline_last(self):
        if self._readline is None:
            return 0, None
        try:
            length = self._readline.get_current_history_length()
            last = self._readline.get_history_item(length) if length else None
            return length, last
        except (AttributeError, RuntimeError):
            return 0, None

    def _remove_current_readline_line(self, value):
        # The standard input() implementation adds the just-entered line
        # before returning. Remove it for commands and repeated prompts.
        length, last = self._readline_last()
        if length and last == value:
            try:
                self._readline.remove_history_item(length - 1)
            except (AttributeError, RuntimeError, ValueError):
                pass

    def _ensure_readline_line(self, value):
        # If input() already inserted the line, leave it in place.
        length, last = self._readline_last()
        if last != value and self._readline is not None:
            try:
                self._readline.add_history(value)
            except (AttributeError, RuntimeError):
                pass

    def _write(self):
        """Write atomically and restrict the history file to the current user."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ignore_file = self.path.parent / ".gitignore"
        if not ignore_file.exists():
            try:
                ignore_file.write_text("*\n!.gitignore\n", encoding="utf-8")
            except OSError:
                pass
        fd, temporary = tempfile.mkstemp(
            prefix=".history-", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    self._data, handle, ensure_ascii=False, indent=2
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, str(self.path))
            try:
                os.chmod(str(self.path), 0o600)
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


def _fallback_input(prompt, history):
    """Small readline fallback for terminals where Python lacks readline."""
    import termios
    import tty

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return input(prompt)

    entries = list(history)
    original = termios.tcgetattr(sys.stdin.fileno())
    line = []
    cursor = 0
    history_index = len(entries)
    draft = []

    def redraw():
        value = "".join(line)
        sys.stdout.write("\r" + prompt + value + " ")
        sys.stdout.write("\r" + prompt + value)
        move_left = len(value) - cursor
        if move_left:
            sys.stdout.write("\x1b[{}D".format(move_left))
        sys.stdout.flush()

    try:
        tty.setraw(sys.stdin.fileno())
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            char = sys.stdin.read(1)
            if char in ("\r", "\n"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return "".join(line)
            if char == "\x03":
                raise KeyboardInterrupt
            if char == "\x04":
                if not line:
                    raise EOFError
                continue
            if char in ("\x7f", "\x08"):
                if cursor:
                    del line[cursor - 1]
                    cursor -= 1
                    redraw()
                continue
            if char == "\x1b":
                second = sys.stdin.read(1)
                third = sys.stdin.read(1) if second == "[" else ""
                if second == "[" and third == "A":  # Up
                    if entries and history_index > 0:
                        if history_index == len(entries):
                            draft = list(line)
                        history_index -= 1
                        line = list(entries[history_index])
                        cursor = len(line)
                        redraw()
                elif second == "[" and third == "B":  # Down
                    if history_index < len(entries):
                        history_index += 1
                        line = (list(draft) if history_index == len(entries)
                                else list(entries[history_index]))
                        cursor = len(line)
                        redraw()
                elif second == "[" and third == "C" and cursor < len(line):
                    cursor += 1
                    sys.stdout.write("\x1b[C")
                    sys.stdout.flush()
                elif second == "[" and third == "D" and cursor > 0:
                    cursor -= 1
                    sys.stdout.write("\x1b[D")
                    sys.stdout.flush()
                continue
            if char == "\x01":  # Ctrl+A
                cursor = 0
                redraw()
                continue
            if char == "\x05":  # Ctrl+E
                cursor = len(line)
                redraw()
                continue
            if char.isprintable():
                line.insert(cursor, char)
                cursor += 1
                redraw()
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)


def input_with_history(prompt, history_store):
    """Read a prompt, supporting persistent Up/Down message navigation."""
    if history_store._readline is None:
        value = _fallback_input(prompt, history_store._data["input_history"])
    else:
        value = input(prompt)
    history_store.add_input(value)
    return value
