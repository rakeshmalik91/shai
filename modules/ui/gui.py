"""Tkinter desktop GUI for the Shai AI assistant.

Supports Ask mode (chat only, no tools), Read-Only mode (chat with read-only
project inspection tools), and Agent mode (full coding agent with tools),
using the shared Shai engine and pluggable InteractionHandler.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import pathlib
import queue
import re
import subprocess
import sys
import textwrap
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from modules.agent_safeguards import compact_conversation_history
from modules.commands.help import HELP_TEXT
from modules.commands.registry import normalize_command
from modules.commands.shai_command import resolve_shai_command
from modules.context_meter import (
    estimate_messages_tokens,
    format_context_status,
    get_context_window,
    log_and_display_usage,
)
from modules.conversation_history import ConversationHistory
from modules.engine import agent_runtime as engine
from modules.engine.interaction import InteractionHandler, set_interaction_handler
from modules.engine.process_control import IterationInterrupted, set_external_cancel_event
from modules.logging_config import get_logger, init_default_logging
from modules.model_routing import resolve_model as resolve_routed_model
from modules.provider_selection import (
    PROVIDERS,
    get_available_models,
    get_provider_config,
    get_saved_context_window,
    get_selected_model_details,
    initialize_provider,
    load_config as load_provider_config,
    save_config as save_provider_config,
)
from modules.reasoning_handlers import display_reasoning_summary
from modules.tools import TOOL_DISPATCH_READ_ONLY
from modules.tools.os_command import OSCommandRejected, parse_os_command_line
from modules.ui.agent_cli import display_os_command_result
from modules.ui.chat_runtime import response_text
from modules.ui.speech import (
    SpeechToTextSession,
    WakeWordSession,
    is_speech_cancel_command,
    is_speech_stop_listening_command,
    play_speech_cue,
)
from modules.ui.tray import (
    WindowsTrayIcon,
    ensure_app_icons,
    ensure_listening_icons,
    format_tray_notification,
    set_windows_app_id,
)

LOGGER = get_logger("shai.gui")

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_FENCE_RE = re.compile(r"(?m)^[ \t]*```([^\n`]*)\r?\n(.*?)^[ \t]*```[ \t]*\r?$", re.DOTALL)
_MD_INLINE_RE = re.compile(
    r"(`[^`\n]+`|\*\*[^*\n]+?\*\*|__[^_\n]+?__|\*[^*\n]+?\*|(?<!\w)_[^_\n]+?_(?!\w)|\[[^\]\n]+\]\([^\)\n]+\))"
)
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
CODE_PREVIEW_LINES = 4
CONTINUOUS_SPEECH_AUTO_SEND_MS = 300
SPEECH_ANIM_INTERVAL_MS = 350
SPEECH_PULSE_COLORS = ("#f38ba8", "#f5a3b8", "#f9c6d3", "#f5a3b8")
APP_STATE_ROOT = pathlib.Path.home()
APP_STATE_ROOT = pathlib.Path.home()
DEFAULT_TOKEN_STORE_PATH = pathlib.Path.home() / ".shai" / "tokens.json"

MODE_LABELS = {
    "ask": "Ask (Chat Only)",
    "read-only": "Read-Only (Inspect)",
    "agent": "Agent (Code & Tools)",
}
LABEL_TO_MODE = {label: mode for mode, label in MODE_LABELS.items()}

PROVIDER_LABELS = {
    "kilo": "Kilo Gateway (paid/non-free)",
    "kilo-free": "Kilo Gateway (free only)",
    "ollama": "Ollama (local)",
}

# Dark palette (Catppuccin Mocha-inspired)
THEME = {
    "bg": "#1e1e2e",
    "surface": "#181825",
    "surface_alt": "#262637",
    "border": "#313244",
    "fg": "#cdd6f4",
    "muted": "#a6adc8",
    "accent": "#89b4fa",
    "user": "#a6e3a1",
    "assistant": "#89dceb",
    "system": "#89b4fa",
    "tool": "#cba6f7",
    "warning": "#f9e2af",
    "error": "#f38ba8",
    "button_bg": "#313244",
    "button_active": "#45475a",
}

ANSI_FG_COLORS = {
    30: "#585b70",  # black / dark gray
    31: "#f38ba8",  # red
    32: "#a6e3a1",  # green
    33: "#f9e2af",  # yellow
    34: "#89b4fa",  # blue
    35: "#cba6f7",  # magenta
    36: "#89dceb",  # cyan
    37: "#cdd6f4",  # white
    90: "#6c7086",  # bright black
    91: "#f38ba8",  # bright red
    92: "#a6e3a1",  # bright green
    93: "#f9e2af",  # bright yellow
    94: "#89b4fa",  # bright blue
    95: "#f5c2e7",  # bright magenta
    96: "#94e2d5",  # bright cyan
    97: "#ffffff",  # bright white
}

ANSI_BG_COLORS = {
    40: "#11111b",
    41: "#452432",
    42: "#243b2c",
    43: "#423a26",
    44: "#24314a",
    45: "#3b2c4a",
    46: "#243d45",
    47: "#45475a",
}


def _parse_table_row(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [cell.strip() for cell in raw.split("|")]


def split_markdown_tables(text: str) -> list[tuple]:
    """Split markdown prose into ('text', str) and ('table', headers, rows) segments."""
    lines = text.splitlines(keepends=True)
    parts: list[tuple] = []
    text_buf: list[str] = []
    i = 0
    while i < len(lines):
        if (
            i + 1 < len(lines)
            and "|" in lines[i]
            and _MD_TABLE_SEP_RE.match(lines[i + 1])
        ):
            headers = _parse_table_row(lines[i])
            if headers:
                if text_buf:
                    parts.append(("text", "".join(text_buf)))
                    text_buf = []
                i += 2
                rows: list[list[str]] = []
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    row = _parse_table_row(lines[i])
                    if len(row) < len(headers):
                        row.extend([""] * (len(headers) - len(row)))
                    elif len(row) > len(headers):
                        row = row[: len(headers)]
                    rows.append(row)
                    i += 1
                parts.append(("table", headers, rows))
                continue
        text_buf.append(lines[i])
        i += 1
    if text_buf:
        parts.append(("text", "".join(text_buf)))
    return parts


def parse_markdown_inline(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Split a line of markdown into (text, extra_tags) spans."""
    if not text:
        return []
    spans: list[tuple[str, tuple[str, ...]]] = []
    pos = 0
    for match in _MD_INLINE_RE.finditer(text):
        start, end = match.span()
        if start > pos:
            spans.append((text[pos:start], ()))
        token = match.group(0)
        if token.startswith("`") and token.endswith("`") and len(token) >= 2:
            spans.append((token[1:-1], ("md_code",)))
        elif (token.startswith("**") and token.endswith("**")) or (
            token.startswith("__") and token.endswith("__")
        ):
            inner = token[2:-2]
            if "`" in inner:
                for sub_txt, sub_tags in parse_markdown_inline(inner):
                    spans.append((sub_txt, ("md_bold", *sub_tags)))
            else:
                spans.append((inner, ("md_bold",)))
        elif (token.startswith("*") and token.endswith("*")) or (
            token.startswith("_") and token.endswith("_")
        ):
            spans.append((token[1:-1], ("md_italic",)))
        elif token.startswith("[") and "](" in token and token.endswith(")"):
            label = token[1 : token.index("](")]
            spans.append((label, ("md_link",)))
        else:
            spans.append((token, ()))
        pos = end
    if pos < len(text):
        spans.append((text[pos:], ()))
    return spans


def strip_ansi(text: str) -> str:
    """Remove ANSI terminal escape sequences from captured output."""
    return _ANSI_RE.sub("", text)


def format_tool_label_for_ui(tool_label: str) -> str:
    """Format 'tool_name (json_args)' into readable key=value parameters."""
    if " (" not in tool_label or not tool_label.endswith(")"):
        return tool_label
    name, rest = tool_label.split(" (", 1)
    raw_detail = rest[:-1].strip()
    if not raw_detail.startswith("{"):
        return tool_label
    try:
        parsed = json.loads(raw_detail)
    except (ValueError, TypeError):
        return tool_label
    if not isinstance(parsed, dict):
        return tool_label
    if not parsed:
        return name

    priority = ("path", "file", "command", "query", "pattern", "message", "offset", "limit")
    ordered_keys = [k for k in priority if k in parsed] + [
        k for k in parsed if k not in priority
    ]
    parts: list[str] = []
    for key in ordered_keys:
        val = parsed[key]
        if isinstance(val, str):
            text_val = val.replace("\n", "\\n")
            if len(text_val) > 80:
                text_val = text_val[:77] + "..."
            parts.append(f"{key}={text_val}")
        elif isinstance(val, (list, dict)):
            rendered = json.dumps(val, ensure_ascii=False)
            if len(rendered) > 60:
                rendered = rendered[:57] + "..."
            parts.append(f"{key}={rendered}")
        else:
            parts.append(f"{key}={val}")
    return f"{name} ({', '.join(parts)})"


def strip_non_sgr_ansi(text: str) -> str:
    """Remove non-SGR ANSI control sequences while preserving color SGR codes."""
    def _replace(match: re.Match) -> str:
        seq = match.group(0)
        return seq if seq.endswith("m") else ""

    return _ANSI_RE.sub(_replace, text)


def parse_ansi_segments(
    text: str,
    state: dict | None = None,
) -> tuple[list[tuple[str, dict]], dict]:
    """Split text into (chunk, style_dict) segments from POSIX/ANSI SGR codes."""
    current = dict(state) if state else {
        "fg": None,
        "bg": None,
        "bold": False,
        "dim": False,
        "underline": False,
    }
    segments: list[tuple[str, dict]] = []
    pos = 0

    for match in _ANSI_SGR_RE.finditer(text):
        start, end = match.span()
        if start > pos:
            segments.append((text[pos:start], dict(current)))
        raw_codes = match.group(1)
        codes = [int(c) for c in raw_codes.split(";") if c.isdigit()] if raw_codes else [0]
        for code in codes:
            if code == 0:
                current = {"fg": None, "bg": None, "bold": False, "dim": False, "underline": False}
            elif code == 1:
                current["bold"] = True
            elif code == 2:
                current["dim"] = True
            elif code == 4:
                current["underline"] = True
            elif code == 22:
                current["bold"] = False
                current["dim"] = False
            elif code == 24:
                current["underline"] = False
            elif code == 39:
                current["fg"] = None
            elif code == 49:
                current["bg"] = None
            elif code in ANSI_FG_COLORS:
                current["fg"] = code
            elif code in ANSI_BG_COLORS:
                current["bg"] = code
        pos = end

    if pos < len(text):
        segments.append((text[pos:], dict(current)))

    return segments, current


def _split_diff_blocks(text: str) -> list[tuple]:
    """Extract unified diff blocks from plain text into ('text', str) / ('code', 'diff', lines)."""
    lines = text.splitlines(keepends=True)
    parts: list[tuple] = []
    plain_buf: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        is_diff_start = line.startswith("diff --git ") or (
            line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ ")
        )
        if not is_diff_start:
            plain_buf.append(line)
            i += 1
            continue

        if plain_buf:
            parts.append(("text", "".join(plain_buf)))
            plain_buf.clear()

        diff_lines = [line.rstrip("\r\n")]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt.startswith("diff --git "):
                break
            if nxt in ("\n", "\r\n") or nxt.startswith((
                "+++ ", "--- ", "@@ ", "+", "-", " ", "\\",
                "index ", "new file ", "deleted file ", "old mode ", "new mode ",
                "rename from ", "rename to ", "similarity index ",
            )):
                diff_lines.append(nxt.rstrip("\r\n"))
                i += 1
            else:
                break
        while diff_lines and not diff_lines[-1]:
            diff_lines.pop()
        parts.append(("code", "diff", diff_lines))

    if plain_buf:
        parts.append(("text", "".join(plain_buf)))
    return parts


def split_markdown_code_blocks(text: str) -> list[tuple]:
    """Split text into ('text', str) and ('code', lang, list[str]) segments."""
    # Extract unified diffs first so backticks inside diff hunks don't split the diff.
    if "diff --git " in text or ("\n--- " in text and "\n+++ " in text) or text.startswith("--- "):
        diff_split = _split_diff_blocks(text)
    else:
        diff_split = [("text", text)]

    final_parts: list[tuple] = []
    for part in diff_split:
        if part[0] != "text":
            final_parts.append(part)
            continue
        chunk = part[1]
        if "```" not in chunk:
            final_parts.append(("text", chunk))
            continue
        pos = 0
        for match in _FENCE_RE.finditer(chunk):
            start, end = match.span()
            if start > pos:
                final_parts.append(("text", chunk[pos:start]))
            lang = match.group(1).strip()
            lines = match.group(2).splitlines()
            final_parts.append(("code", lang, lines))
            pos = end
        if pos < len(chunk):
            final_parts.append(("text", chunk[pos:]))
    return final_parts


def get_token_env_specs() -> list[tuple[str, list[str]]]:
    """Return ordered (env_var, [providers]) pairs for all configured providers."""
    grouped: dict[str, list[str]] = {}
    for provider in PROVIDERS:
        env_name = get_provider_config(provider).get("key_env")
        if not env_name:
            continue
        grouped.setdefault(env_name, []).append(provider)
    return list(grouped.items())


def apply_token_env_vars(tokens: dict[str, str]) -> list[str]:
    """Apply token values to os.environ; empty values clear the variable.

    Returns the list of environment variable names that are currently set.
    """
    updated: list[str] = []
    for env_name, raw_value in tokens.items():
        value = (raw_value or "").strip()
        if value:
            os.environ[env_name] = value
            updated.append(env_name)
        else:
            os.environ.pop(env_name, None)
    return updated


def persist_windows_user_env_vars(tokens: dict[str, str]) -> None:
    """Persist non-empty token values to HKCU\\Environment on Windows."""
    if os.name != "nt":
        return
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        for env_name, raw_value in tokens.items():
            value = (raw_value or "").strip()
            if value:
                winreg.SetValueEx(key, env_name, 0, winreg.REG_SZ, value)
            else:
                try:
                    winreg.DeleteValue(key, env_name)
                except FileNotFoundError:
                    pass


def save_token_store(
    tokens: dict[str, str], path: pathlib.Path | None = None
) -> pathlib.Path:
    """Persist non-empty API tokens to user-local ~/.shai/tokens.json."""
    target = path or DEFAULT_TOKEN_STORE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {
        str(k): str(v).strip()
        for k, v in tokens.items()
        if str(v or "").strip()
    }
    target.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    return target


def load_windows_user_env_vars(env_names: list[str] | None = None) -> list[str]:
    """Load configured provider token env vars from HKCU\\Environment if unset."""
    if os.name != "nt":
        return []
    import winreg

    targets = env_names if env_names is not None else [name for name, _ in get_token_env_specs()]
    loaded: list[str] = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ
        ) as key:
            for env_name in targets:
                if os.environ.get(env_name):
                    continue
                try:
                    value, _ = winreg.QueryValueEx(key, env_name)
                except FileNotFoundError:
                    continue
                cleaned = str(value or "").strip()
                if cleaned:
                    os.environ[env_name] = cleaned
                    loaded.append(env_name)
    except OSError:
        return loaded
    return loaded


def load_saved_tokens_into_env(path: pathlib.Path | None = None) -> list[str]:
    """Load saved tokens from ~/.shai/tokens.json and Windows user env into os.environ."""
    loaded: list[str] = []
    target = path or DEFAULT_TOKEN_STORE_PATH
    if target.is_file():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            for env_name, raw_value in data.items():
                value = str(raw_value or "").strip()
                if value and not os.environ.get(env_name):
                    os.environ[env_name] = value
                    loaded.append(env_name)

    for env_name in load_windows_user_env_vars():
        if env_name not in loaded:
            loaded.append(env_name)
    return loaded


def default_window_geometry(screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """Return default (width, height, x, y) anchored at the bottom-right corner."""
    width = max(420, int(screen_w * 0.30))
    height = max(420, int(screen_h * 0.80))
    x = max(0, screen_w - width)
    y = max(0, screen_h - height)
    return width, height, x, y


def gui_state_path(project_root: pathlib.Path | str) -> pathlib.Path:
    """Return the path to the GUI state file."""
    return pathlib.Path(project_root) / ".shai" / "app_state.json"


def load_gui_state(project_root: pathlib.Path | str) -> dict:
    """Load persisted GUI state from <project_root>/.shai/gui_state.json."""
    target = gui_state_path(project_root)
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_gui_state(project_root: pathlib.Path | str, state: dict) -> pathlib.Path:
    """Persist GUI window geometry and preferences to .shai/gui_state.json."""
    target = gui_state_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return target


class QueueTextStream:
    """Thread-safe text stream that forwards stdout/stderr to a UI queue.

    Essential for `.pyw` / `pythonw.exe` on Windows where `sys.stdout` and
    `sys.stderr` are `None` by default.
    """

    supports_ansi_colors = True

    def __init__(self, event_queue: queue.Queue, tag: str = "system"):
        self._queue = event_queue
        self._tag = tag

    def write(self, data: str) -> int:
        if not data:
            return 0
        cleaned = strip_non_sgr_ansi(str(data))
        if not cleaned:
            return len(data)

        if "\r" not in cleaned:
            self._queue.put(("output", cleaned, self._tag))
            return len(data)

        # Handle carriage-return progress/spinner updates without polluting the transcript.
        parts = cleaned.split("\n")
        for line in parts[:-1]:
            resolved = line.rsplit("\r", 1)[-1]
            if strip_ansi(resolved).strip():
                self._queue.put(("output", resolved + "\n", self._tag))

        tail = parts[-1]
        if "\r" in tail:
            progress = strip_ansi(tail.rsplit("\r", 1)[-1]).strip()
            self._queue.put(("progress", progress, None))
        elif tail:
            self._queue.put(("output", tail, self._tag))
        return len(data)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


class GuiInteractionHandler(InteractionHandler):
    """Thread-safe InteractionHandler that prompts via Tkinter modal dialogs."""

    def __init__(self, ui_call):
        """ui_call(fn) executes fn on the Tk main thread and returns its result."""
        self._ui_call = ui_call

    def confirm_action(
        self, prompt: str, category: str | None = None, tool: str | None = None
    ) -> bool:
        def _ask():
            dialog = _ChoiceDialog(
                title="ShAI — Confirm Action",
                message=prompt.strip(),
                buttons=[
                    ("Yes", "yes"),
                    ("Yes to All", "all"),
                    ("No", "no"),
                ],
                default="no",
            )
            return dialog.result

        answer = self._ui_call(_ask)
        if answer == "all":
            settings = engine.load_approval_settings()
            settings["all"] = True
            engine.save_approval_settings(settings)
            return True
        return answer == "yes"

    def confirm_risky_action(
        self, prompt: str, category: str | None = None, tool: str | None = None
    ) -> bool:
        def _ask():
            return messagebox.askyesno("ShAI — Risky Action Confirmation", prompt.strip())

        return bool(self._ui_call(_ask))

    def select_choice(
        self, prompt: str, choices: list[str], default: str | None = None
    ) -> str:
        def _ask():
            buttons = [(c, c) for c in choices] if choices else [("OK", default or "")]
            dialog = _ChoiceDialog(
                title="ShAI — Select Option",
                message=prompt.strip(),
                buttons=buttons,
                default=default,
            )
            return dialog.result or (default or "")

        return self._ui_call(_ask) or "deny"

    def confirm_os_command(self, preview: str) -> bool:
        def _ask():
            return messagebox.askyesno(
                "ShAI — OS Command Approval",
                f"Run this exact command?\n\n{preview}",
            )

        return bool(self._ui_call(_ask))

    def request_os_command_permission(
        self, command: str, subcommand: str | None = None
    ) -> str:
        executable = command.split(None, 1)[0] if command.strip() else command

        def _ask():
            buttons = [
                ("Allow once", "once"),
                (f"Allow command ({executable})", "command"),
            ]
            if subcommand:
                buttons.append((f"Allow subcommand ({executable} {subcommand})", "subcommand"))
            buttons.extend([
                ("Allow exact command", "exact"),
                ("Deny", "deny"),
            ])
            dialog = _ChoiceDialog(
                title="ShAI — Command Permission",
                message=f"Command approval required:\n\n{command}",
                buttons=buttons,
                default="deny",
            )
            return dialog.result or "deny"

        return self._ui_call(_ask) or "deny"

    def request_command_whitelist(self, command: str) -> bool:
        def _ask():
            return messagebox.askyesno(
                "ShAI — Allowlist Command",
                f"Command is not in command_config.json's allowed_commands:\n\n{command}\n\n"
                "Allow this executable in future?",
            )

        return bool(self._ui_call(_ask))


class _ChoiceDialog(tk.Toplevel):
    """Simple modal button-choice dialog."""

    def __init__(self, title: str, message: str, buttons: list[tuple[str, str]], default=None):
        super().__init__()
        self.title(title)
        self.configure(bg=THEME["bg"])
        self.resizable(False, False)
        self.result = default

        frame = tk.Frame(self, bg=THEME["bg"], padx=16, pady=14)
        frame.pack(fill="both", expand=True)

        msg = tk.Label(
            frame,
            text=message,
            bg=THEME["bg"],
            fg=THEME["fg"],
            justify="left",
            wraplength=460,
            font=("Segoe UI", 10),
        )
        msg.pack(anchor="w", pady=(0, 12))

        btn_frame = tk.Frame(frame, bg=THEME["bg"])
        btn_frame.pack(fill="x")
        for label, value in buttons:
            btn = tk.Button(
                btn_frame,
                cursor="hand2",
                text=label,
                command=lambda v=value: self._choose(v),
                bg=THEME["button_bg"],
                fg=THEME["fg"],
                activebackground=THEME["button_active"],
                activeforeground=THEME["fg"],
                relief="flat",
                padx=10,
                pady=5,
                font=("Segoe UI", 9),
            )
            btn.pack(side="left", padx=(0, 6))

        self.protocol("WM_DELETE_WINDOW", lambda: self._choose(default))
        self.grab_set()
        self.transient()
        self.wait_window(self)

    def _choose(self, value):
        self.result = value
        self.destroy()


class ModelPickerDialog(tk.Toplevel):
    """Dialog to choose a provider and model without blocking the UI."""

    def __init__(self, parent, current_provider: str, current_model: str):
        super().__init__(parent)
        self.title("ShAI — Select Provider & Model")
        self.configure(bg=THEME["bg"])
        self.geometry("540x480")
        self.minsize(440, 380)
        self.selected: tuple[str, str] | None = None
        self._all_models: list[str] = []

        container = tk.Frame(self, bg=THEME["bg"], padx=14, pady=12)
        container.pack(fill="both", expand=True)

        # Provider row
        prov_row = tk.Frame(container, bg=THEME["bg"])
        prov_row.pack(fill="x", pady=(0, 8))
        tk.Label(
            prov_row, text="Provider:", bg=THEME["bg"], fg=THEME["fg"], font=("Segoe UI", 10)
        ).pack(side="left")

        self.provider_var = tk.StringVar(
            value=current_provider if current_provider in PROVIDERS else list(PROVIDERS)[0]
        )
        provider_combo = ttk.Combobox(
            prov_row,
            textvariable=self.provider_var,
            values=list(PROVIDERS),
            state="readonly",
            width=22,
        )
        provider_combo.pack(side="left", padx=8)

        self.load_btn = tk.Button(
            prov_row,
            cursor="hand2",
            text="Load Models",
            command=self._load_models,
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            activebackground=THEME["button_active"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=10,
            pady=4,
        )
        self.load_btn.pack(side="left")

        self.status_label = tk.Label(
            prov_row, text="", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9)
        )
        self.status_label.pack(side="left", padx=8)

        # Filter row
        filter_row = tk.Frame(container, bg=THEME["bg"])
        filter_row.pack(fill="x", pady=(0, 6))
        tk.Label(
            filter_row, text="Filter:", bg=THEME["bg"], fg=THEME["fg"], font=("Segoe UI", 9)
        ).pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        filter_entry = tk.Entry(
            filter_row,
            textvariable=self.filter_var,
            bg=THEME["surface"],
            fg=THEME["fg"],
            insertbackground=THEME["fg"],
            relief="flat",
        )
        filter_entry.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=4)

        # Models listbox
        list_frame = tk.Frame(container, bg=THEME["surface"])
        list_frame.pack(fill="both", expand=True, pady=(0, 8))
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", style="ShAI.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            bg=THEME["surface"],
            fg=THEME["fg"],
            selectbackground=THEME["accent"],
            selectforeground="#11111b",
            relief="flat",
            font=("Consolas", 10),
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._confirm())

        # Manual model entry
        manual_row = tk.Frame(container, bg=THEME["bg"])
        manual_row.pack(fill="x", pady=(0, 10))
        tk.Label(
            manual_row, text="Model ID:", bg=THEME["bg"], fg=THEME["fg"], font=("Segoe UI", 9)
        ).pack(side="left")
        self.model_var = tk.StringVar(value=current_model or "")
        model_entry = tk.Entry(
            manual_row,
            textvariable=self.model_var,
            bg=THEME["surface"],
            fg=THEME["fg"],
            insertbackground=THEME["fg"],
            relief="flat",
            font=("Consolas", 10),
        )
        model_entry.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=4)

        # Actions
        action_row = tk.Frame(container, bg=THEME["bg"])
        action_row.pack(fill="x")
        tk.Button(
            action_row,
            cursor="hand2",
            text="Cancel",
            command=self.destroy,
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            relief="flat",
            padx=12,
            pady=5,
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            action_row,
            cursor="hand2",
            text="Use Selected",
            command=self._confirm,
            bg=THEME["accent"],
            fg="#11111b",
            relief="flat",
            padx=12,
            pady=5,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")

        self.transient(parent)
        self.grab_set()
        center_window(self, parent, width=540, height=480)
        self._load_models()

    def _load_models(self):
        provider = self.provider_var.get()
        self.load_btn.config(state="disabled")
        self.status_label.config(text="Loading...")
        self.listbox.delete(0, tk.END)

        def _worker():
            try:
                models = get_available_models(provider)
                error = None
            except Exception as exc:
                models = []
                error = str(exc)
            self.after(0, lambda: self._on_models_loaded(models, error))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_models_loaded(self, models: list[str], error: str | None):
        if not self.winfo_exists():
            return
        self.load_btn.config(state="normal")
        if error:
            self.status_label.config(text=f"Error: {error[:45]}")
            self._all_models = []
            return
        self._all_models = models
        self.status_label.config(text=f"{len(models)} models")
        self._apply_filter()

    def _apply_filter(self):
        needle = self.filter_var.get().strip().lower()
        self.listbox.delete(0, tk.END)
        for item in self._all_models:
            if not needle or needle in item.lower():
                self.listbox.insert(tk.END, item)

    def _on_listbox_select(self, _event=None):
        sel = self.listbox.curselection()
        if sel:
            self.model_var.set(self.listbox.get(sel[0]))

    def _confirm(self):
        provider = self.provider_var.get().strip()
        model = self.model_var.get().strip()
        if not provider or not model:
            messagebox.showwarning("ShAI", "Please select or enter a model ID.", parent=self)
            return
        self.selected = (provider, model)
        self.destroy()


def center_window(
    window: tk.Misc,
    parent: tk.Misc | None = None,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """Center a Tk window over its parent (or screen if parent is unmapped)."""
    window.update_idletasks()
    win_w = width or window.winfo_width() or window.winfo_reqwidth()
    win_h = height or window.winfo_height() or window.winfo_reqheight()

    if parent is not None and parent.winfo_viewable():
        parent.update_idletasks()
        base_x = parent.winfo_rootx()
        base_y = parent.winfo_rooty()
        base_w = parent.winfo_width()
        base_h = parent.winfo_height()
    else:
        base_x = 0
        base_y = 0
        base_w = window.winfo_screenwidth()
        base_h = window.winfo_screenheight()

    x = max(0, base_x + (base_w - win_w) // 2)
    y = max(0, base_y + (base_h - win_h) // 2)
    window.geometry(f"{win_w}x{win_h}+{x}+{y}")
    window.update_idletasks()


class ToolTip:
    """Lightweight hover tooltip for Tkinter widgets."""

    def __init__(self, widget: tk.Misc, text: str, delay_ms: int = 400):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip_window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self.show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    @staticmethod
    def _work_area(widget: tk.Misc) -> tuple[int, int, int, int]:
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                rect = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                    return rect.left, rect.top, rect.right, rect.bottom
            except Exception:
                pass
        return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()

    def show(self) -> None:
        if self._tip_window is not None or not self.text:
            return
        try:
            wx = self.widget.winfo_rootx()
            wy = self.widget.winfo_rooty()
            wh = self.widget.winfo_height()
            work_left, work_top, work_right, work_bottom = self._work_area(self.widget)
        except tk.TclError:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        try:
            tip.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            tip,
            text=self.text,
            bg=THEME["surface_alt"],
            fg=THEME["fg"],
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=3,
            font=("Segoe UI", 8),
        )
        label.pack()
        tip.update_idletasks()
        tip_w = tip.winfo_reqwidth()
        tip_h = tip.winfo_reqheight()

        x = wx + 10
        if x + tip_w > work_right - 4:
            x = max(work_left + 4, work_right - tip_w - 4)
        x = max(work_left + 4, x)

        y = wy + wh + 4
        if y + tip_h > work_bottom - 4:
            y_above = wy - tip_h - 4
            y = y_above if y_above >= work_top + 4 else max(work_top + 4, work_bottom - tip_h - 4)

        tip.wm_geometry(f"+{x}+{y}")
        self._tip_window = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip_window is not None:
            try:
                self._tip_window.destroy()
            except tk.TclError:
                pass
            self._tip_window = None


class SettingsDialog(tk.Toplevel):
    """Dialog to configure GUI preferences and provider API tokens."""

    def __init__(
        self,
        parent,
        gui_settings: dict | None = None,
        geometry_state: dict | None = None,
    ):
        super().__init__(parent)
        self.title("ShAI — Settings")
        self.configure(bg=THEME["bg"])
        self.geometry("600x380")
        self.minsize(500, 320)
        self.saved_vars: list[str] | None = None
        self.saved_gui_settings: dict | None = None
        self.saved_geometry: dict | None = None
        cfg = gui_settings or {}

        outer = tk.Frame(self, bg=THEME["bg"])
        outer.pack(fill="both", expand=True)

        body = tk.Frame(outer, bg=THEME["bg"])
        body.pack(fill="both", expand=True)

        # Left-hand tab sidebar
        sidebar = tk.Frame(body, bg=THEME["surface"], width=128)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self._content_frame = tk.Frame(body, bg=THEME["bg"], padx=16, pady=14)
        self._content_frame.pack(side="left", fill="both", expand=True)

        self._tab_buttons: dict[str, tk.Button] = {}
        self._tab_frames: dict[str, tk.Frame] = {}
        self._active_tab = "gui"

        for tab_key, tab_label in (("gui", "GUI"), ("tokens", "Tokens")):
            btn = tk.Button(
                sidebar,
                cursor="hand2",
                text=tab_label,
                command=lambda k=tab_key: self._select_tab(k),
                bg=THEME["surface"],
                fg=THEME["muted"],
                activebackground=THEME["surface_alt"],
                activeforeground=THEME["fg"],
                relief="flat",
                anchor="w",
                padx=14,
                pady=10,
                font=("Segoe UI", 9, "bold"),
            )
            btn.pack(fill="x")
            self._tab_buttons[tab_key] = btn

        # Tab 1: GUI preferences
        gui_tab = tk.Frame(self._content_frame, bg=THEME["bg"])
        self._tab_frames["gui"] = gui_tab

        tk.Label(
            gui_tab,
            text="GUI Preferences",
            bg=THEME["bg"],
            fg=THEME["fg"],
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 12))

        self._always_on_top_var = tk.BooleanVar(value=bool(cfg.get("always_on_top", False)))
        self._start_minimized_var = tk.BooleanVar(value=bool(cfg.get("start_minimized", False)))
        self._minimize_to_tray_var = tk.BooleanVar(value=bool(cfg.get("minimize_to_tray", True)))
        self._close_to_tray_var = tk.BooleanVar(value=bool(cfg.get("close_to_tray", True)))
        self._minimize_on_focus_loss_var = tk.BooleanVar(
            value=bool(cfg.get("minimize_on_focus_loss", False))
        )
        self._sound_cues_var = tk.BooleanVar(value=bool(cfg.get("sound_cues", True)))
        self._sound_cue_success_var = tk.BooleanVar(value=bool(cfg.get("sound_cue_success", True)))
        self._sound_cue_error_var = tk.BooleanVar(value=bool(cfg.get("sound_cue_error", True)))
        self._disable_listening_var = tk.BooleanVar(value=bool(cfg.get("disable_listening", False)))

        for label_text, var in (
            ("Always on top", self._always_on_top_var),
            ("Start minimized", self._start_minimized_var),
            ("Minimize to system tray", self._minimize_to_tray_var),
            ("Close to system tray", self._close_to_tray_var),
            ("Minimize to system tray when out of focus", self._minimize_on_focus_loss_var),
            ("Play sound on listen start/stop/send", self._sound_cues_var),
            ("Play sound on task complete (success)", self._sound_cue_success_var),
            ("Play sound on task error", self._sound_cue_error_var),
            ("Disable listening (speech input)", self._disable_listening_var),
        ):
            tk.Checkbutton(
                gui_tab,
                cursor="hand2",
                text=label_text,
                variable=var,
                bg=THEME["bg"],
                fg=THEME["fg"],
                selectcolor=THEME["surface"],
                activebackground=THEME["bg"],
                activeforeground=THEME["fg"],
                anchor="w",
                font=("Segoe UI", 9),
            ).pack(fill="x", pady=4)

        # Tab 2: API tokens
        tokens_tab = tk.Frame(self._content_frame, bg=THEME["bg"])
        self._tab_frames["tokens"] = tokens_tab

        tk.Label(
            tokens_tab,
            text="Set provider API tokens (applied to process environment variables):",
            bg=THEME["bg"],
            fg=THEME["fg"],
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        self._vars: dict[str, tk.StringVar] = {}
        self._initial_tokens: dict[str, str] = {}
        self._entries: list[tk.Entry] = []

        form = tk.Frame(tokens_tab, bg=THEME["bg"])
        form.pack(fill="both", expand=True)

        for row_idx, (env_name, providers) in enumerate(get_token_env_specs()):
            label_text = f"{env_name}\n({', '.join(providers)})"
            tk.Label(
                form,
                text=label_text,
                bg=THEME["bg"],
                fg=THEME["muted"],
                font=("Segoe UI", 8),
                justify="left",
                anchor="w",
            ).grid(row=row_idx, column=0, sticky="w", padx=(0, 10), pady=5)

            initial_val = os.environ.get(env_name, "")
            self._initial_tokens[env_name] = initial_val
            var = tk.StringVar(value=initial_val)
            self._vars[env_name] = var
            entry = tk.Entry(
                form,
                textvariable=var,
                show="*",
                bg=THEME["surface"],
                fg=THEME["fg"],
                insertbackground=THEME["fg"],
                relief="flat",
                font=("Consolas", 10),
            )
            entry.grid(row=row_idx, column=1, sticky="ew", pady=5, ipady=4)
            self._entries.append(entry)

        form.columnconfigure(1, weight=1)

        options_row = tk.Frame(tokens_tab, bg=THEME["bg"])
        options_row.pack(fill="x", pady=(8, 4))

        self._show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            options_row,
            cursor="hand2",
            text="Show tokens",
            variable=self._show_var,
            command=self._toggle_visibility,
            bg=THEME["bg"],
            fg=THEME["fg"],
            selectcolor=THEME["surface"],
            activebackground=THEME["bg"],
            activeforeground=THEME["fg"],
            font=("Segoe UI", 9),
        ).pack(side="left")

        self._persist_var = tk.BooleanVar(value=(os.name == "nt"))
        if os.name == "nt":
            tk.Checkbutton(
                options_row,
                cursor="hand2",
                text="Also save to Windows user environment",
                variable=self._persist_var,
                bg=THEME["bg"],
                fg=THEME["fg"],
                selectcolor=THEME["surface"],
                activebackground=THEME["bg"],
                activeforeground=THEME["fg"],
                font=("Segoe UI", 9),
            ).pack(side="left", padx=(14, 0))

        action_row = tk.Frame(outer, bg=THEME["surface"], padx=14, pady=10)
        action_row.pack(fill="x", side="bottom")
        tk.Button(
            action_row,
            cursor="hand2",
            text="Cancel",
            command=self._on_cancel,
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            relief="flat",
            padx=12,
            pady=5,
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            action_row,
            cursor="hand2",
            text="Save",
            command=self._save,
            bg=THEME["accent"],
            fg="#11111b",
            relief="flat",
            padx=14,
            pady=5,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")

        self._select_tab("gui")
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Return>", lambda _e: self._save())
        if parent is not None and parent.winfo_viewable():
            self.transient(parent)
        self.grab_set()
        if not self._restore_geometry(geometry_state):
            center_window(self, width=600, height=380)
        self.lift()
        self.focus_force()

    def _restore_geometry(self, geometry_state: dict | None) -> bool:
        if not isinstance(geometry_state, dict):
            return False
        w = geometry_state.get("width")
        h = geometry_state.get("height")
        x = geometry_state.get("x")
        y = geometry_state.get("y")
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        if (
            isinstance(w, int) and not isinstance(w, bool) and w >= 500
            and isinstance(h, int) and not isinstance(h, bool) and h >= 320
            and isinstance(x, int) and not isinstance(x, bool) and 0 <= x < screen_w
            and isinstance(y, int) and not isinstance(y, bool) and 0 <= y < screen_h
        ):
            self.geometry(f"{w}x{h}+{x}+{y}")
            return True
        return False

    def _capture_geometry(self):
        try:
            self.update_idletasks()
            w = self.winfo_width()
            h = self.winfo_height()
            x = self.winfo_x()
            y = self.winfo_y()
            if w >= 500 and h >= 320:
                self.saved_geometry = {"width": w, "height": h, "x": x, "y": y}
        except tk.TclError:
            pass

    def _on_cancel(self):
        self._capture_geometry()
        self.destroy()

    def _select_tab(self, tab_key: str):
        self._active_tab = tab_key
        for key, frame in self._tab_frames.items():
            if key == tab_key:
                frame.pack(fill="both", expand=True)
                self._tab_buttons[key].config(bg=THEME["surface_alt"], fg=THEME["accent"])
            else:
                frame.pack_forget()
                self._tab_buttons[key].config(bg=THEME["surface"], fg=THEME["muted"])

    def _toggle_visibility(self):
        mask = "" if self._show_var.get() else "*"
        for entry in self._entries:
            entry.config(show=mask)

    def _save(self):
        self._capture_geometry()
        self.saved_gui_settings = {
            "always_on_top": bool(self._always_on_top_var.get()),
            "start_minimized": bool(self._start_minimized_var.get()),
            "minimize_to_tray": bool(self._minimize_to_tray_var.get()),
            "close_to_tray": bool(self._close_to_tray_var.get()),
            "minimize_on_focus_loss": bool(self._minimize_on_focus_loss_var.get()),
            "sound_cues": bool(self._sound_cues_var.get()),
            "sound_cue_success": bool(self._sound_cue_success_var.get()),
            "sound_cue_error": bool(self._sound_cue_error_var.get()),
            "disable_listening": bool(self._disable_listening_var.get()),
        }
        tokens = {env_name: var.get() for env_name, var in self._vars.items()}
        if tokens != self._initial_tokens:
            self.saved_vars = apply_token_env_vars(tokens)
            try:
                save_token_store(tokens)
            except OSError as exc:
                LOGGER.warning("Could not save token store: %s", exc)
            if os.name == "nt" and self._persist_var.get():
                try:
                    persist_windows_user_env_vars(tokens)
                except OSError as exc:
                    messagebox.showwarning(
                        "ShAI — Settings",
                        f"Saved to current session, but could not persist to Windows user environment:\n{exc}",
                        parent=self,
                    )
        self.destroy()


class ShaiGuiApp:
    """Main Tkinter desktop application for Shai."""

    def __init__(
        self,
        root: tk.Tk,
        initial_project: str | None = None,
        enable_tray: bool = True,
        preload_models: bool = True,
        state_root: pathlib.Path | str | None = None,
        enable_wake_word: bool = True,
    ):
        self.root = root
        self.root.title("ShAI — AI Coding Assistant")
        self.root.minsize(420, 420)
        self.root.configure(bg=THEME["bg"])
        self._state_root = pathlib.Path(state_root) if state_root else APP_STATE_ROOT
        self._tray: WindowsTrayIcon | None = None
        self._icon_photo = None

        self._event_queue: queue.Queue = queue.Queue()
        self._drag_scroll_y: int | None = None
        self._queued_messages: list[str] = []
        self._busy = False
        self._cancel_event = threading.Event()
        self._has_progress = False
        self._ansi_state: dict | None = None
        self._tool_batch_seq = 0
        self._active_tool_batch: dict | None = None
        self._tool_batches: dict[int, dict] = {}
        self._code_block_seq = 0
        self._code_blocks: dict[int, dict] = {}
        self._table_blocks: list[dict] = []
        self._last_bubble_role: str | None = None
        self._last_task_snapshot: list[tuple[str, str]] | None = None
        self._last_context_key: tuple | None = None
        self._mode = "agent"
        self._input_history: list[str] = []
        self._input_history_idx = 0
        self._speech_session: SpeechToTextSession | None = None
        self._speech_mode: str | None = None
        self._speech_auto_send_after_id: str | None = None
        self._speech_pending_auto_send = False
        self._speech_anim_after_id: str | None = None
        self._speech_anim_step = 0
        self._restore_listening = False
        self._always_on_top = False
        self._start_minimized = False
        self._minimize_to_tray = True
        self._close_to_tray = True
        self._minimize_on_focus_loss = False
        self._sound_cues = True
        self._sound_cue_success = True
        self._sound_cue_error = True
        self._disable_listening = False
        self._turn_had_error = False
        self._settings_geometry: dict | None = None
        self._wake_word_enabled = bool(enable_wake_word) and sys.platform == "win32"
        self._wake_session: WakeWordSession | None = None

        # Redirect stdout/stderr so print() and pythonw.exe work seamlessly.
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = QueueTextStream(self._event_queue, "system")
        sys.stderr = QueueTextStream(self._event_queue, "error")

        init_default_logging(engine.load_config())
        set_interaction_handler(GuiInteractionHandler(self._run_on_ui_thread))
        set_external_cancel_event(self._cancel_event)
        engine.STATUS.on_tool_event = self._on_tool_event
        load_saved_tokens_into_env()

        if initial_project:
            try:
                target = pathlib.Path(initial_project).expanduser().resolve()
                if target.is_dir():
                    engine.set_project_root(target)
            except Exception as exc:
                LOGGER.warning("Could not switch to initial project %s: %s", initial_project, exc)

        self._restore_gui_state()
        self._apply_app_icon(enable_tray=enable_tray)

        self._init_provider_state()
        engine.cleanup_old_backups()
        engine._load_task_plan()

        self.history_store = ConversationHistory(engine.ROOT, "agent", engine.SYSTEM_PROMPT)
        self.history = self.history_store.load()
        previous_provider = self.history_store.metadata().get("provider")
        if previous_provider and previous_provider != engine.PROVIDER and engine.PROVIDER != "unknown":
            self.history = engine.history_for_new_provider(self.history)
        if engine.PROVIDER != "unknown" and engine.MODEL:
            self.history_store.save(self.history, provider=engine.PROVIDER, model=engine.MODEL)

        self._build_ui()
        self._sync_input_history()
        self._refresh_header()
        self._append_output(
            f"ShAI GUI ready.\nProject: {engine.ROOT}\n"
            f"Provider: {engine.PROVIDER} | Model: {engine.MODEL or '(not set)'}\n"
            "Switch between Ask (chat only), Read-Only, and Agent (tools) above.\n"
            "Type /help for slash commands. Enter sends; Shift+Enter inserts a newline.\n",
            "system",
        )
        self._render_loaded_history()
        if preload_models and engine.PROVIDER in PROVIDERS:
            self._load_models_for_provider_async(
                engine.PROVIDER, auto_select_first=False, quiet=True
            )

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", self._on_window_unmap)
        self.root.bind("<FocusOut>", self._on_window_focus_out)
        for target in (self.root, self.input_box):
            target.bind("<Control-m>", self._on_mic_cont_shortcut)
            target.bind("<Control-M>", self._on_mic_cont_shortcut)
        self.root.after(50, self._poll_events)
        self.root.after_idle(self._focus_input)
        self._set_speech_state(self._speech_mode)
        if not self._disable_listening:
            if self._restore_listening:
                self.root.after_idle(lambda: self.start_speech_listening(continuous=True))
            elif self._wake_word_enabled:
                self.root.after_idle(self._start_wake_word_listening)

    def _init_provider_state(self):
        saved = load_provider_config()
        saved_provider = saved.get("provider")
        saved_model = saved.get("model")
        if saved_provider in PROVIDERS and saved_model:
            provider, model = initialize_provider(auto_select=True)
            engine.PROVIDER = provider
            engine.MODEL = model
        else:
            engine.PROVIDER = saved_provider if saved_provider in PROVIDERS else "unknown"
            engine.MODEL = saved_model or ""

    def _run_on_ui_thread(self, fn):
        """Execute fn on the Tkinter main thread from a worker thread and wait."""
        if threading.current_thread() is threading.main_thread():
            return fn()
        done = threading.Event()
        box = {}

        def _invoke():
            try:
                box["value"] = fn()
            except Exception as exc:
                box["error"] = exc
            finally:
                done.set()

        self._event_queue.put(("call", _invoke, None))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=THEME["surface_alt"],
            background=THEME["button_bg"],
            foreground=THEME["fg"],
            arrowcolor=THEME["fg"],
            bordercolor=THEME["border"],
            lightcolor=THEME["surface_alt"],
            darkcolor=THEME["surface_alt"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", THEME["surface_alt"]), ("disabled", THEME["surface"])],
            background=[("readonly", THEME["button_bg"]), ("active", THEME["button_active"])],
            foreground=[("readonly", THEME["fg"]), ("disabled", THEME["muted"])],
            selectbackground=[("readonly", THEME["surface_alt"])],
            selectforeground=[("readonly", THEME["fg"])],
        )
        self.root.option_add("*TCombobox*Listbox.background", THEME["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", THEME["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", THEME["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#11111b")
        style.configure(
            "Shai.Horizontal.TProgressbar",
            troughcolor=THEME["surface"],
            background=THEME["accent"],
            bordercolor=THEME["border"],
            lightcolor=THEME["accent"],
            darkcolor=THEME["accent"],
            thickness=6,
        )
        style.layout(
            "ShAI.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [
                            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
                        ],
                    },
                )
            ],
        )
        style.configure(
            "ShAI.Vertical.TScrollbar",
            gripcount=0,
            background=THEME["button_bg"],
            darkcolor=THEME["button_bg"],
            lightcolor=THEME["button_bg"],
            troughcolor=THEME["surface"],
            bordercolor=THEME["surface"],
            relief="flat",
            width=10,
        )
        style.map(
            "ShAI.Vertical.TScrollbar",
            background=[("active", THEME["button_active"]), ("pressed", THEME["accent"])],
        )
        style.layout(
            "ShAI.Horizontal.TScrollbar",
            [
                (
                    "Horizontal.Scrollbar.trough",
                    {
                        "sticky": "ew",
                        "children": [
                            ("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
                        ],
                    },
                )
            ],
        )
        style.configure(
            "ShAI.Horizontal.TScrollbar",
            gripcount=0,
            background=THEME["button_bg"],
            darkcolor=THEME["button_bg"],
            lightcolor=THEME["button_bg"],
            troughcolor=THEME["surface"],
            bordercolor=THEME["surface"],
            relief="flat",
            width=8,
        )
        style.map(
            "ShAI.Horizontal.TScrollbar",
            background=[("active", THEME["button_active"]), ("pressed", THEME["accent"])],
        )

        # Top toolbar
        top = tk.Frame(self.root, bg=THEME["surface"], padx=10, pady=8)
        top.pack(fill="x")

        tk.Label(
            top, text="Mode:", bg=THEME["surface"], fg=THEME["muted"], font=("Segoe UI", 9, "bold")
        ).pack(side="left")
        self.mode_var = tk.StringVar(value=MODE_LABELS[self._mode])
        self.mode_combo = ttk.Combobox(
            top,
            textvariable=self.mode_var,
            values=list(MODE_LABELS.values()),
            state="readonly",
            width=21,
        )
        self.mode_combo.pack(side="left", padx=(6, 12))
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_combo)
        ToolTip(self.mode_combo, "Switch mode: Ask (chat only), Read-Only (inspect), or Agent (tools)")

        self.settings_btn = tk.Button(
            top,
            cursor="hand2",
            text="⚙",
            command=self._open_settings_dialog,
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            activebackground=THEME["button_active"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=8,
            pady=2,
            font=("Segoe UI", 10),
        )
        self.settings_btn.pack(side="right")
        ToolTip(self.settings_btn, "Configure provider API tokens (/settings)")

        self.project_btn = tk.Button(
            top,
            cursor="hand2",
            text="Project...",
            command=self._choose_project_dialog,
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            activebackground=THEME["button_active"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=8,
            pady=3,
            font=("Segoe UI", 9),
        )
        self.project_btn.pack(side="right", padx=(0, 6))
        self._project_tooltip = ToolTip(self.project_btn, "Select working project folder (/project <path>)")

        # Quick action bar
        actions = tk.Frame(self.root, bg=THEME["bg"], padx=10, pady=4)
        actions.pack(fill="x")
        self._action_buttons: list[tk.Button] = []
        for label, cmd, tip in (
            ("Status", "/status", "Show Git status (/status)"),
            ("Files", "/files", "List project files (/files)"),
            ("Diff", "/diff", "Show Git diff (/diff)"),
            ("Commit", "/commit", "Stage and commit changes (/commit)"),
            ("Push", "/push", "Push commits to remote (/push)"),
        ):
            btn = tk.Button(
                actions,
                cursor="hand2",
                text=label,
                command=lambda c=cmd: self._submit_text(c),
                bg=THEME["surface_alt"],
                fg=THEME["muted"],
                activebackground=THEME["button_active"],
                activeforeground=THEME["fg"],
                relief="flat",
                padx=8,
                pady=2,
                font=("Segoe UI", 8),
            )
            btn.pack(side="left", padx=(0, 5))
            ToolTip(btn, tip)
            self._action_buttons.append(btn)

        reset_btn = tk.Button(
            actions,
            cursor="hand2",
            text="Reset",
            command=lambda: self._submit_text("/reset"),
            bg="#452432",
            fg=THEME["error"],
            activebackground="#5c2b3d",
            activeforeground=THEME["error"],
            relief="flat",
            padx=8,
            pady=2,
            font=("Segoe UI", 8, "bold"),
        )
        reset_btn.pack(side="right")
        ToolTip(reset_btn, "Clear conversation history and task plan (/reset)")
        self._action_buttons.append(reset_btn)

        help_btn = tk.Button(
            actions,
            cursor="hand2",
            text="?",
            command=lambda: self._submit_text("/help"),
            bg=THEME["surface_alt"],
            fg=THEME["muted"],
            activebackground=THEME["button_active"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=8,
            pady=2,
            font=("Segoe UI", 8, "bold"),
        )
        help_btn.pack(side="right", padx=(0, 5))
        ToolTip(help_btn, "Show available commands (/help)")
        self._action_buttons.append(help_btn)

        # Transcript area
        self.transcript_frame = tk.Frame(self.root, bg=THEME["bg"], padx=10, pady=4)
        self.transcript_frame.pack(fill="both", expand=True)
        transcript_frame = self.transcript_frame

        self.transcript_scrollbar = ttk.Scrollbar(
            transcript_frame,
            orient="vertical",
            style="ShAI.Vertical.TScrollbar",
        )
        self.transcript_scrollbar.pack(side="right", fill="y")

        self.transcript = tk.Text(
            transcript_frame,
            wrap="word",
            state="disabled",
            bg=THEME["surface"],
            fg=THEME["fg"],
            insertbackground=THEME["fg"],
            relief="flat",
            padx=10,
            pady=8,
            font=("Consolas", 10),
            yscrollcommand=self.transcript_scrollbar.set,
        )
        self.transcript.pack(side="left", fill="both", expand=True)
        self.transcript_scrollbar.config(command=self.transcript.yview)
        self._bind_transcript_scroll(self.transcript)
        self.transcript.bind("<Configure>", self._on_transcript_configure, add="+")
        self.root.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_global_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_global_mousewheel, add="+")
        self.transcript.tag_config(
            "user",
            foreground=THEME["user"],
            background="#1c332b",
            font=("Segoe UI", 10, "bold"),
            lmargin1=72,
            lmargin2=72,
            rmargin=12,
            spacing1=5,
            spacing3=5,
        )
        self.transcript.tag_config(
            "assistant",
            foreground=THEME["fg"],
            background="#1e2238",
            font=("Segoe UI", 10),
            lmargin1=10,
            lmargin2=10,
            rmargin=48,
            spacing1=2,
            spacing3=2,
        )
        self.transcript.tag_config(
            "assistant_header",
            foreground=THEME["assistant"],
            background="#1e2238",
            font=("Segoe UI", 8, "bold"),
            lmargin1=10,
            rmargin=48,
            spacing1=6,
        )
        self.transcript.tag_config("bubble_hidden", elide=True)
        self.transcript.tag_config("bubble_right", justify="right", rmargin=10, spacing1=4, spacing3=4)
        self.transcript.tag_config("bubble_left", justify="left", lmargin1=10, spacing1=4, spacing3=4)
        self.transcript.tag_config("system", foreground=THEME["system"])
        self.transcript.tag_config("tool", foreground=THEME["tool"])
        self.transcript.tag_config("tool_header", foreground=THEME["tool"], font=("Consolas", 10, "bold"))
        self.transcript.tag_config("md_bold", font=("Segoe UI", 10, "bold"))
        self.transcript.tag_config("md_italic", font=("Segoe UI", 10, "italic"))
        self.transcript.tag_config(
            "md_code", background="#282a3e", foreground="#f9e2af", font=("Consolas", 9)
        )
        self.transcript.tag_config("md_link", foreground=THEME["accent"], underline=True)
        self.transcript.tag_config(
            "md_h1", foreground=THEME["accent"], font=("Segoe UI", 12, "bold"), spacing1=6, spacing3=3
        )
        self.transcript.tag_config(
            "md_h2", foreground=THEME["accent"], font=("Segoe UI", 11, "bold"), spacing1=5, spacing3=2
        )
        self.transcript.tag_config(
            "md_h3", foreground="#b4befe", font=("Segoe UI", 10, "bold"), spacing1=4, spacing3=2
        )
        self.transcript.tag_config(
            "md_quote", foreground=THEME["muted"], font=("Segoe UI", 10, "italic")
        )
        self.transcript.tag_config(
            "code", background="#11111b", foreground=THEME["fg"], font=("Consolas", 9),
            lmargin1=10, lmargin2=10, rmargin=10,
        )
        self.transcript.tag_config(
            "code_header", background="#1e2030", foreground=THEME["accent"], font=("Consolas", 9, "bold"),
            lmargin1=8, lmargin2=8, rmargin=10, spacing1=4, spacing3=2,
        )
        self.transcript.tag_config(
            "diff_file", background="#1e2030", foreground=THEME["accent"], font=("Consolas", 9, "bold"),
            lmargin1=10, lmargin2=10, rmargin=10,
        )
        self.transcript.tag_config(
            "diff_hunk", background="#1b2432", foreground="#94e2d5", font=("Consolas", 9, "bold"),
            lmargin1=10, lmargin2=10, rmargin=10,
        )
        self.transcript.tag_config(
            "diff_add", background="#193326", foreground="#a6e3a1", font=("Consolas", 9),
            lmargin1=10, lmargin2=10, rmargin=10,
        )
        self.transcript.tag_config(
            "diff_del", background="#3b1e28", foreground="#f38ba8", font=("Consolas", 9),
            lmargin1=10, lmargin2=10, rmargin=10,
        )
        self.transcript.tag_config(
            "diff_ctx", background="#11111b", foreground="#bac2de", font=("Consolas", 9),
            lmargin1=10, lmargin2=10, rmargin=10,
        )
        self.transcript.tag_config("warning", foreground=THEME["warning"])
        self.transcript.tag_config("error", foreground=THEME["error"])
        self.transcript.tag_config("ansi_bold", font=("Consolas", 10, "bold"))
        self.transcript.tag_config("ansi_dim", foreground=THEME["muted"])
        self.transcript.tag_config("ansi_underline", underline=True)
        for code, color in ANSI_FG_COLORS.items():
            self.transcript.tag_config(f"ansi_fg_{code}", foreground=color)
        for code, color in ANSI_BG_COLORS.items():
            self.transcript.tag_config(f"ansi_bg_{code}", background=color)

        # Current task list (shown above context meter & input when active)
        self.task_panel = tk.Frame(
            self.root, bg=THEME["surface"], padx=10, pady=5, highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        self.task_header_var = tk.StringVar(value="Tasks")
        tk.Label(
            self.task_panel,
            textvariable=self.task_header_var,
            bg=THEME["surface"],
            fg=THEME["accent"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill="x")
        self.task_items_frame = tk.Frame(self.task_panel, bg=THEME["surface"])
        self.task_items_frame.pack(fill="x", pady=(2, 0))

        # Message queue panel (shown below task list when messages are queued)
        self.queue_panel = tk.Frame(
            self.root, bg=THEME["surface"], padx=10, pady=5, highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        queue_header_frame = tk.Frame(self.queue_panel, bg=THEME["surface"])
        queue_header_frame.pack(fill="x")
        self.queue_header_var = tk.StringVar(value="Message Queue")
        tk.Label(
            queue_header_frame,
            textvariable=self.queue_header_var,
            bg=THEME["surface"],
            fg=THEME["accent"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(side="left")
        # Queue control buttons
        self.queue_clear_btn = tk.Button(
            queue_header_frame,
            cursor="hand2",
            text="Clear",
            command=self._clear_message_queue,
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            activebackground=THEME["button_active"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=6,
            pady=1,
            font=("Segoe UI", 7),
        )
        self.queue_clear_btn.pack(side="right", padx=(4, 0))
        ToolTip(self.queue_clear_btn, "Clear all queued messages")
        self.queue_send_all_btn = tk.Button(
            queue_header_frame,
            cursor="hand2",
            text="Send All",
            command=self._send_all_queued,
            bg=THEME["accent"],
            fg="#11111b",
            activebackground="#b4befe",
            activeforeground="#11111b",
            relief="flat",
            padx=6,
            pady=1,
            font=("Segoe UI", 7, "bold"),
        )
        self.queue_send_all_btn.pack(side="right", padx=(4, 0))
        ToolTip(self.queue_send_all_btn, "Send all queued messages sequentially")
        self.queue_items_frame = tk.Frame(self.queue_panel, bg=THEME["surface"])
        self.queue_items_frame.pack(fill="x", pady=(2, 0))

        # Context window meter (above prompt input box)
        self.context_bar = tk.Frame(self.root, bg=THEME["bg"], padx=10, pady=2)
        self.context_bar.pack(fill="x")
        self.context_var = tk.StringVar(value="Context: —")
        tk.Label(
            self.context_bar,
            textvariable=self.context_var,
            bg=THEME["bg"],
            fg=THEME["muted"],
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(side="left")
        self.context_progress = ttk.Progressbar(
            self.context_bar,
            style="Shai.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
            length=120,
        )
        self.context_progress.pack(side="left", fill="x", expand=True, padx=(10, 0))

        # Provider & Model selector bar (right above prompt input box)
        model_bar = tk.Frame(self.root, bg=THEME["bg"], padx=10, pady=2)
        model_bar.pack(fill="x")
        tk.Label(
            model_bar, text="Provider:", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 8)
        ).pack(side="left")
        self.provider_var = tk.StringVar(
            value=engine.PROVIDER if engine.PROVIDER in PROVIDERS else list(PROVIDERS)[0]
        )
        self.provider_combo = ttk.Combobox(
            model_bar,
            textvariable=self.provider_var,
            values=list(PROVIDERS),
            state="readonly",
            width=12,
        )
        self.provider_combo.pack(side="left", padx=(4, 8))
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_combo)
        ToolTip(self.provider_combo, "Select AI provider")

        tk.Label(
            model_bar, text="Model:", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 8)
        ).pack(side="left")
        self.model_var = tk.StringVar(value=engine.MODEL or "")
        self.model_combo = ttk.Combobox(
            model_bar,
            textvariable=self.model_var,
            values=[engine.MODEL] if engine.MODEL else [],
            state="readonly",
            postcommand=self._ensure_models_loaded_for_dropdown,
        )
        self.model_combo.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_combo)
        self.model_combo.bind("<Return>", self._on_model_combo)
        ToolTip(self.model_combo, "Select model for the active provider")
        self._models_cache: dict[str, list[str]] = {}
        self._models_loading_for: str | None = None

        # Input area
        self.input_frame = tk.Frame(self.root, bg=THEME["bg"], padx=10, pady=6)
        self.input_frame.pack(fill="x")
        input_frame = self.input_frame

        self.send_btn = tk.Button(
            input_frame,
            cursor="hand2",
            text="Send",
            command=self._on_send_clicked,
            bg=THEME["accent"],
            fg="#11111b",
            activebackground="#b4befe",
            activeforeground="#11111b",
            relief="flat",
            padx=16,
            pady=12,
            font=("Segoe UI", 10, "bold"),
        )
        self.send_btn.pack(side="right")
        self._send_tooltip = ToolTip(self.send_btn, "Send message (Enter; Shift+Enter for newline)")

        self.mic_cont_btn = tk.Button(
            input_frame,
            cursor="hand2",
            text="🎤",
            command=self._on_mic_cont_clicked,
            bg=THEME["surface_alt"],
            fg=THEME["fg"],
            activebackground=THEME["border"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=10,
            pady=12,
            font=("Segoe UI", 10, "bold"),
        )
        self.mic_cont_btn.pack(side="right", padx=(0, 6))
        self._mic_cont_tooltip = ToolTip(
            self.mic_cont_btn,
            "Continuous listening — auto-sends on pause (Ctrl+M)",
        )

        self.input_box = tk.Text(
            input_frame,
            width=1,
            height=3,
            wrap="word",
            bg=THEME["surface_alt"],
            fg=THEME["fg"],
            insertbackground=THEME["fg"],
            relief="flat",
            padx=8,
            pady=6,
            font=("Segoe UI", 10),
        )
        self.input_box.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.input_box.bind("<Return>", self._on_enter_key)
        self.input_box.bind("<Up>", self._on_history_up)
        self.input_box.bind("<Down>", self._on_history_down)
        self.input_box.focus_set()

        # Status bar
        status_bar = tk.Frame(self.root, bg=THEME["surface"], padx=10, pady=4)
        status_bar.pack(fill="x")
        self.status_var = tk.StringVar(value="Idle")
        tk.Label(
            status_bar,
            textvariable=self.status_var,
            bg=THEME["surface"],
            fg=THEME["muted"],
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

    def _refresh_header(self):
        project_path = pathlib.Path(engine.ROOT)
        project_name = project_path.name or str(project_path)
        self.root.title(f"ShAI - {project_path}")
        self.project_btn.config(text=f"📁 {project_name}")
        self._project_tooltip.set_text(f"Project: {project_path} (click to switch)")
        self.mode_var.set(MODE_LABELS[self._mode])
        if hasattr(self, "provider_var") and engine.PROVIDER in PROVIDERS:
            self.provider_var.set(engine.PROVIDER)
        if hasattr(self, "model_var"):
            self.model_var.set(engine.MODEL or "")
        self._refresh_context_meter(force=True)
        self._refresh_task_panel(force=True)

    def _refresh_context_meter(self, force: bool = False):
        history = getattr(self, "history", [])
        key = (len(history), engine.PROVIDER, engine.MODEL)
        if not force and key == self._last_context_key:
            return
        self._last_context_key = key

        estimated = estimate_messages_tokens(history) if history else 0
        context_limit = (
            get_context_window(engine.PROVIDER, engine.MODEL, engine.load_config())
            if engine.PROVIDER != "unknown" and engine.MODEL
            else None
        )
        if context_limit:
            pct = min(100.0, (estimated / float(context_limit)) * 100.0)
            self.context_var.set(
                f"Context: {estimated:,} / {context_limit:,} tokens ({pct:.1f}%)"
            )
            self.context_progress["value"] = pct
        else:
            self.context_var.set(f"Context: ~{estimated:,} tokens")
            self.context_progress["value"] = 0

    def _refresh_task_panel(self, force: bool = False):
        plan = list(getattr(engine, "TASK_PLAN", []) or [])
        snapshot = [(str(item.get("status", "pending")), str(item.get("title", ""))) for item in plan]
        if not force and snapshot == self._last_task_snapshot:
            return
        self._last_task_snapshot = snapshot

        for child in self.task_items_frame.winfo_children():
            child.destroy()

        if not snapshot:
            self.task_panel.pack_forget()
            return

        done_count = sum(1 for status, _ in snapshot if status == "completed")
        self.task_header_var.set(f"Tasks ({done_count}/{len(snapshot)}):")
        status_styles = {
            "completed": ("x", THEME["user"]),
            "in_progress": ("~", THEME["assistant"]),
            "blocked": ("!", THEME["error"]),
            "pending": (" ", THEME["muted"]),
        }
        for status, title in snapshot:
            char, color = status_styles.get(status, (" ", THEME["muted"]))
            tk.Label(
                self.task_items_frame,
                text=f"  [{char}] {title}",
                bg=THEME["surface"],
                fg=color,
                font=("Consolas", 8),
                anchor="w",
            ).pack(fill="x")

        if not self.task_panel.winfo_manager():
            self.task_panel.pack(fill="x", padx=10, pady=(2, 2), before=self.context_bar)

    def _refresh_queue_panel(self, force: bool = False):
        """Refresh the message queue panel display."""
        queue = list(self._queued_messages)
        snapshot = [(i, msg) for i, msg in enumerate(queue)]
        if not force and snapshot == getattr(self, "_last_queue_snapshot", None):
            return
        self._last_queue_snapshot = snapshot

        for child in self.queue_items_frame.winfo_children():
            child.destroy()

        if not snapshot:
            self.queue_panel.pack_forget()
            return

        self.queue_header_var.set(f"Message Queue ({len(snapshot)}):")
        for idx, msg in snapshot:
            item_frame = tk.Frame(self.queue_items_frame, bg=THEME["surface"])
            item_frame.pack(fill="x", pady=1)

            # Message preview (truncated)
            preview = msg[:80] + ("..." if len(msg) > 80 else "")
            tk.Label(
                item_frame,
                text=f"  {idx + 1}. {preview}",
                bg=THEME["surface"],
                fg=THEME["fg"],
                font=("Consolas", 8),
                anchor="w",
                cursor="hand2",
            ).pack(side="left", fill="x", expand=True)

            # Move up button
            up_btn = tk.Button(
                item_frame,
                text="▲",
                command=lambda i=idx: self._move_queued_message(i, -1),
                bg=THEME["button_bg"],
                fg=THEME["fg"],
                activebackground=THEME["button_active"],
                activeforeground=THEME["fg"],
                relief="flat",
                padx=4,
                pady=0,
                font=("Segoe UI", 7),
                state="normal" if idx > 0 else "disabled",
            )
            up_btn.pack(side="right", padx=(2, 0))
            ToolTip(up_btn, "Move up")

            # Move down button
            down_btn = tk.Button(
                item_frame,
                text="▼",
                command=lambda i=idx: self._move_queued_message(i, 1),
                bg=THEME["button_bg"],
                fg=THEME["fg"],
                activebackground=THEME["button_active"],
                activeforeground=THEME["fg"],
                relief="flat",
                padx=4,
                pady=0,
                font=("Segoe UI", 7),
                state="normal" if idx < len(snapshot) - 1 else "disabled",
            )
            down_btn.pack(side="right", padx=(2, 0))
            ToolTip(down_btn, "Move down")

            # Remove button
            remove_btn = tk.Button(
                item_frame,
                text="✕",
                command=lambda i=idx: self._remove_queued_message(i),
                bg=THEME["error"],
                fg="#11111b",
                activebackground="#f38ba8",
                activeforeground="#11111b",
                relief="flat",
                padx=4,
                pady=0,
                font=("Segoe UI", 7, "bold"),
            )
            remove_btn.pack(side="right", padx=(2, 0))
            ToolTip(remove_btn, "Remove from queue")

        # Pack queue panel before context_bar (after task_panel if visible)
        if not self.queue_panel.winfo_manager():
            if self.task_panel.winfo_manager():
                self.queue_panel.pack(fill="x", padx=10, pady=(2, 2), before=self.context_bar)
            else:
                self.queue_panel.pack(fill="x", padx=10, pady=(2, 2), before=self.context_bar)

    def _clear_message_queue(self):
        """Clear all queued messages."""
        self._queued_messages.clear()
        self._refresh_queue_panel(force=True)

    def _send_all_queued(self):
        """Send all queued messages sequentially."""
        if not self._queued_messages:
            return
        # The _poll_events already handles sending queued messages one by one
        # when a turn completes. This just ensures the first one starts if idle.
        if not self._busy:
            next_msg = self._queued_messages.pop(0)
            self._submit_text(next_msg)
        self._refresh_queue_panel(force=True)

    def _remove_queued_message(self, index: int):
        """Remove a specific message from the queue."""
        if 0 <= index < len(self._queued_messages):
            self._queued_messages.pop(index)
            self._refresh_queue_panel(force=True)

    def _move_queued_message(self, index: int, direction: int):
        """Move a queued message up or down."""
        new_index = index + direction
        if 0 <= new_index < len(self._queued_messages):
            self._queued_messages[index], self._queued_messages[new_index] = (
                self._queued_messages[new_index],
                self._queued_messages[index],
            )
            self._refresh_queue_panel(force=True)

    def _collapse_active_tool_batch(self):
        batch = self._active_tool_batch
        if batch is None:
            return
        if not batch["collapsed"]:
            batch["collapsed"] = True
            self.transcript.tag_config(batch["body_tag"], elide=True)
            self._update_tool_batch_header(batch)
        self._active_tool_batch = None

    def _insert_styled_text(self, text: str, tag: str = "system", target: tk.Text | None = None):
        widget = target or self.transcript
        if "\x1b[" in text or self._ansi_state is not None:
            segments, new_state = parse_ansi_segments(text, self._ansi_state)
            has_active_style = any(new_state.values())
            self._ansi_state = new_state if has_active_style else None
            for chunk, style in segments:
                tags: list[str] = []
                if style.get("fg") in ANSI_FG_COLORS:
                    tags.append(f"ansi_fg_{style['fg']}")
                elif style.get("dim"):
                    tags.append("ansi_dim")
                elif tag:
                    tags.append(tag)
                if style.get("bg") in ANSI_BG_COLORS:
                    tags.append(f"ansi_bg_{style['bg']}")
                if style.get("bold"):
                    tags.append("ansi_bold")
                if style.get("underline"):
                    tags.append("ansi_underline")
                self._insert_markdown_text(chunk, tag, target=widget, base_tags=tuple(tags))
        else:
            self._insert_markdown_text(text, tag, target=widget)

    def _configure_markdown_tags(
        self, widget: tk.Text, base_bg: str | None = None, base_fg: str | None = None
    ):
        widget.tag_config("md_bold", font=("Segoe UI", 10, "bold"))
        widget.tag_config("md_italic", font=("Segoe UI", 10, "italic"))
        widget.tag_config(
            "md_code", background="#282a3e", foreground="#f9e2af", font=("Consolas", 9)
        )
        widget.tag_config("md_link", foreground=THEME["accent"], underline=True)
        widget.tag_config(
            "md_h1", foreground=THEME["accent"], font=("Segoe UI", 12, "bold"), spacing1=4, spacing3=2
        )
        widget.tag_config(
            "md_h2", foreground=THEME["accent"], font=("Segoe UI", 11, "bold"), spacing1=3, spacing3=2
        )
        widget.tag_config(
            "md_h3", foreground="#b4befe", font=("Segoe UI", 10, "bold"), spacing1=2, spacing3=1
        )
        widget.tag_config(
            "md_quote", foreground=THEME["muted"], font=("Segoe UI", 10, "italic")
        )
        if base_bg:
            widget.tag_config(
                "user",
                foreground=base_fg or THEME["user"],
                background=base_bg,
                font=("Segoe UI", 10),
            )
            widget.tag_config(
                "assistant",
                foreground=base_fg or THEME["fg"],
                background=base_bg,
                font=("Segoe UI", 10),
            )
        widget.tag_config("ansi_bold", font=("Segoe UI", 10, "bold"))
        widget.tag_config("ansi_dim", foreground=THEME["muted"])
        widget.tag_config("ansi_underline", underline=True)
        for code, color in ANSI_FG_COLORS.items():
            widget.tag_config(f"ansi_fg_{code}", foreground=color)
        for code, color in ANSI_BG_COLORS.items():
            widget.tag_config(f"ansi_bg_{code}", background=color)

    def _insert_markdown_text(
        self,
        text: str,
        tag: str = "system",
        target: tk.Text | None = None,
        base_tags: tuple[str, ...] | None = None,
    ):
        widget = target or self.transcript
        root_tags = base_tags if base_tags is not None else ((tag,) if tag else ())
        if tag not in ("assistant", "system", "user"):
            widget.insert(tk.END, text, root_tags)
            return
        lines = text.splitlines(keepends=True)
        for line in lines:
            ending = "\n" if line.endswith("\n") else ""
            raw = line[:-1] if ending else line
            stripped = raw.lstrip()
            extra_line_tags: tuple[str, ...] = ()
            if tag != "user":
                if stripped.startswith("### "):
                    raw = stripped[4:]
                    extra_line_tags = ("md_h3",)
                elif stripped.startswith("## "):
                    raw = stripped[3:]
                    extra_line_tags = ("md_h2",)
                elif stripped.startswith("# "):
                    raw = stripped[2:]
                    extra_line_tags = ("md_h1",)
                elif stripped.startswith("> "):
                    raw = "│ " + stripped[2:]
                    extra_line_tags = ("md_quote",)
                elif stripped.startswith(("- ", "* ")):
                    indent = raw[: len(raw) - len(stripped)]
                    raw = f"{indent}• {stripped[2:]}"
            for span_text, span_tags in parse_markdown_inline(raw):
                combined = tuple(t for t in (*root_tags, *extra_line_tags, *span_tags) if t)
                widget.insert(tk.END, span_text, combined)
            if ending:
                widget.insert(tk.END, ending, root_tags)

    @staticmethod
    def _draw_rounded_rect(
        canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, r: int = 12, **kwargs
    ) -> int:
        r = max(2, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
        points = (
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        )
        return canvas.create_polygon(points, smooth=True, **kwargs)

    def _is_in_transcript_area(self, widget: tk.Misc | None) -> bool:
        cur = widget
        while cur is not None:
            if cur is self.transcript_frame or cur is self.transcript:
                return True
            cur = getattr(cur, "master", None)
        return False

    def _on_global_mousewheel(self, event):
        try:
            if not self.transcript.winfo_exists():
                return None
            target = self.root.winfo_containing(event.x_root, event.y_root) or event.widget
        except (tk.TclError, AttributeError):
            target = getattr(event, "widget", None)
        if self._is_in_transcript_area(target):
            return self._on_child_mousewheel(event)
        return None

    def _on_child_mousewheel(self, event):
        delta = getattr(event, "delta", 0)
        if delta:
            px = int(-delta / 2)
            if px == 0:
                px = -8 if delta > 0 else 8
            self.transcript.yview_scroll(px, "pixels")
        elif getattr(event, "num", None) == 4:
            self.transcript.yview_scroll(-40, "pixels")
        elif getattr(event, "num", None) == 5:
            self.transcript.yview_scroll(40, "pixels")
        return "break"

    def _on_transcript_drag_start(self, event):
        self._drag_scroll_y = getattr(event, "y_root", None)
        return "break"

    def _on_transcript_drag_move(self, event):
        y_root = getattr(event, "y_root", None)
        if y_root is not None and self._drag_scroll_y is not None:
            dy = self._drag_scroll_y - y_root
            self._drag_scroll_y = y_root
            if dy:
                self.transcript.yview_scroll(dy, "pixels")
        return "break"

    def _bind_transcript_scroll(self, widget: tk.Misc):
        widget.bind("<MouseWheel>", self._on_child_mousewheel)
        widget.bind("<Shift-MouseWheel>", lambda _e: "break")
        widget.bind("<Button-4>", self._on_child_mousewheel)
        widget.bind("<Button-5>", self._on_child_mousewheel)
        widget.bind("<Button-2>", self._on_transcript_drag_start)
        widget.bind("<B2-Motion>", self._on_transcript_drag_move)
        for child in widget.winfo_children():
            self._bind_transcript_scroll(child)

    def _insert_rounded_bubble(
        self,
        role_tag: str,
        text: str,
        timestamp: str | None = None,
        is_history: bool = False,
    ):
        cleaned = text.strip()
        if not cleaned:
            return
        is_user = role_tag == "user"
        display_text = cleaned[4:].strip() if is_user and cleaned.startswith("You:") else cleaned
        if not display_text:
            return

        if is_history:
            bubble_bg = "#1d2624" if is_user else "#1a1c29"
            bubble_border = "#2a3834" if is_user else "#2a2d40"
            sender_fg = "#7fa382" if is_user else "#7a9aad"
            body_fg = "#9fb2a2" if is_user else "#9ba3b8"
        else:
            bubble_bg = "#1c332b" if is_user else "#1e2238"
            bubble_border = "#2d5446" if is_user else "#313654"
            sender_fg = THEME["user"] if is_user else THEME["assistant"]
            body_fg = THEME["user"] if is_user else THEME["fg"]
        sender_label = "You" if is_user else "ShAI"
        ts_text = timestamp or datetime.now().strftime("%H:%M")
        show_header = self._last_bubble_role != role_tag
        self._last_bubble_role = role_tag

        canvas = tk.Canvas(
            self.transcript,
            bg=THEME["surface"],
            highlightthickness=0,
            borderwidth=0,
        )

        inner = tk.Frame(canvas, bg=bubble_bg, padx=10, pady=8)
        if show_header:
            hdr = tk.Frame(inner, bg=bubble_bg)
            hdr.pack(fill="x", pady=(0, 3))
            tk.Label(
                hdr,
                text=sender_label,
                bg=bubble_bg,
                fg=sender_fg,
                font=("Segoe UI", 8, "bold"),
            ).pack(side="left")
            tk.Label(
                hdr,
                text=ts_text,
                bg=bubble_bg,
                fg=THEME["muted"],
                font=("Segoe UI", 8),
            ).pack(side="right", padx=(14, 0))

        plain_text = strip_ansi(display_text)
        transcript_px = max(320, self.transcript.winfo_width() - 32)
        max_chars = max(24, min(62, int(transcript_px * 0.72 / 8)))
        max_line_len = max((len(l) for l in plain_text.splitlines()), default=1)
        body_w = max(14, min(max_chars, max_line_len + 2))

        body_holder = tk.Frame(inner, bg=bubble_bg)
        body_holder.pack(fill="x", expand=True)

        body = tk.Text(
            body_holder,
            wrap="word",
            bg=bubble_bg,
            fg=body_fg,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 10),
            width=body_w,
            height=1,
        )
        self._configure_markdown_tags(body, base_bg=bubble_bg, base_fg=body_fg)
        self._insert_styled_text(display_text, role_tag, target=body)
        est_lines = sum(
            max(1, len(textwrap.wrap(ln, width=max(1, body_w - 1), break_long_words=True, break_on_hyphens=False)))
            for ln in (plain_text.splitlines() or [""])
        )
        body.config(height=max(1, est_lines), state="disabled")
        body.pack(fill="both", expand=True)

        win_id = canvas.create_window(2, 2, window=inner, anchor="nw")

        def _sync_bubble(_event=None):
            if body.winfo_width() > 40:
                actual = int((body.count("1.0", tk.END, "displaylines") or (1,))[0] or 1)
                if actual != int(body.cget("height")):
                    body.config(height=max(1, actual))
                ypix = body.count("1.0", tk.END, "ypixels")
                if ypix and ypix[0]:
                    target_h = int(ypix[0]) + 4
                    body_holder.pack_propagate(False)
                    body_holder.config(width=max(80, body.winfo_reqwidth()), height=target_h)
            inner.update_idletasks()
            w = max(120, inner.winfo_reqwidth() + 4)
            h = max(36, inner.winfo_reqheight() + 4)
            canvas.config(width=w, height=h)
            canvas.delete("bubble_bg")
            bg_id = self._draw_rounded_rect(
                canvas, 1, 1, w - 1, h - 1, r=12, fill=bubble_bg, outline=bubble_border, width=1, tags="bubble_bg"
            )
            canvas.tag_lower(bg_id, win_id)

        inner.bind("<Configure>", _sync_bubble)
        body.bind("<Configure>", _sync_bubble)
        _sync_bubble()
        self.root.after_idle(_sync_bubble)

        self._bind_transcript_scroll(canvas)

        align_tag = "bubble_right" if is_user else "bubble_left"
        self.transcript.insert(tk.END, cleaned + "\n", ("bubble_hidden",))
        line_start = self.transcript.index(tk.END + "-1c")
        self.transcript.window_create(tk.END, window=canvas, pady=5)
        self.transcript.insert(tk.END, "\n")
        self.transcript.tag_add(align_tag, line_start, self.transcript.index(tk.END + "-1c"))

    def _diff_title(self, lines: list[str]) -> str:
        for line in lines[:4]:
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    return f"diff: {parts[3][2:] if parts[3].startswith('b/') else parts[3]}"
            if line.startswith("+++ "):
                path = line[4:].strip()
                return f"diff: {path[2:] if path.startswith('b/') else path}"
        return "diff"

    def _diff_line_tag(self, line: str) -> str:
        if line.startswith(("diff --git ", "index ", "--- ", "+++ ", "new file ", "deleted file ", "old mode ", "new mode ", "rename ")):
            return "diff_file"
        if line.startswith("@@ "):
            return "diff_hunk"
        if line.startswith("+"):
            return "diff_add"
        if line.startswith("-"):
            return "diff_del"
        return "diff_ctx"

    def _format_code_block_header(self, block: dict) -> str:
        arrow = "▶" if block["collapsed"] else "▼"
        label = block["label"]
        total = block["total"]
        if block["collapsed"]:
            hidden = total - CODE_PREVIEW_LINES
            return f" {arrow} {label} ({total} lines, {hidden} hidden — click to expand)\n"
        return f" {arrow} {label} ({total} lines — click to collapse)\n"

    def _toggle_code_block(self, block_id: int):
        block = self._code_blocks.get(block_id)
        if not block:
            return
        block["collapsed"] = not block["collapsed"]
        self.transcript.tag_config(block["body_tag"], elide=block["collapsed"])
        header_lbl = block.get("header_label")
        if header_lbl is not None:
            header_lbl.config(text=self._format_code_block_header(block).strip())
        body_text = block.get("body_text")
        if body_text is not None:
            self._populate_code_box_text(block)
        ranges = self.transcript.tag_ranges(block["header_tag"])
        if len(ranges) >= 2:
            self.transcript.config(state="normal")
            self.transcript.replace(
                ranges[0],
                ranges[1],
                self._format_code_block_header(block),
                ("code_header", block["header_tag"]),
            )
            self.transcript.config(state="disabled")

    def _populate_code_box_text(self, block: dict):
        body_text: tk.Text = block["body_text"]
        lines: list[str] = block["lines"]
        is_diff = block["lang"] == "diff"
        visible = lines[:CODE_PREVIEW_LINES] if block["collapsed"] else lines
        body_text.config(state="normal")
        body_text.delete("1.0", tk.END)
        for idx, line in enumerate(visible):
            clean_line = strip_ansi(line)
            line_tag = self._diff_line_tag(clean_line) if is_diff else "code"
            suffix = "\n" if idx < len(visible) - 1 else ""
            body_text.insert(tk.END, clean_line + suffix, (line_tag,))
        body_text.config(height=max(1, min(len(visible), 22)), state="disabled")

    def _copy_code_block(self, block: dict, copy_btn: tk.Button):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(strip_ansi(l) for l in block["lines"]))
            copy_btn.config(text="Copied!")
            self.root.after(1200, lambda: copy_btn.winfo_exists() and copy_btn.config(text="Copy"))
        except tk.TclError:
            pass

    def _open_code_popout(self, block: dict):
        win = tk.Toplevel(self.root)
        win.title(f"ShAI — {block.get('label', 'code')}")
        win.configure(bg=THEME["bg"])
        win.geometry("760x520")
        win.minsize(420, 280)

        top = tk.Frame(win, bg=THEME["surface"], padx=10, pady=6)
        top.pack(fill="x")
        tk.Label(
            top,
            text=f"{block.get('label', 'code')} ({block.get('total', 0)} lines)",
            bg=THEME["surface"],
            fg=THEME["accent"],
            font=("Consolas", 10, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        copy_btn = tk.Button(
            top,
            cursor="hand2",
            text="Copy",
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            activebackground=THEME["button_active"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=10,
            pady=3,
            font=("Segoe UI", 9),
        )
        copy_btn.config(command=lambda: self._copy_code_block(block, copy_btn))
        copy_btn.pack(side="right")

        body_frame = tk.Frame(win, bg=THEME["bg"], padx=8, pady=8)
        body_frame.pack(fill="both", expand=True)
        v_scroll = ttk.Scrollbar(body_frame, orient="vertical", style="ShAI.Vertical.TScrollbar")
        v_scroll.pack(side="right", fill="y")
        h_scroll = ttk.Scrollbar(body_frame, orient="horizontal", style="ShAI.Horizontal.TScrollbar")
        h_scroll.pack(side="bottom", fill="x")

        txt = tk.Text(
            body_frame,
            wrap="none",
            bg="#11111b",
            fg=THEME["fg"],
            relief="flat",
            padx=10,
            pady=8,
            font=("Consolas", 10),
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set,
        )
        txt.tag_config("code", background="#11111b", foreground=THEME["fg"])
        txt.tag_config("diff_file", background="#1e2030", foreground=THEME["accent"], font=("Consolas", 10, "bold"))
        txt.tag_config("diff_hunk", background="#1b2432", foreground="#94e2d5", font=("Consolas", 10, "bold"))
        txt.tag_config("diff_add", background="#193326", foreground="#a6e3a1")
        txt.tag_config("diff_del", background="#3b1e28", foreground="#f38ba8")
        txt.tag_config("diff_ctx", background="#11111b", foreground="#bac2de")

        is_diff = block.get("lang") == "diff"
        lines = block.get("lines", [])
        for idx, line in enumerate(lines):
            clean = strip_ansi(line)
            line_tag = self._diff_line_tag(clean) if is_diff else "code"
            suffix = "\n" if idx < len(lines) - 1 else ""
            txt.insert(tk.END, clean + suffix, (line_tag,))
        txt.config(state="disabled")
        txt.pack(side="left", fill="both", expand=True)
        v_scroll.config(command=txt.yview)
        h_scroll.config(command=txt.xview)
        center_window(win, width=760, height=520)
        win.lift()
        win.focus_force()
        return win

    def _insert_code_lines(self, lang: str, lines: list[str], extra_tag: str | None = None):
        is_diff = lang == "diff"
        for line in lines:
            clean_line = strip_ansi(line)
            base_tag = self._diff_line_tag(clean_line) if is_diff else "code"
            tags = (base_tag, extra_tag) if extra_tag else (base_tag,)
            self.transcript.insert(tk.END, clean_line + "\n", tags)

    def _on_transcript_configure(self, event):
        box_w = max(200, event.width - 44)
        try:
            self.transcript.xview_moveto(0)
        except tk.TclError:
            pass
        for block in self._code_blocks.values():
            spacer = block.get("width_spacer")
            if spacer is not None:
                try:
                    if spacer.winfo_exists():
                        spacer.config(width=box_w)
                except tk.TclError:
                    pass
        for tblock in self._table_blocks:
            outer = tblock.get("outer")
            if outer is None:
                continue
            try:
                if not outer.winfo_exists():
                    continue
            except tk.TclError:
                continue
            col_widths = self._compute_table_col_widths(
                tblock["col_max_chars"], box_w
            )
            for c_idx, lbl_list in enumerate(tblock["col_labels"]):
                wl = col_widths[c_idx]
                for lbl in lbl_list:
                    lbl.config(wraplength=wl)

    def _insert_code_block(self, lang: str, lines: list[str]):
        label = self._diff_title(lines) if lang == "diff" else (lang or "code")
        self._code_block_seq += 1
        block_id = self._code_block_seq
        header_tag = f"code_hdr_{block_id}"
        body_tag = f"code_body_{block_id}"
        collapsible = len(lines) > CODE_PREVIEW_LINES
        block = {
            "id": block_id,
            "lang": lang,
            "label": label,
            "lines": list(lines),
            "total": len(lines),
            "header_tag": header_tag,
            "body_tag": body_tag,
            "collapsed": collapsible,
        }
        self._code_blocks[block_id] = block
        self.transcript.tag_config(body_tag, elide=collapsible)

        box = tk.Frame(
            self.transcript,
            bg="#11111b",
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        block["box_frame"] = box

        t_width = self.transcript.winfo_width()
        box_w = max(200, (t_width if t_width > 40 else 360) - 40)
        width_spacer = tk.Frame(box, width=box_w, height=1, bg="#11111b")
        width_spacer.pack(fill="x")
        block["width_spacer"] = width_spacer

        header_bar = tk.Frame(box, bg="#1e2030", padx=8, pady=4)
        header_bar.pack(fill="x")

        popout_btn = tk.Button(
            header_bar,
            cursor="hand2",
            text="↗",
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            activebackground=THEME["button_active"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=6,
            pady=1,
            font=("Segoe UI", 8, "bold"),
            command=lambda b=block: self._open_code_popout(b),
        )
        popout_btn.pack(side="right", padx=(4, 0))
        ToolTip(popout_btn, "Open in separate window")
        block["popout_btn"] = popout_btn

        copy_btn = tk.Button(
            header_bar,
            cursor="hand2",
            text="Copy",
            bg=THEME["button_bg"],
            fg=THEME["fg"],
            activebackground=THEME["button_active"],
            activeforeground=THEME["fg"],
            relief="flat",
            padx=6,
            pady=1,
            font=("Segoe UI", 8),
        )
        copy_btn.config(command=lambda b=block, btn=copy_btn: self._copy_code_block(b, btn))
        copy_btn.pack(side="right", padx=(6, 0))
        block["copy_btn"] = copy_btn

        hdr_title = (
            self._format_code_block_header(block).strip()
            if collapsible
            else f"{label} ({len(lines)} lines)"
        )
        header_lbl = tk.Label(
            header_bar,
            text=hdr_title,
            width=1,
            bg="#1e2030",
            fg=THEME["accent"],
            font=("Consolas", 9, "bold"),
            anchor="w",
            cursor="hand2" if collapsible else "",
        )
        header_lbl.pack(side="left", fill="x", expand=True)
        block["header_label"] = header_lbl

        if collapsible:
            for w in (header_bar, header_lbl):
                w.bind("<Button-1>", lambda _e, bid=block_id: self._toggle_code_block(bid))

        h_scroll = ttk.Scrollbar(
            box,
            orient="horizontal",
            style="ShAI.Horizontal.TScrollbar",
        )
        body_text = tk.Text(
            box,
            wrap="none",
            width=1,
            bg="#11111b",
            fg=THEME["fg"],
            relief="flat",
            padx=8,
            pady=6,
            font=("Consolas", 9),
            height=max(1, min(CODE_PREVIEW_LINES if collapsible else len(lines), 22)),
            borderwidth=0,
            highlightthickness=0,
            xscrollcommand=h_scroll.set,
        )
        h_scroll.config(command=body_text.xview)
        body_text.tag_config("code", background="#11111b", foreground=THEME["fg"])
        body_text.tag_config("diff_file", background="#1e2030", foreground=THEME["accent"], font=("Consolas", 9, "bold"))
        body_text.tag_config("diff_hunk", background="#1b2432", foreground="#94e2d5", font=("Consolas", 9, "bold"))
        body_text.tag_config("diff_add", background="#193326", foreground="#a6e3a1")
        body_text.tag_config("diff_del", background="#3b1e28", foreground="#f38ba8")
        body_text.tag_config("diff_ctx", background="#11111b", foreground="#bac2de")
        body_text.pack(fill="x", expand=True)
        h_scroll.pack(fill="x")
        block["h_scroll"] = h_scroll
        block["body_text"] = body_text
        self._populate_code_box_text(block)
        self._bind_transcript_scroll(box)
        body_text.bind(
            "<Shift-MouseWheel>",
            lambda e, bt=body_text: (
                bt.xview_scroll(int(-e.delta / 60) or (-1 if e.delta > 0 else 1), "units"),
                "break",
            )[1],
        )

        self.transcript.window_create(tk.END, window=box, padx=10, pady=4, stretch=True)
        self.transcript.insert(tk.END, "\n")

    @staticmethod
    def _compute_table_col_widths(col_max_chars: list[int], box_w: int) -> list[int]:
        num_cols = max(1, len(col_max_chars))
        content_budget = max(100, box_w - (18 * num_cols) - 4)
        weights = [max(5, min(m, 40)) for m in col_max_chars]
        total_w = max(1, sum(weights))
        widths = [max(44, int(content_budget * w / total_w)) for w in weights]
        if sum(widths) > content_budget:
            scale = content_budget / float(sum(widths))
            widths = [max(36, int(w * scale)) for w in widths]
        return widths

    @staticmethod
    def _clean_table_cell_text(raw: str) -> tuple[str, bool]:
        spans = parse_markdown_inline(raw)
        if not spans:
            return raw, False
        text = "".join(s for s, _ in spans)
        all_bold = all("md_bold" in tags for _, tags in spans)
        return text, all_bold

    def _insert_table_block(self, headers: list[str], rows: list[list[str]]):
        t_width = self.transcript.winfo_width()
        box_w = max(200, (t_width if t_width > 40 else 360) - 44)
        num_cols = max(1, len(headers))

        clean_headers = [self._clean_table_cell_text(h) for h in headers]
        clean_rows = [[self._clean_table_cell_text(c) for c in r] for r in rows]

        col_max_chars = [
            max([len(clean_headers[c][0])] + [len(r[c][0]) for r in clean_rows])
            for c in range(num_cols)
        ]
        col_widths = self._compute_table_col_widths(col_max_chars, box_w)

        outer = tk.Frame(
            self.transcript,
            bg=THEME["border"],
            highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        grid_frame = tk.Frame(outer, bg=THEME["border"])
        grid_frame.pack(fill="both", expand=True)

        col_labels: list[list[tk.Label]] = [[] for _ in range(num_cols)]

        for c_idx, (h_text, _) in enumerate(clean_headers):
            cell = tk.Label(
                grid_frame,
                text=h_text,
                bg="#1e2030",
                fg=THEME["accent"],
                font=("Segoe UI", 9, "bold"),
                anchor="w",
                justify="left",
                padx=8,
                pady=5,
                wraplength=col_widths[c_idx],
            )
            cell.grid(row=0, column=c_idx, sticky="nsew", padx=(0, 1 if c_idx < num_cols - 1 else 0), pady=(0, 1))
            grid_frame.columnconfigure(c_idx, weight=max(1, col_max_chars[c_idx]))
            col_labels[c_idx].append(cell)

        for r_idx, row in enumerate(clean_rows, start=1):
            row_bg = "#11111b" if r_idx % 2 == 1 else "#181825"
            for c_idx, (val, is_bold) in enumerate(row):
                cell = tk.Label(
                    grid_frame,
                    text=val,
                    bg=row_bg,
                    fg=THEME["fg"],
                    font=("Segoe UI", 9, "bold" if is_bold else "normal"),
                    anchor="w",
                    justify="left",
                    padx=8,
                    pady=4,
                    wraplength=col_widths[c_idx],
                )
                cell.grid(
                    row=r_idx,
                    column=c_idx,
                    sticky="nsew",
                    padx=(0, 1 if c_idx < num_cols - 1 else 0),
                    pady=(0, 1 if r_idx < len(clean_rows) else 0),
                )
                col_labels[c_idx].append(cell)

        self._table_blocks.append({
            "outer": outer,
            "col_max_chars": col_max_chars,
            "col_labels": col_labels,
        })
        self._bind_transcript_scroll(outer)
        raw_table = "\n".join(
            " | ".join(c[0] for c in r) for r in [clean_headers, *clean_rows]
        )
        self.transcript.insert(tk.END, raw_table + "\n", ("bubble_hidden",))
        self.transcript.window_create(tk.END, window=outer, padx=10, pady=6)
        self.transcript.insert(tk.END, "\n")

    def _insert_session_separator(self, label: str = "Current Session"):
        self._last_bubble_role = None
        self.transcript.config(state="normal")
        sep = tk.Frame(self.transcript, bg=THEME["surface"])
        t_width = self.transcript.winfo_width()
        sep_w = max(240, (t_width if t_width > 40 else 360) - 36)
        width_spacer = tk.Frame(sep, width=sep_w, height=1, bg=THEME["surface"])
        width_spacer.pack(fill="x")
        row = tk.Frame(sep, bg=THEME["surface"])
        row.pack(fill="x", pady=2)
        tk.Frame(row, bg=THEME["border"], height=1).pack(side="left", fill="x", expand=True)
        tk.Label(
            row,
            text=f"  {label}  ",
            bg=THEME["surface"],
            fg=THEME["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Frame(row, bg=THEME["border"], height=1).pack(side="left", fill="x", expand=True)
        self._bind_transcript_scroll(sep)
        self.transcript.insert(tk.END, f"--- {label} ---\n", ("bubble_hidden",))
        self.transcript.window_create(tk.END, window=sep, padx=10, pady=6)
        self.transcript.insert(tk.END, "\n")
        self.transcript.see(tk.END)
        self.transcript.config(state="disabled")

    def _append_output(
        self,
        text: str,
        tag: str = "system",
        collapse_long: bool = False,
        is_history: bool = False,
    ):
        if text.strip():
            self._collapse_active_tool_batch()
        self.transcript.config(state="normal")
        if tag in ("assistant", "system", "user"):
            treat_as_assistant_bubble = (
                tag == "system"
                and self._busy
                and not collapse_long
                and not text.lstrip().startswith(("[ShAI]", "Model context window:", "Selected:"))
            )
            bubble_role = "assistant" if (tag == "assistant" or treat_as_assistant_bubble) else tag
            for part in split_markdown_code_blocks(text):
                if part[0] == "code":
                    self._insert_code_block(part[1], part[2])
                elif part[1]:
                    chunk = part[1]
                    lines = chunk.splitlines()
                    if collapse_long and len(lines) > CODE_PREVIEW_LINES + 2:
                        self._insert_code_block("output", lines)
                    elif bubble_role in ("user", "assistant") and chunk.strip():
                        for seg in split_markdown_tables(chunk):
                            if seg[0] == "table":
                                self._insert_table_block(seg[1], seg[2])
                            elif seg[1].strip():
                                self._insert_rounded_bubble(bubble_role, seg[1], is_history=is_history)
                    else:
                        self._insert_styled_text(chunk, tag)
        else:
            self._insert_styled_text(text, tag)
        self.transcript.see(tk.END)
        self.transcript.config(state="disabled")

    def _format_tool_batch_header(self, batch: dict) -> str:
        arrow = "▶" if batch["collapsed"] else "▼"
        count = batch["count"]
        noun = "tool call" if count == 1 else "tool calls"
        err = f", {batch['errors']} error" if batch["errors"] == 1 else (
            f", {batch['errors']} errors" if batch["errors"] > 1 else ""
        )
        return f"{arrow} {count} {noun}{err}\n"

    def _toggle_tool_batch(self, batch_id: int):
        batch = self._tool_batches.get(batch_id)
        if not batch:
            return
        batch["collapsed"] = not batch["collapsed"]
        self.transcript.tag_config(batch["body_tag"], elide=batch["collapsed"])
        self._update_tool_batch_header(batch)

    def _update_tool_batch_header(self, batch: dict):
        ranges = self.transcript.tag_ranges(batch["header_tag"])
        if len(ranges) >= 2:
            self.transcript.config(state="normal")
            self.transcript.replace(
                ranges[0],
                ranges[1],
                self._format_tool_batch_header(batch),
                ("tool_header", batch["header_tag"]),
            )
            self.transcript.config(state="disabled")

    def _append_tool_event(self, tool_label: str, error: bool = False):
        tool_label = format_tool_label_for_ui(tool_label)
        self.transcript.config(state="normal")
        batch = self._active_tool_batch
        if batch is None:
            self._tool_batch_seq += 1
            batch_id = self._tool_batch_seq
            header_tag = f"tool_batch_hdr_{batch_id}"
            body_tag = f"tool_batch_body_{batch_id}"
            batch = {
                "id": batch_id,
                "header_tag": header_tag,
                "body_tag": body_tag,
                "count": 0,
                "errors": 0,
                "collapsed": False,
            }
            self._tool_batches[batch_id] = batch
            self._active_tool_batch = batch

            self.transcript.tag_config(body_tag, elide=False)
            self.transcript.tag_bind(
                header_tag, "<Button-1>", lambda _e, bid=batch_id: self._toggle_tool_batch(bid)
            )
            self.transcript.tag_bind(
                header_tag, "<Enter>", lambda _e: self.transcript.config(cursor="hand2")
            )
            self.transcript.tag_bind(
                header_tag, "<Leave>", lambda _e: self.transcript.config(cursor="")
            )
            self.transcript.insert(
                tk.END, self._format_tool_batch_header(batch), ("tool_header", header_tag)
            )

        if error:
            batch["errors"] += 1
            self._turn_had_error = True
            line = f"  [Tool] {tool_label} — ERROR\n"
            line_tag = "error"
        else:
            batch["count"] += 1
            line = f"  [Tool] {tool_label}\n"
            line_tag = "tool"

        # Keep body lines right after the header (before any trailing text).
        self.transcript.insert(tk.END, line, (line_tag, batch["body_tag"]))
        self.transcript.see(tk.END)
        self.transcript.config(state="disabled")
        self._update_tool_batch_header(batch)

    def _format_tool_batch_header(self, batch: dict) -> str:
        arrow = "▶" if batch["collapsed"] else "▼"
        count = batch["count"]
        noun = "tool call" if count == 1 else "tool calls"
        err = f", {batch['errors']} error" if batch["errors"] == 1 else (
            f", {batch['errors']} errors" if batch["errors"] > 1 else ""
        )
        return f"{arrow} {count} {noun}{err}\n"

    def _toggle_tool_batch(self, batch_id: int):
        batch = self._tool_batches.get(batch_id)
        if not batch:
            return
        batch["collapsed"] = not batch["collapsed"]
        self.transcript.tag_config(batch["body_tag"], elide=batch["collapsed"])
        self._update_tool_batch_header(batch)

    def _update_tool_batch_header(self, batch: dict):
        ranges = self.transcript.tag_ranges(batch["header_tag"])
        if len(ranges) >= 2:
            self.transcript.config(state="normal")
            self.transcript.replace(
                ranges[0],
                ranges[1],
                self._format_tool_batch_header(batch),
                ("tool_header", batch["header_tag"]),
            )
            self.transcript.config(state="disabled")

    def _append_tool_event(self, tool_label: str, error: bool = False):
        tool_label = format_tool_label_for_ui(tool_label)
        self.transcript.config(state="normal")
        batch = self._active_tool_batch
        if batch is None:
            self._tool_batch_seq += 1
            batch_id = self._tool_batch_seq
            header_tag = f"tool_batch_hdr_{batch_id}"
            body_tag = f"tool_batch_body_{batch_id}"
            batch = {
                "id": batch_id,
                "header_tag": header_tag,
                "body_tag": body_tag,
                "count": 0,
                "errors": 0,
                "collapsed": False,
            }
            self._tool_batches[batch_id] = batch
            self._active_tool_batch = batch

            self.transcript.tag_config(body_tag, elide=False)
            self.transcript.tag_bind(
                header_tag, "<Button-1>", lambda _e, bid=batch_id: self._toggle_tool_batch(bid)
            )
            self.transcript.tag_bind(
                header_tag, "<Enter>", lambda _e: self.transcript.config(cursor="hand2")
            )
            self.transcript.tag_bind(
                header_tag, "<Leave>", lambda _e: self.transcript.config(cursor="")
            )
            self.transcript.insert(
                tk.END, self._format_tool_batch_header(batch), ("tool_header", header_tag)
            )

        if error:
            batch["errors"] += 1
            self._turn_had_error = True
            line = f"  [Tool] {tool_label} — ERROR\n"
            line_tag = "error"
        else:
            batch["count"] += 1
            line = f"  [Tool] {tool_label}\n"
            line_tag = "tool"

        # Keep body lines right after the header (before any trailing text).
        self.transcript.insert(tk.END, line, (line_tag, batch["body_tag"]))
        self.transcript.see(tk.END)
        self.transcript.config(state="disabled")
        self._update_tool_batch_header(batch)

    def _set_busy(self, busy: bool, status_text: str | None = None):
        self._busy = busy
        state = "disabled" if busy else "normal"
        if busy:
            self.send_btn.config(
                text="Stop",
                command=self._on_stop_clicked,
                state="normal",
                bg="#452432",
                fg=THEME["error"],
                activebackground="#5c2b3d",
                activeforeground=THEME["error"],
            )
            self._send_tooltip.set_text("Stop current request")
        else:
            self._cancel_event.clear()
            self.send_btn.config(
                text="Send",
                command=self._on_send_clicked,
                state="normal",
                bg=THEME["accent"],
                fg="#11111b",
                activebackground="#b4befe",
                activeforeground="#11111b",
            )
            self._send_tooltip.set_text("Send message (Enter; Shift+Enter for newline)")
        self.mode_combo.config(state="disabled" if busy else "readonly")
        self.project_btn.config(state=state)
        # Allow opening settings even while busy
        self.settings_btn.config(state="normal")
        self.provider_combo.config(state="disabled" if busy else "readonly")
        self.model_combo.config(state="disabled" if busy else "readonly")
        for btn in self._action_buttons:
            btn.config(state=state)
        if status_text is not None:
            self.status_var.set(status_text)

    def _on_stop_clicked(self):
        if not self._busy:
            return
        self._queued_messages.clear()
        self._refresh_queue_panel(force=True)
        self._cancel_event.set()
        self.send_btn.config(state="disabled")
        self.status_var.set("Stopping...")

    def _poll_events(self):
        try:
            while True:
                kind, payload, tag = self._event_queue.get_nowait()
                if kind == "output":
                    self._append_output(payload, tag or "system")
                elif kind == "tool":
                    self._append_tool_event(payload, bool(tag))
                elif kind == "progress":
                    if payload:
                        self._has_progress = True
                        self.status_var.set(payload)
                    else:
                        self._has_progress = False
                        if self._busy:
                            self.status_var.set("Working...")
                elif kind == "call":
                    try:
                        payload()
                    except Exception:
                        LOGGER.exception("Error in queued call")
                elif kind == "done":
                    self._collapse_active_tool_batch()
                    self._has_progress = False
                    self._set_busy(False, "Idle")
                    self._refresh_header()
                    # Play completion sound based on success/error
                    if self._sound_cues:
                        try:
                            from modules.ui.speech import play_completion_sound
                            play_completion_sound(self._turn_had_error, self._sound_cue_success, self._sound_cue_error)
                        except Exception:
                            LOGGER.exception("Error playing completion sound")
                    self._turn_had_error = False
                    if self._queued_messages:
                        try:
                            next_msg = self._queued_messages.pop(0)
                            self._submit_text(next_msg)
                            self._refresh_queue_panel(force=True)
                        except Exception:
                            LOGGER.exception("Error sending queued message")
                    elif self._speech_mode == "continuous":
                        self.status_var.set("Listening (continuous)...")
                        if self._speech_pending_auto_send:
                            self._auto_send_continuous_speech()
                elif kind == "wake_word":
                    if self._disable_listening:
                        return
                    if self._speech_mode != "continuous":
                        self.start_speech_listening(continuous=True)
                elif kind == "speech_text":
                    if is_speech_stop_listening_command(payload):
                        self._speech_pending_auto_send = False
                        self._cancel_continuous_auto_send()
                        self.stop_speech_listening()
                        if self._tray is not None and self._is_in_taskbar():
                            self._tray.show_notification(
                                "ShAI — Paused",
                                "Stopped listening. Say 'Shai' to resume.",
                            )
                    elif self._busy and is_speech_cancel_command(payload):
                        self._speech_pending_auto_send = False
                        self._cancel_continuous_auto_send()
                        self._on_stop_clicked()
                        if self._tray is not None and self._is_in_taskbar():
                            self._tray.show_notification(
                                "ShAI — Stopped",
                                "Stopped current request.",
                            )
                    else:
                        self._insert_transcribed_text(payload)
                        if self._speech_mode == "continuous":
                            self._schedule_continuous_auto_send()
                            if self._tray is not None and self._is_in_taskbar():
                                self._tray.show_notification(
                                    "ShAI — Heard",
                                    format_tray_notification(payload),
                                )
                elif kind == "speech_error":
                    self._append_output(f"\n[ShAI] Speech error: {payload}\n", "error")
                    self._speech_session = None
                    self._set_speech_state(None)
                elif kind == "speech_done":
                    if self._speech_session is None or not self._speech_session.is_running:
                        self._speech_session = None
                        self._set_speech_state(None)
        except queue.Empty:
            pass
        except Exception:
            LOGGER.exception("Error in _poll_events")

        if self._busy and self._mode == "agent" and not self._has_progress:
            phase = getattr(engine.STATUS, "phase", "Working")
            self.status_var.set(phase)

        self._refresh_task_panel()
        self._refresh_context_meter()

        self.root.after(60, self._poll_events)

    def _on_mode_combo(self, _event=None):
        selected = LABEL_TO_MODE.get(self.mode_var.get(), "agent")
        self.set_mode(selected)

    def set_mode(self, new_mode: str):
        """Switch between ask, read-only, and agent modes."""
        if new_mode not in MODE_LABELS:
            return
        if new_mode == self._mode:
            return
        self._mode = new_mode
        self._refresh_header()
        self._append_output(f"\n[ShAI] Switched to {MODE_LABELS[new_mode]}.\n", "system")

    def _choose_project_dialog(self):
        chosen = filedialog.askdirectory(initialdir=str(engine.ROOT), title="Select Project Folder")
        if chosen:
            self._switch_project(chosen)

    def _switch_project(self, path_str: str):
        try:
            new_root = pathlib.Path(path_str).expanduser().resolve()
            if not new_root.is_dir():
                self._append_output(f"\n[ShAI] Error: '{path_str}' is not a directory.\n", "error")
                return
        except Exception as exc:
            self._append_output(f"\n[ShAI] Error: Invalid path '{path_str}': {exc}\n", "error")
            return

        engine.set_project_root(new_root)
        self.history_store = ConversationHistory(engine.ROOT, "agent", engine.SYSTEM_PROMPT)
        self.history = self.history_store.load()
        self._sync_input_history()
        if engine.PROVIDER != "unknown" and engine.MODEL:
            self.history_store.save(self.history, provider=engine.PROVIDER, model=engine.MODEL)
        self._refresh_header()
        self._append_output(f"\n[ShAI] Project switched to: {engine.ROOT}\n", "system")
        self._render_loaded_history()
        self._save_current_gui_state()

    def _ensure_models_loaded_for_dropdown(self):
        provider = self.provider_var.get().strip()
        if not provider or provider in self._models_cache or self._models_loading_for == provider:
            if provider in self._models_cache:
                self.model_combo["values"] = self._models_cache[provider]
            return
        self._load_models_for_provider_async(provider, auto_select_first=False)

    def _load_models_for_provider_async(
        self, provider: str, auto_select_first: bool = False, quiet: bool = False
    ):
        self._models_loading_for = provider
        if not quiet:
            self.status_var.set(f"Loading {provider} models...")

        def _worker():
            try:
                models = get_available_models(provider)
                error = None
            except Exception as exc:
                models = []
                error = str(exc)
            self._event_queue.put((
                "call",
                lambda: self._on_provider_models_loaded(
                    provider, models, error, auto_select_first, quiet
                ),
                None,
            ))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_provider_models_loaded(
        self,
        provider: str,
        models: list[str],
        error: str | None,
        auto_select_first: bool,
        quiet: bool = False,
    ):
        self._models_loading_for = None
        if not self._busy:
            self.status_var.set("Idle")
        if error:
            if not quiet:
                self._append_output(f"\n[ShAI] Could not load {provider} models: {error}\n", "error")
            return
        self._models_cache[provider] = models
        if self.provider_var.get().strip() != provider:
            return
        self.model_combo["values"] = models
        if auto_select_first and models:
            chosen = engine.MODEL if (engine.PROVIDER == provider and engine.MODEL in models) else models[0]
            self.model_var.set(chosen)
            self._apply_provider_model(provider, chosen)

    def _on_provider_combo(self, _event=None):
        provider = self.provider_var.get().strip()
        if provider in self._models_cache:
            models = self._models_cache[provider]
            self.model_combo["values"] = models
            if models:
                chosen = engine.MODEL if (engine.PROVIDER == provider and engine.MODEL in models) else models[0]
                self.model_var.set(chosen)
                self._apply_provider_model(provider, chosen)
            return
        self._load_models_for_provider_async(provider, auto_select_first=True)

    def _on_model_combo(self, _event=None):
        provider = self.provider_var.get().strip()
        model = self.model_var.get().strip()
        if not provider or not model:
            return "break"
        if provider == engine.PROVIDER and model == engine.MODEL:
            return "break"
        self._apply_provider_model(provider, model)
        return "break"

    def _apply_provider_model(self, new_provider: str, new_model: str):
        engine.PROVIDER = new_provider
        engine.MODEL = new_model
        save_provider_config(new_provider, new_model)
        try:
            metadata = get_selected_model_details(new_provider, new_model)
            context_limit = metadata.get("context_window") or get_saved_context_window(
                new_provider, new_model
            )
            if context_limit:
                save_provider_config(new_provider, new_model, context_limit)
        except Exception:
            pass
        self.history = engine.history_for_new_provider(self.history)
        self.history_store.save(self.history, provider=engine.PROVIDER, model=engine.MODEL)
        self._refresh_header()
        self._append_output(
            f"\n[ShAI] Switched to: {engine.PROVIDER} / {engine.MODEL}\n", "system"
        )

    def _choose_model_dialog(self):
        dialog = ModelPickerDialog(self.root, engine.PROVIDER, engine.MODEL)
        self.root.wait_window(dialog)
        if not dialog.selected:
            return
        self._apply_provider_model(*dialog.selected)

    def _open_settings_dialog(self):
        dialog = SettingsDialog(
            self.root,
            gui_settings={
                "always_on_top": self._always_on_top,
                "start_minimized": self._start_minimized,
                "minimize_to_tray": self._minimize_to_tray,
                "close_to_tray": self._close_to_tray,
                "minimize_on_focus_loss": self._minimize_on_focus_loss,
                "sound_cues": self._sound_cues,
                "disable_listening": self._disable_listening,
            },
            geometry_state=self._settings_geometry,
        )
        self.root.wait_window(dialog)
        state_dirty = False
        if dialog.saved_geometry is not None:
            self._settings_geometry = dialog.saved_geometry
            state_dirty = True
        if dialog.saved_gui_settings is not None:
            self._always_on_top = bool(dialog.saved_gui_settings.get("always_on_top", False))
            self._start_minimized = bool(dialog.saved_gui_settings.get("start_minimized", False))
            self._minimize_to_tray = bool(dialog.saved_gui_settings.get("minimize_to_tray", True))
            self._close_to_tray = bool(dialog.saved_gui_settings.get("close_to_tray", True))
            self._minimize_on_focus_loss = bool(
                dialog.saved_gui_settings.get("minimize_on_focus_loss", False)
            )
            self._sound_cues = bool(dialog.saved_gui_settings.get("sound_cues", True))
            self._sound_cue_success = bool(dialog.saved_gui_settings.get("sound_cue_success", True))
            self._sound_cue_error = bool(dialog.saved_gui_settings.get("sound_cue_error", True))
            self._disable_listening = bool(dialog.saved_gui_settings.get("disable_listening", False))
            self._set_speech_state(self._speech_mode)
            self._apply_always_on_top()
            state_dirty = True
        if state_dirty:
            self._save_current_gui_state()
        if dialog.saved_vars is not None:
            configured = ", ".join(dialog.saved_vars) if dialog.saved_vars else "(none)"
            self._append_output(
                f"\n[ShAI] Updated environment tokens. Active: {configured}\n", "system"
            )

    def _on_enter_key(self, event):
        if event.state & 0x1:  # Shift+Enter -> newline
            return None
        self._on_send_clicked()
        return "break"

    def _on_history_up(self, _event=None):
        if not self._input_history or self._input_history_idx <= 0:
            return None
        cursor_line = self.input_box.index("insert").split(".")[0]
        if cursor_line != "1":
            return None
        self._input_history_idx -= 1
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", self._input_history[self._input_history_idx])
        return "break"

    def _on_history_down(self, _event=None):
        if not self._input_history:
            return None
        cursor_line = self.input_box.index("insert").split(".")[0]
        end_line = self.input_box.index("end-1c").split(".")[0]
        if cursor_line != end_line:
            return None
        if self._input_history_idx < len(self._input_history) - 1:
            self._input_history_idx += 1
            text = self._input_history[self._input_history_idx]
        else:
            self._input_history_idx = len(self._input_history)
            text = ""
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", text)
        return "break"

    def _sync_input_history(self):
        self._input_history = self.history_store.input_history()
        self._input_history_idx = len(self._input_history)

    @staticmethod
    def _extract_message_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
            return "\n".join(parts)
        return ""

    def _render_loaded_history(self):
        rendered = False
        for item in self.history:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role not in ("user", "assistant"):
                continue
            text = self._extract_message_text(item.get("content", "")).strip()
            if not text:
                continue
            if role == "user":
                self._append_output(f"\nYou: {text}\n", "user", is_history=True)
            else:
                self._append_output(f"\n{text}\n", "assistant", is_history=True)
            rendered = True
        if rendered:
            self._insert_session_separator("Current Session")

    def _on_send_clicked(self):
        text = self.input_box.get("1.0", "end-1c").strip()
        if not text:
            return
        self.input_box.delete("1.0", tk.END)
        self.history_store.add_input(text)
        self._sync_input_history()
        self._submit_text(text)

    def _submit_text(self, user_text: str):
        if self._busy:
            if is_speech_cancel_command(user_text):
                self._on_stop_clicked()
                return
            self._queued_messages.append(user_text)
            self._append_output(
                f"\n[ShAI] Queued ({len(self._queued_messages)}): {user_text}\n",
                "system",
            )
            self._refresh_queue_panel(force=True)
            return

        natural_command = resolve_shai_command(user_text, self._mode)
        if natural_command:
            self._append_output(f"\n[ShAI] Interpreted as {natural_command}.\n", "system")
            user_text = natural_command
        user_text = normalize_command(user_text)

        if user_text == "/exit":
            self._exit_app()
            return
        if user_text in ("/model", "/switch", "/switch-model"):
            self._choose_model_dialog()
            return
        if user_text == "/settings":
            self._open_settings_dialog()
            return
        if user_text == "/help":
            self._append_output(f"\nYou: {user_text}\n", "user")
            self._append_output("\n" + HELP_TEXT + "\n", "system", collapse_long=False)
            return
        if user_text == "/reset":
            if not messagebox.askyesno(
                "ShAI — Confirm Reset",
                "Clear conversation history and current task plan?",
                parent=self.root,
            ):
                return
            self.history = [{"role": "system", "content": engine.SYSTEM_PROMPT}]
            self.history_store.clear()
            engine.TASK_PLAN = []
            engine.STATUS.update(task_plan=[])
            engine._save_task_plan()
            self.transcript.config(state="normal")
            self.transcript.delete("1.0", tk.END)
            self.transcript.config(state="disabled")
            self._refresh_task_panel(force=True)
            self._refresh_context_meter(force=True)
            self._append_output("Conversation history cleared.\n", "system")
            return
        if user_text.startswith("/project "):
            self._append_output(f"\nYou: {user_text}\n", "user")
            self._switch_project(user_text[len("/project "):].strip())
            return
        if user_text == "/ask":
            self._append_output(f"\nYou: {user_text}\n", "user")
            self.set_mode("ask")
            return
        if user_text in ("/read-only", "/ro"):
            self._append_output(f"\nYou: {user_text}\n", "user")
            self.set_mode("read-only")
            return
        if user_text == "/code":
            self._append_output(f"\nYou: {user_text}\n", "user")
            self.set_mode("agent")
            return
        if user_text in ("/todo", "/task"):
            self._append_output(f"\nYou: {user_text}\n", "user")
            if not engine.TASK_PLAN:
                self._append_output("\n[ShAI] No tasks in the current plan.\n", "system")
            else:
                lines = ["\nTask plan:"]
                for item in engine.TASK_PLAN:
                    char = {"pending": " ", "in_progress": "~", "completed": "x", "blocked": "!"}.get(
                        item.get("status", "pending"), " "
                    )
                    lines.append(f"  [{char}] {item.get('title', '')}")
                self._append_output("\n".join(lines) + "\n", "system")
            return
        if user_text == "/files":
            self._append_output(f"\nYou: {user_text}\n", "user")
            self._append_output("\n" + engine.list_files() + "\n", "system", collapse_long=True)
            return

        if engine.PROVIDER == "unknown" or not engine.MODEL:
            self._append_output("\n[ShAI] Please select a provider and model first.\n", "warning")
            self._choose_model_dialog()
            if engine.PROVIDER == "unknown" or not engine.MODEL:
                return

        # Commands or model turns that may do network/git/subprocess work run off the UI thread.
        self._cancel_event.clear()
        self._turn_had_error = False
        self._append_output(f"\nYou: {user_text}\n", "user")
        self._set_busy(True, "Running...")
        threading.Thread(
            target=self._run_worker_turn, args=(user_text,), daemon=True
        ).start()

    def _run_worker_turn(self, user_text: str):
        try:
            if user_text == "/status":
                self._event_queue.put(("output", "\n" + engine.git_status() + "\n", "system"))
                return
            if user_text == "/diff":
                self._event_queue.put(("output", "\n" + engine.git_diff() + "\n", "system"))
                return
            if user_text == "/commit-push":
                self._event_queue.put(("output", "\n" + engine.git_commit_push() + "\n", "system"))
                return
            if user_text == "/push":
                self._event_queue.put(("output", "\n" + engine.git_push() + "\n", "system"))
                return
            if user_text.startswith("/commit"):
                message_only = "--message-only" in user_text.split()
                self._event_queue.put(
                    ("output", "\n" + engine.git_commit(message_only=message_only) + "\n", "system")
                )
                return
            if user_text.startswith(("/exec ", "/os-command ")) or user_text in ("/exec", "/os-command"):
                if self._mode != "agent":
                    self._event_queue.put(
                        ("output", "\n[ShAI] os_command is available only in Agent mode.\n", "warning")
                    )
                    return
                command_text = user_text.split(None, 1)[1] if len(user_text.split(None, 1)) > 1 else ""
                executable, arguments = parse_os_command_line(command_text)
                result = engine.os_command(executable, arguments, manual=True)
                display_os_command_result(result)
                return

            if self._mode == "ask":
                self._run_ask_turn(user_text)
            elif self._mode == "read-only":
                self._run_read_only_turn(user_text)
            else:
                engine.ask_model(self.history, user_text)
        except IterationInterrupted:
            self._event_queue.put(("output", "\n[ShAI] Interrupted.\n", "warning"))
        except OSCommandRejected as exc:
            self._event_queue.put(("output", f"\n[ShAI] {exc}\n", "error"))
        except Exception as exc:
            LOGGER.exception("GUI turn failed")
            self._event_queue.put(("output", f"\nError: {exc}\n", "error"))
        finally:
            if engine.PROVIDER != "unknown" and engine.MODEL:
                self.history_store.save(
                    self.history, provider=engine.PROVIDER, model=engine.MODEL
                )
            self._event_queue.put(("done", None, None))

    def _compact_history(self):
        safeguards = engine.load_config().get("agent_safeguards", {})
        if safeguards.get("enabled", True) is False:
            return

        def _limit(key, default):
            val = safeguards.get(key, default)
            return default if isinstance(val, bool) or not isinstance(val, int) or val < 1 else val

        self.history = compact_conversation_history(
            self.history,
            max_chars=_limit("max_history_chars", 90000),
            max_tool_result_chars=_limit("max_tool_result_chars", 8000),
            keep_recent_turns=_limit("keep_recent_turns", 4),
            max_recent_tool_cycles=_limit("max_recent_tool_cycles", 6),
        )

    def _run_ask_turn(self, user_text: str):
        self.history.append({"role": "user", "content": user_text})
        self._compact_history()
        self.history_store.save(self.history, provider=engine.PROVIDER, model=engine.MODEL)

        route = resolve_routed_model("chat", engine.ROOT, engine.PROVIDER, engine.MODEL)
        eff_provider, eff_model = route["provider"], route["model"]
        context_limit = get_context_window(eff_provider, eff_model, engine.load_config())
        estimated_tokens = estimate_messages_tokens(self.history)
        context_status = format_context_status(estimated_tokens, context_limit)
        self._event_queue.put(("output", f"[ShAI] {context_status}\n", "system"))

        response = engine.api_request_for_role(self.history, "chat")
        log_and_display_usage(
            LOGGER, response, get_provider_config(eff_provider)["format"],
            estimated_tokens, context_limit
        )
        display_reasoning_summary(eff_provider, response)
        answer = response_text(response, eff_provider)
        if answer:
            self._event_queue.put(("output", f"\n{answer}\n", "assistant"))
            self.history.append({"role": "assistant", "content": answer})

    def _run_read_only_turn(self, user_text: str):
        self.history.append({"role": "user", "content": user_text})
        self._compact_history()
        self.history_store.save(self.history, provider=engine.PROVIDER, model=engine.MODEL)

        route = resolve_routed_model("orchestrator", engine.ROOT, engine.PROVIDER, engine.MODEL)
        eff_provider, eff_model = route["provider"], route["model"]
        context_limit = get_context_window(eff_provider, eff_model, engine.load_config())
        estimated_tokens = estimate_messages_tokens(self.history)
        context_status = format_context_status(estimated_tokens, context_limit)
        self._event_queue.put(("output", f"[ShAI] {context_status}\n", "system"))

        response = engine.api_request_for_role(self.history, "orchestrator")
        log_and_display_usage(
            LOGGER, response, get_provider_config(eff_provider)["format"],
            estimated_tokens, context_limit
        )
        display_reasoning_summary(eff_provider, response)
        answer, tool_calls = response_text(response, eff_provider)

        if tool_calls:
            if get_provider_config(engine.PROVIDER)["format"] == "anthropic":
                content = []
                if answer:
                    content.append({"type": "text", "text": answer})
                for tc in tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"]),
                    })
                self.history.append({"role": "assistant", "content": content})
            else:
                msg = {"role": "assistant", "content": answer or ""}
                msg["tool_calls"] = tool_calls
                self.history.append(msg)

            for tc in tool_calls:
                call_id = tc["id"]
                fn_data = tc["function"]
                name = fn_data.get("name")
                raw_args = fn_data.get("arguments", "{}")
                self._event_queue.put(("tool", name, False))
                try:
                    args = json.loads(raw_args)
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be an object.")
                except Exception as exc:
                    result = {"error": f"Invalid tool arguments: {exc}"}
                else:
                    if name in TOOL_DISPATCH_READ_ONLY:
                        try:
                            result = TOOL_DISPATCH_READ_ONLY[name](**args)
                        except Exception as exc:
                            result = {"error": str(exc)}
                    else:
                        result = {"error": f"Unknown tool: {name}"}

                tool_content = (
                    "Error: " + result["error"]
                    if isinstance(result, dict) and "error" in result
                    else str(result)
                )
                self.history.append({
                    "role": "tool", "tool_call_id": call_id, "content": tool_content
                })

            response = engine.api_request_for_role(self.history, "orchestrator")
            log_and_display_usage(
                LOGGER, response, get_provider_config(eff_provider)["format"],
                estimate_messages_tokens(self.history), context_limit
            )
            display_reasoning_summary(eff_provider, response)
            answer, _ = response_text(response, eff_provider)

        if answer:
            self._event_queue.put(("output", f"\n{answer}\n", "assistant"))
            self.history.append({"role": "assistant", "content": answer})

    def _restore_gui_state(self):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        def_w, def_h, def_x, def_y = default_window_geometry(screen_w, screen_h)
        saved = load_gui_state(self._state_root)

        w = saved.get("width")
        h = saved.get("height")
        x = saved.get("x")
        y = saved.get("y")

        width = w if isinstance(w, int) and not isinstance(w, bool) and w >= 420 else def_w
        height = h if isinstance(h, int) and not isinstance(h, bool) and h >= 420 else def_h
        pos_x = x if isinstance(x, int) and not isinstance(x, bool) and 0 <= x < screen_w else def_x
        pos_y = y if isinstance(y, int) and not isinstance(y, bool) and 0 <= y < screen_h else def_y

        self._default_geometry = (width, height, pos_x, pos_y)
        self.root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        if saved.get("zoomed") is True:
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass

        saved_mode = saved.get("mode")
        if saved_mode in MODE_LABELS:
            self._mode = saved_mode
        self._restore_listening = saved.get("listening") is True
        self._always_on_top = saved.get("always_on_top") is True
        self._start_minimized = saved.get("start_minimized") is True
        self._minimize_to_tray = saved.get("minimize_to_tray", True) is not False
        self._close_to_tray = saved.get("close_to_tray", True) is not False
        self._minimize_on_focus_loss = saved.get("minimize_on_focus_loss") is True
        self._sound_cues = saved.get("sound_cues", True) is not False
        self._sound_cue_success = saved.get("sound_cue_success", True) is not False
        self._sound_cue_error = saved.get("sound_cue_error", True) is not False
        self._disable_listening = saved.get("disable_listening") is True
        saved_settings_geo = saved.get("settings_geometry")
        self._settings_geometry = saved_settings_geo if isinstance(saved_settings_geo, dict) else None
        self._apply_always_on_top()
        if self._start_minimized:
            try:
                if self._minimize_to_tray:
                    self.root.withdraw()
                else:
                    self.root.iconify()
            except tk.TclError:
                pass
        elif saved.get("window_open") is False:
            try:
                self.root.withdraw()
            except tk.TclError:
                pass

    def _save_current_gui_state(self, window_open: bool | None = None):
        try:
            self.root.update_idletasks()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            # Avoid overwriting valid dimensions when window was never mapped (e.g. headless tests).
            if w <= 1 or h <= 1:
                w, h, x, y = getattr(self, "_default_geometry", default_window_geometry(
                    self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                ))
            zoomed = False
            is_open = True if window_open is None else bool(window_open)
            try:
                state = self.root.state()
                zoomed = state == "zoomed"
                if window_open is None:
                    is_open = state != "withdrawn"
            except tk.TclError:
                pass
            save_gui_state(self._state_root, {
                "width": w,
                "height": h,
                "x": x,
                "y": y,
                "zoomed": zoomed,
                "mode": self._mode,
                "last_project": str(engine.ROOT),
                "listening": self._speech_mode == "continuous",
                "window_open": is_open,
                "always_on_top": self._always_on_top,
                "start_minimized": self._start_minimized,
                "minimize_to_tray": self._minimize_to_tray,
                "close_to_tray": self._close_to_tray,
                "minimize_on_focus_loss": self._minimize_on_focus_loss,
                "sound_cues": self._sound_cues,
                "sound_cue_success": self._sound_cue_success,
                "sound_cue_error": self._sound_cue_error,
                "disable_listening": self._disable_listening,
                "settings_geometry": self._settings_geometry,
            })
            if hasattr(self, "history_store") and hasattr(self, "history"):
                self.history_store.save(
                    self.history,
                    provider=engine.PROVIDER if engine.PROVIDER != "unknown" else None,
                    model=engine.MODEL or None,
                )
        except Exception as exc:
            LOGGER.warning("Could not save GUI state: %s", exc)

    def _set_window_icon(self, ico_path: pathlib.Path, png_path: pathlib.Path):
        if os.name == "nt" and ico_path.is_file():
            try:
                self.root.iconbitmap(str(ico_path))
            except tk.TclError:
                pass
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
                for icon_type, size in ((0, 16), (1, 32)):
                    hicon = user32.LoadImageW(None, str(ico_path), 1, size, size, 0x0010)
                    if hicon:
                        user32.SendMessageW(hwnd, 0x0080, icon_type, hicon)
            except Exception:
                pass
        if png_path.is_file():
            try:
                self._icon_photo = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, self._icon_photo)
            except tk.TclError:
                pass

    def _apply_app_icon(self, enable_tray: bool = True):
        try:
            ico_path, png_path = ensure_app_icons()
            self._set_window_icon(ico_path, png_path)
            if enable_tray and os.name == "nt" and ico_path.is_file():
                self._tray = WindowsTrayIcon(
                    ico_path,
                    tooltip="ShAI — AI Coding Assistant",
                    on_show=lambda: self._event_queue.put(("call", self._show_window, None)),
                    on_toggle_window=lambda: self._event_queue.put(
                        ("call", self._toggle_window, None)
                    ),
                    on_toggle_listening=lambda: self._event_queue.put(
                        ("call", self._on_mic_cont_clicked, None)
                    ),
                    on_settings=lambda: self._event_queue.put(
                        ("call", self._open_settings_dialog, None)
                    ),
                    on_restart=lambda: self._event_queue.put(("call", self.restart_app, None)),
                    on_exit=lambda: self._event_queue.put(("call", self._exit_app, None)),
                )
                self._tray.start()
        except Exception as exc:
            LOGGER.warning("Could not initialize app icon/tray: %s", exc)

    def _force_windows_foreground(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            fg_hwnd = user32.GetForegroundWindow()
            if fg_hwnd and fg_hwnd != hwnd:
                fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
                cur_tid = kernel32.GetCurrentThreadId()
                attached = bool(fg_tid and cur_tid and fg_tid != cur_tid and user32.AttachThreadInput(cur_tid, fg_tid, True))
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                finally:
                    if attached:
                        user32.AttachThreadInput(cur_tid, fg_tid, False)
            else:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _focus_input(self):
        try:
            if self.root.state() == "withdrawn":
                return
            self.root.update_idletasks()
            self._force_windows_foreground()
            self.root.lift()
            self.root.focus_force()
            self.input_box.focus_force()
        except tk.TclError:
            pass

    def _show_window(self):
        try:
            self.root.deiconify()
            self._focus_input()
            self._save_current_gui_state(window_open=True)
        except tk.TclError:
            pass

    def _toggle_window(self):
        try:
            if self.root.state() in ("withdrawn", "iconic"):
                self._show_window()
            else:
                self._hide_to_tray()
        except tk.TclError:
            self._show_window()

    def _hide_to_tray(self):
        self._save_current_gui_state(window_open=False)
        try:
            self.root.withdraw()
        except tk.TclError:
            pass

    def _apply_always_on_top(self):
        try:
            self.root.attributes("-topmost", bool(self._always_on_top))
        except tk.TclError:
            pass

    def _on_window_unmap(self, event):
        if event.widget is not self.root or self._tray is None or not self._minimize_to_tray:
            return
        try:
            if self.root.state() == "iconic":
                self._hide_to_tray()
        except tk.TclError:
            pass

    def _on_window_focus_out(self, event):
        if event.widget is not self.root or self._tray is None or not self._minimize_on_focus_loss:
            return
        try:
            self.root.after(120, self._check_focus_loss_hide)
        except tk.TclError:
            pass

    def _check_focus_loss_hide(self):
        if self._tray is None or not self._minimize_on_focus_loss:
            return
        try:
            if self.root.state() in ("withdrawn", "iconic"):
                return
            if self.root.focus_displayof() is None:
                self._hide_to_tray()
        except tk.TclError:
            pass

    def restart_app(self):
        """Save state, tear down the UI/tray, and relaunch the current GUI process."""
        args = list(sys.argv)
        if args and args[0]:
            args[0] = str(pathlib.Path(args[0]).resolve())
        else:
            args = [str(pathlib.Path(__file__).resolve())]
        self._save_current_gui_state()
        proc = subprocess.Popen([sys.executable, *args], cwd=os.getcwd())
        if sys.platform == "win32" and isinstance(getattr(proc, "pid", None), int):
            try:
                import ctypes

                ctypes.windll.user32.AllowSetForegroundWindow(proc.pid)
            except Exception:
                pass
        self._exit_app()

    def _on_tool_event(self, event: str, tool_label: str, error: bool):
        if event == "started":
            self._event_queue.put(("tool", tool_label, False))
        elif event == "finished" and error:
            self._event_queue.put(("tool", tool_label, True))

    def _on_close(self):
        if self._tray is not None and self._close_to_tray:
            self._hide_to_tray()
            return
        self._exit_app()

    def _on_mic_cont_shortcut(self, _event=None):
        self._on_mic_cont_clicked()
        return "break"

    def _on_mic_cont_clicked(self):
        if self._disable_listening:
            return
        if (
            self._speech_mode == "continuous"
            and self._speech_session is not None
            and self._speech_session.is_running
        ):
            self.stop_speech_listening()
        else:
            self.start_speech_listening(continuous=True)

    def _is_in_taskbar(self) -> bool:
        try:
            if self.root.state() in ("withdrawn", "iconic"):
                return True
        except tk.TclError:
            pass
        try:
            return self.root.focus_displayof() is None
        except tk.TclError:
            return False

    def _update_taskbar_listening_icon(self, listening: bool):
        try:
            ico_path, png_path = (
                ensure_listening_icons() if listening else ensure_app_icons()
            )
            self._set_window_icon(ico_path, png_path)
            if self._tray is not None:
                tip = (
                    "ShAI — Listening (continuous)"
                    if listening
                    else "ShAI — AI Coding Assistant"
                )
                self._tray.set_icon(ico_path, tooltip=tip, listening=listening)
        except Exception:
            pass

    def _set_speech_state(self, mode: str | None):
        self._speech_mode = mode
        idle_cfg = {
            "bg": THEME["surface_alt"],
            "fg": THEME["fg"],
            "activebackground": THEME["border"],
            "activeforeground": THEME["fg"],
        }
        active_cfg = {
            "bg": THEME["error"],
            "fg": "#11111b",
            "activebackground": "#eba0ac",
            "activeforeground": "#11111b",
        }
        disabled_cfg = {
            "bg": THEME["surface"],
            "fg": THEME["muted"],
            "activebackground": THEME["surface"],
            "activeforeground": THEME["muted"],
        }
        if self._disable_listening:
            self.mic_cont_btn.config(**disabled_cfg)
            self._mic_cont_tooltip.text = (
                "Listening disabled — enable it from Settings"
            )
            self._stop_speech_animation()
            self._update_taskbar_listening_icon(False)
        elif mode == "continuous":
            self.mic_cont_btn.config(**active_cfg)
            self._mic_cont_tooltip.text = (
                "Listening continuously — click or press Ctrl+M to stop"
            )
            if not self._busy:
                self.status_var.set("Listening (continuous)...")
            self._start_speech_animation()
            self._update_taskbar_listening_icon(True)
        else:
            self._stop_speech_animation()
            self.mic_cont_btn.config(**idle_cfg)
            self._mic_cont_tooltip.text = (
                "Continuous listening — auto-sends on pause (Ctrl+M)"
            )
            if not self._busy and self.status_var.get().startswith("Listening"):
                self.status_var.set("Idle")
            self._update_taskbar_listening_icon(False)

    def _start_speech_animation(self):
        self._stop_speech_animation()
        self._speech_anim_step = 0
        try:
            self._speech_anim_after_id = self.root.after(
                SPEECH_ANIM_INTERVAL_MS, self._tick_speech_animation
            )
        except tk.TclError:
            self._speech_anim_after_id = None

    def _stop_speech_animation(self):
        if self._speech_anim_after_id is not None:
            try:
                self.root.after_cancel(self._speech_anim_after_id)
            except tk.TclError:
                pass
            self._speech_anim_after_id = None

    def _tick_speech_animation(self):
        self._speech_anim_after_id = None
        if self._speech_mode != "continuous":
            return
        self._speech_anim_step += 1
        color = SPEECH_PULSE_COLORS[self._speech_anim_step % len(SPEECH_PULSE_COLORS)]
        try:
            self.mic_cont_btn.config(bg=color)
            self._speech_anim_after_id = self.root.after(
                SPEECH_ANIM_INTERVAL_MS, self._tick_speech_animation
            )
        except tk.TclError:
            self._speech_anim_after_id = None

    def _cancel_continuous_auto_send(self):
        if self._speech_auto_send_after_id is not None:
            try:
                self.root.after_cancel(self._speech_auto_send_after_id)
            except tk.TclError:
                pass
            self._speech_auto_send_after_id = None

    def _schedule_continuous_auto_send(self):
        self._speech_pending_auto_send = False
        self._cancel_continuous_auto_send()
        try:
            self._speech_auto_send_after_id = self.root.after(
                CONTINUOUS_SPEECH_AUTO_SEND_MS, self._auto_send_continuous_speech
            )
        except tk.TclError:
            self._speech_auto_send_after_id = None

    def _play_cue(self, cue: str):
        if self._sound_cues:
            play_speech_cue(cue)

    def _auto_send_continuous_speech(self):
        self._speech_auto_send_after_id = None
        if self._speech_mode != "continuous":
            self._speech_pending_auto_send = False
            return
        if self._busy:
            self._speech_pending_auto_send = True
            return
        self._speech_pending_auto_send = False
        if self.input_box.get("1.0", "end-1c").strip():
            self._play_cue("send")
            self._on_send_clicked()

    def _insert_transcribed_text(self, text: str):
        snippet = (text or "").strip()
        if not snippet:
            return
        existing = self.input_box.get("1.0", "end-1c")
        if existing and not existing[-1].isspace():
            self.input_box.insert(tk.END, " " + snippet)
        else:
            self.input_box.insert(tk.END, snippet)
        self.input_box.see(tk.END)
        self.input_box.focus_set()

    def _start_wake_word_listening(self):
        if self._disable_listening:
            return
        if not self._wake_word_enabled or self._speech_mode == "continuous":
            return
        if self._wake_session is not None and self._wake_session.is_running:
            return
        session = WakeWordSession(
            on_wake=lambda text: self._event_queue.put(("wake_word", text, None)),
        )
        self._wake_session = session
        session.start()

    def _stop_wake_word_listening(self):
        session = self._wake_session
        self._wake_session = None
        if session is not None:
            session.stop()

    def start_speech_listening(self, continuous: bool = False):
        self._stop_wake_word_listening()
        self.stop_speech_listening(resume_wake_word=False)
        mode = "continuous" if continuous else "single"
        self._set_speech_state(mode)
        session = SpeechToTextSession(
            continuous=continuous,
            on_text=lambda text: self._event_queue.put(("speech_text", text, None)),
            on_error=lambda err: self._event_queue.put(("speech_error", err, None)),
            on_done=lambda: self._event_queue.put(("speech_done", None, None)),
        )
        self._speech_session = session
        session.start()
        self._play_cue("start")

    def stop_speech_listening(self, resume_wake_word: bool = True):
        was_listening = self._speech_session is not None or self._speech_mode is not None
        self._speech_pending_auto_send = False
        self._cancel_continuous_auto_send()
        session = self._speech_session
        self._speech_session = None
        if session is not None:
            session.stop()
        self._set_speech_state(None)
        if was_listening:
            self._play_cue("stop")
        if resume_wake_word and self._wake_word_enabled:
            self._start_wake_word_listening()

    def _exit_app(self):
        self._save_current_gui_state()
        self._wake_word_enabled = False
        self._stop_wake_word_listening()
        self.stop_speech_listening(resume_wake_word=False)
        set_external_cancel_event(None)
        if getattr(engine.STATUS, "on_tool_event", None) == self._on_tool_event:
            engine.STATUS.on_tool_event = None
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr
        self.root.destroy()


def resolve_startup_gui_project(argv: list[str] | None = None, state_root: pathlib.Path | str | None = None) -> str | None:
    """Return CLI project argument if given, else last_project from gui_state.json."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0]:
        return args[0]
    saved = load_gui_state(state_root or APP_STATE_ROOT)
    last_project = saved.get("last_project")
    if isinstance(last_project, str) and last_project.strip():
        candidate = pathlib.Path(last_project).expanduser()
        if candidate.is_dir():
            return str(candidate)
    return None


def main(argv: list[str] | None = None) -> int:
    """Launch the Shai Tkinter GUI."""
    set_windows_app_id()
    initial_project = resolve_startup_gui_project(argv)

    root = tk.Tk()
    ShaiGuiApp(root, initial_project=initial_project)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
