"""Legacy slash-command aliases generated from the JSON command registry."""
from .natural_language import load_command_registry

ALIASES = {}
for _item in load_command_registry()["commands"]:
    for _alias in _item.get("aliases", []):
        ALIASES[_alias] = _item["slash_command"].split()[0]
