"""Command descriptions generated from the JSON command registry."""
from .natural_language import load_command_registry

COMMANDS = {}
for _item in load_command_registry()["commands"]:
    COMMANDS.setdefault(
        _item["slash_command"], _item.get("description", "")
    )
