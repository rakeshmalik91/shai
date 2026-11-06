"""Deterministic natural-language routing for Shai's own slash commands."""
import json
import logging
import re
from pathlib import Path

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "assets" / "command_registry.json"
CONFIG_PATH = Path(__file__).resolve().parents[2] / "assets" / "command_config.json"

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
    registry_path = Path(path) if path else REGISTRY_PATH
    with registry_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("commands"), list):
        raise ValueError("Command registry must contain a commands array.")
    return data


def _enabled(config_path=None):
    path = Path(config_path) if config_path else CONFIG_PATH
    try:
        with path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        settings = config.get("shai_command", {}) if isinstance(config, dict) else {}
        if not settings and path.name != "command_config.json":
            split_path = path.parent / "command_config.json"
            if split_path.is_file():
                try:
                    with split_path.open("r", encoding="utf-8") as split_handle:
                        split_config = json.load(split_handle)
                    if isinstance(split_config, dict) and "shai_command" in split_config:
                        settings = split_config.get("shai_command", {})
                except (OSError, ValueError, TypeError):
                    pass
        return not isinstance(settings, dict) or settings.get("enabled", True) is True
    except (OSError, ValueError, TypeError):
        # Preserve command behavior if an older installation has no feature key.
        return True


def _normalize_phrase(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"[/_]+", " ", value)
    value = re.sub(r"[^a-z0-9\s-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _strip_politeness(value):
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


def resolve_shai_command(text, mode, registry_path=None, config_path=None):
    """Resolve an exact, explicit phrase to a slash command; otherwise return None."""
    if not _enabled(config_path) or not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw or raw.startswith("/") or _NEGATIVE_OR_META.search(raw):
        return None
    phrase = _strip_politeness(_normalize_phrase(raw))
    if not phrase:
        return None
    try:
        registry = _load_registry(registry_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        LOGGER.error("Shai command registry unavailable: %s", error)
        return None

    matches = []
    for item in registry["commands"]:
        if not isinstance(item, dict) or item.get("enabled", True) is not True:
            continue
        if mode not in item.get("modes", []):
            continue
        triggers = item.get("triggers", [])
        if isinstance(triggers, list) and phrase in {
            _normalize_phrase(trigger) for trigger in triggers
        }:
            matches.append(item)
    if len(matches) != 1:
        if len(matches) > 1:
            LOGGER.warning("Ambiguous Shai command phrase=%r ids=%s", phrase,
                           [item.get("id") for item in matches])
        return None
    command = matches[0].get("slash_command")
    if not isinstance(command, str) or not command.startswith("/"):
        LOGGER.error("Invalid slash_command in registry entry id=%r", matches[0].get("id"))
        return None
    LOGGER.info("Shai command matched id=%s mode=%s", matches[0].get("id"), mode)
    return command


def load_command_registry(registry_path=None):
    return _load_registry(registry_path)
