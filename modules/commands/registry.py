"""Command registry facade and canonicalization."""
from .aliases import ALIASES
from .definitions import COMMANDS
from .natural_language import load_command_registry, resolve_natural_language_command


def normalize_command(command):
    """Normalize a slash command while preserving its argument string."""
    if not isinstance(command, str):
        return command
    stripped = command.strip()
    if not stripped.startswith("/"):
        return stripped
    parts = stripped.split(None, 1)
    head = parts[0]
    canonical = ALIASES.get(head, head)
    return canonical + ((" " + parts[1]) if len(parts) > 1 else "")
