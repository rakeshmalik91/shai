"""Natural-language aliases for registered Shai commands.

This resolver is deliberately conservative: it only maps a complete, explicit
phrase from the JSON registry. It never constructs command arguments and never
executes a command itself.
"""
import json
import logging
import re
from pathlib import Path

LOGGER = logging.getLogger(__name__)
_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "assets" / "command_registry.json"

_POLITE_PREFIXES = (
    "could you please ", "could you ", "would you please ", "would you ",
    "can you please ", "can you ", "please ", "kindly ",
)
_POLITE_SUFFIXES = (" please", " for me", " now")
_NEGATIVE_OR_META = re.compile(
    r"\b(don't|do not|never|should i|what does|what is|explain|"
    r"how does|how do i|meaning of|instead of|rather than)\b",
    re.IGNORECASE,
)


def _load_registry(path=None):
    registry_path = Path(path) if path else _REGISTRY_PATH
    with registry_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("commands"), list):
        raise ValueError("Command registry must contain a commands array.")
    return data


def _normalize_phrase(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"[/_]+", " ", value)
    value = re.sub(r"[^a-z0-9\s-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _strip_politeness(value):
    value = value.strip()
    changed = True
    while changed:
        changed = False
        for prefix in _POLITE_PREFIXES:
            if value.startswith(prefix):
                value = value[len(prefix):].strip()
                changed = True
                break
    for suffix in _POLITE_SUFFIXES:
        if value.endswith(suffix):
            value = value[:-len(suffix)].strip()
            break
    return value


def resolve_natural_language_command(text, mode, registry_path=None):
    """Return a canonical slash command for a clear registry phrase, else None.

    No fuzzy or partial matching is used. This prevents ordinary discussion,
    quoted examples, and vague requests from accidentally invoking commands.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw or raw.startswith("/"):
        return None
    if _NEGATIVE_OR_META.search(raw):
        return None

    phrase = _strip_politeness(_normalize_phrase(raw))
    if not phrase:
        return None

    try:
        registry = _load_registry(registry_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        LOGGER.error("Natural-language command registry unavailable: %s", error)
        return None

    matches = []
    for item in registry["commands"]:
        if not isinstance(item, dict) or mode not in item.get("modes", []):
            continue
        triggers = item.get("triggers", [])
        if not isinstance(triggers, list):
            continue
        if phrase in {_normalize_phrase(trigger) for trigger in triggers}:
            matches.append(item)

    if len(matches) != 1:
        if len(matches) > 1:
            LOGGER.warning("Ambiguous natural-language command phrase=%r ids=%s",
                           phrase, [item.get("id") for item in matches])
        return None

    command = matches[0].get("slash_command")
    if not isinstance(command, str) or not command.startswith("/"):
        LOGGER.error("Invalid slash_command in registry entry id=%r",
                     matches[0].get("id"))
        return None
    LOGGER.info("Natural-language command matched id=%s mode=%s",
                matches[0].get("id"), mode)
    return command


def load_command_registry(registry_path=None):
    """Public read-only registry accessor for help, diagnostics, and tests."""
    return _load_registry(registry_path)
