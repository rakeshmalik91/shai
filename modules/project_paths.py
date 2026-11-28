"""Resolve the currently selected working project independently of Shai's install path."""
from __future__ import annotations

import pathlib
import threading

_LOCK = threading.RLock()
_ACTIVE_PROJECT_ROOT = pathlib.Path.cwd().expanduser().resolve()


def set_active_project_root(path):
    """Set the project whose .shai directory owns all project-local state."""
    global _ACTIVE_PROJECT_ROOT
    resolved = pathlib.Path(path).expanduser().resolve()
    with _LOCK:
        _ACTIVE_PROJECT_ROOT = resolved
    # Keep compatibility constants synchronized for integrations that import
    # them directly instead of calling the path helpers.
    import sys
    model_module = sys.modules.get("modules.model_config")
    if model_module is not None:
        model_module.MODEL_CONFIG_FILE = resolved / ".shai" / "model_config.json"
    logging_module = sys.modules.get("modules.logging_config")
    if logging_module is not None:
        logging_module.DEFAULT_LOG_DIR = resolved / ".shai" / "logs"
    return resolved


def get_active_project_root():
    """Return the selected project root, never the Shai source/repository root."""
    with _LOCK:
        return _ACTIVE_PROJECT_ROOT


def get_project_state_dir():
    """Return the selected project's .shai directory."""
    return get_active_project_root() / ".shai"


def resolve_startup_project_root(argv=None):
    """Resolve an optional CLI project argument without mistaking test flags for paths."""
    import sys
    args = sys.argv if argv is None else argv
    if len(args) > 1 and not str(args[1]).startswith("-"):
        candidate = pathlib.Path(args[1]).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    return pathlib.Path.cwd().resolve()
