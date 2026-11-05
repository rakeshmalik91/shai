"""Command registry facade and canonicalization."""
from .aliases import ALIASES
from .definitions import COMMANDS

def normalize_command(command):
    """Normalize a complete slash command while preserving its arguments."""
    if not isinstance(command, str):
        return command
    head, separator, tail = command.partition(" ")
    canonical = ALIASES.get(head, head)
    return canonical + (separator + tail if separator else "")
