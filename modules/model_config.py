"""Central model capability and limit registry for ShAI."""
from __future__ import annotations
import json
import logging
import pathlib
from copy import deepcopy

LOGGER = logging.getLogger("shai.model_config")
SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[1]
MODEL_CONFIG_FILE = SCRIPT_DIR / "assets" / "model_config.json"
SCHEMA_VERSION = 1
DEFAULT_MODEL_ATTRIBUTES = {
    "context_window": None, "max_output_tokens": None,
    "supported_input_types": ["text"], "supported_output_types": ["text"],
    "supports_tools": None, "supports_structured_output": None,
    "supports_reasoning": None,
}


def _read(path):
    try:
        with pathlib.Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def normalize_attributes(attributes):
    if not isinstance(attributes, dict):
        return {}
    result = {}
    for key in ("context_window", "max_output_tokens"):
        if key in attributes:
            result[key] = _positive_int(attributes[key])
    for key in ("supported_input_types", "supported_output_types"):
        if key in attributes and isinstance(attributes[key], list):
            result[key] = list(dict.fromkeys(
                item.strip() for item in attributes[key]
                if isinstance(item, str) and item.strip()
            ))
    for key in ("supports_tools", "supports_structured_output", "supports_reasoning"):
        if key in attributes and (attributes[key] is None or isinstance(attributes[key], bool)):
            result[key] = attributes[key]
    # Keep additional provider/model metadata extensible.
    for key, value in attributes.items():
        if key not in DEFAULT_MODEL_ATTRIBUTES:
            result[key] = value
    return result


def migrate_legacy_config(legacy):
    legacy = legacy if isinstance(legacy, dict) else {}
    models, overrides = {}, {}
    for old_key, target in (("model_context_windows", models),
                            ("model_context_window_overrides", overrides)):
        values = legacy.get(old_key, {})
        if isinstance(values, dict):
            for key, limit in values.items():
                valid = _positive_int(limit)
                if valid:
                    target[key] = {"context_window": valid}
    active = _positive_int(legacy.get("context_window_tokens"))
    if active and legacy.get("provider") and legacy.get("model"):
        key = "{}::{}".format(legacy["provider"], legacy["model"])
        models.setdefault(key, {})["context_window"] = active
    return {"version": SCHEMA_VERSION, "defaults": deepcopy(DEFAULT_MODEL_ATTRIBUTES),
            "models": models, "overrides": overrides}


def _normalize_config(config):
    config = config if isinstance(config, dict) else {}
    defaults = deepcopy(DEFAULT_MODEL_ATTRIBUTES)
    if isinstance(config.get("defaults"), dict):
        defaults.update(normalize_attributes(config["defaults"]))
    def section(name):
        values = config.get(name, {})
        if not isinstance(values, dict):
            return {}
        return {key: normalize_attributes(value) for key, value in values.items()
                if isinstance(key, str) and isinstance(value, dict)}
    return {"version": SCHEMA_VERSION, "defaults": defaults,
            "models": section("models"), "overrides": section("overrides")}


def load_model_config(path=None, migrate=True):
    path = pathlib.Path(path or MODEL_CONFIG_FILE)
    config = _read(path)
    if config.get("version") != SCHEMA_VERSION:
        if "model_context_windows" in config or "model_context_window_overrides" in config:
            config = migrate_legacy_config(config)
        elif migrate and not path.exists():
            legacy_path = path.with_name("context_window_config.json")
            if legacy_path.is_file():
                config = migrate_legacy_config(_read(legacy_path))
                save_model_config(config, path)
        else:
            config = {}
    return _normalize_config(config)


def save_model_config(config, path=None):
    path = pathlib.Path(path or MODEL_CONFIG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_config(config)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(path)
    return normalized


def get_model_config(provider, model, config=None):
    config = _normalize_config(config) if isinstance(config, dict) else load_model_config()
    key = "{}::{}".format(provider, model)
    result = deepcopy(config["defaults"])
    result.update(config["models"].get(key, {}))
    result.update(config["overrides"].get(key, {}))
    return result


def update_model_metadata(provider, model, attributes, path=None):
    config = load_model_config(path)
    key = "{}::{}".format(provider, model)
    current = config["models"].get(key, {})
    current.update(normalize_attributes(attributes))
    config["models"][key] = current
    save_model_config(config, path)
    return get_model_config(provider, model, config)
