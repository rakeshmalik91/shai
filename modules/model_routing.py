"""Project-scoped, priority-ordered model routing.

The tracked assets/model_routing.json is a schema/default template. The active
project's .Shai/model_routing.json supplies project-specific role overrides.
"""
from __future__ import annotations

import json
import logging
import pathlib
import time

LOGGER = logging.getLogger("shai.model_routing")
ROLES = (
    "orchestrator", "planner", "code", "debug", "test", "chat",
    "commit_message", "review", "documentation", "research",
)
SCHEMA_VERSION = 1
SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE_FILE = SCRIPT_DIR / "assets" / "model_routing.json"
_CACHE = {}
_CACHE_TTL = 30


def _read_json(path):
    try:
        with pathlib.Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def load_routing_config(project_root):
    """Load tracked template and apply the active project's role overrides."""
    template = _read_json(TEMPLATE_FILE)
    if template.get("version") != SCHEMA_VERSION:
        template = {"version": SCHEMA_VERSION, "models": {role: [] for role in ROLES}}
    models = template.get("models")
    if not isinstance(models, dict):
        models = {role: [] for role in ROLES}
    models = {role: list(models.get(role, [])) if isinstance(models.get(role, []), list) else []
              for role in ROLES}

    project_file = pathlib.Path(project_root).expanduser().resolve() / ".Shai" / "model_routing.json"
    project = _read_json(project_file)
    if project:
        if project.get("version") != SCHEMA_VERSION:
            LOGGER.warning("Ignoring unsupported model routing version in %s", project_file)
        else:
            project_models = project.get("models", {})
            if isinstance(project_models, dict):
                for role in ROLES:
                    if role in project_models:
                        value = project_models[role]
                        if isinstance(value, list):
                            models[role] = value
                        else:
                            LOGGER.warning("Ignoring invalid model route list for role=%s", role)
    template["models"] = models
    return template


def _valid_candidates(items):
    candidates, seen = [], set()
    if not isinstance(items, list):
        return candidates
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        provider, model = item.get("provider"), item.get("model")
        priority = item.get("priority", index + 1)
        if (not isinstance(provider, str) or not provider.strip()
                or not isinstance(model, str) or not model.strip()
                or isinstance(priority, bool) or not isinstance(priority, int)
                or priority < 1):
            continue
        key = (provider.strip(), model.strip())
        if key in seen:
            continue
        seen.add(key)
        candidates.append({"provider": key[0], "model": key[1], "priority": priority})
    return sorted(candidates, key=lambda item: item["priority"])


def resolve_model(task_role, project_root, default_provider, default_model,
                  provider_registry=None, model_discovery=None):
    """Resolve the first configured, discoverable route; otherwise default.

    Discovery is cached briefly per project/provider. No prompt or credential
    is written to the routing configuration.
    """
    fallback = {
        "provider": default_provider, "model": default_model,
        "role": task_role if task_role in ROLES else "default",
        "routed": False, "reason": "default model",
    }
    if task_role not in ROLES or not default_provider or not default_model:
        return fallback

    config = load_routing_config(project_root)
    candidates = _valid_candidates(config.get("models", {}).get(task_role, []))
    if not candidates:
        return fallback

    if provider_registry is None:
        from modules.provider_selection import DEFAULT_PROVIDERS as provider_registry
    if model_discovery is None:
        from modules.provider_selection import get_available_models as model_discovery

    project_key = str(pathlib.Path(project_root).expanduser().resolve())
    for candidate in candidates:
        provider, model = candidate["provider"], candidate["model"]
        if provider not in provider_registry:
            LOGGER.warning("Skipping route role=%s: unknown provider=%s", task_role, provider)
            continue
        cache_key = (project_key, provider)
        now = time.monotonic()
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > now:
            available = cached[1]
        else:
            try:
                available = set(model_discovery(provider))
                _CACHE[cache_key] = (now + _CACHE_TTL, available)
            except Exception as error:
                LOGGER.warning("Model discovery failed for route provider=%s: %s",
                               provider, str(error)[:240])
                _CACHE[cache_key] = (now + min(_CACHE_TTL, 5), set())
                available = set()
        if model not in available:
            continue
        LOGGER.info("Model route role=%s selected=%s::%s priority=%d",
                    task_role, provider, model, candidate["priority"])
        return {
            "provider": provider, "model": model, "role": task_role,
            "routed": True, "reason": "priority route",
        }
    fallback["reason"] = "no configured candidate currently available"
    LOGGER.info("Model route role=%s using default=%s::%s reason=%s",
                task_role, default_provider, default_model, fallback["reason"])
    return fallback


def clear_availability_cache():
    _CACHE.clear()
