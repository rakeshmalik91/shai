"""CLI command registry and helpers."""
from .aliases import ALIASES
from .definitions import COMMANDS
from .registry import normalize_command
from .natural_language import load_command_registry, resolve_natural_language_command
