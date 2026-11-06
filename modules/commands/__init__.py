"""CLI command registry and helpers."""
from .aliases import ALIASES
from .definitions import COMMANDS
from .registry import normalize_command
from .shai_command import load_command_registry, resolve_shai_command
