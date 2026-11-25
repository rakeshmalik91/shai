"""Small ANSI styling helpers for interactive terminals (including iSH).

Colors are enabled only for TTY output and can be disabled with NO_COLOR,
TERM=dumb, or SHAI_COLOR=0. No external dependency is required.
"""
import os
import sys

_WINDOWS_VT_ENABLED = False


def _enable_windows_ansi():
    """Enable ANSI escape processing on Windows consoles when available."""
    global _WINDOWS_VT_ENABLED
    if _WINDOWS_VT_ENABLED or os.name != "nt":
        return
    _WINDOWS_VT_ENABLED = True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(handle_id)
            if handle in (0, -1):
                continue
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass

RESET = "\033[0m"
STYLES = {
    "user": "\033[1;32m",       # green
    "assistant": "\033[1;36m",  # cyan
    "tool": "\033[1;35m",       # magenta
    "tool_count": "\033[1;33m", # yellow
    "system": "\033[1;34m",     # blue
    "muted": "\033[2;37m",      # dim white
    "success": "\033[32m",      # green
    "warning": "\033[33m",      # yellow
    "error": "\033[1;31m",      # bold red
    "heading": "\033[1;37m",    # bold white
}

def supports_color(stream=None):
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("SHAI_COLOR", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    try:
        if bool(stream.isatty()):
            _enable_windows_ansi()
            return True
        return False
    except (AttributeError, OSError):
        return False

def colorize(text, role, stream=None):
    value = str(text)
    if not supports_color(stream):
        return value
    code = STYLES.get(role)
    return value if not code else code + value + RESET

def label(text, role):
    return colorize(text, role)
