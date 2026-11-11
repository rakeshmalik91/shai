"""Project-scoped, priority-ordered model routing.

The tracked assets/model_routing.json is a schema/default template. The active
project's .shai/model_routing.json supplies project-specific role overrides.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re

LOGGER = logging.getLogger("shai.model_routing")
ROLES = (
    "orchestrator", "planner", "code", "debug", "test", "chat",
    "commit_message", "review", "documentation", "research",
)
SCHEMA_VERSION = 1
SCRIPT_DIR = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE_FILE = SCRIPT_DIR / "assets" / "model_routing.json"


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
    default = template.get("default")
    default = dict(default) if isinstance(default, dict) else {
        "provider": None, "model": None
    }
    models = template.get("models")
    if not isinstance(models, dict):
        models = {role: [] for role in ROLES}
    models = {role: list(models.get(role, [])) if isinstance(models.get(role, []), list) else []
              for role in ROLES}

    project_file = pathlib.Path(project_root).expanduser().resolve() / ".shai" / "model_routing.json"
    project = _read_json(project_file)
    if project:
        if project.get("version") != SCHEMA_VERSION:
            LOGGER.warning("Ignoring unsupported model routing version in %s", project_file)
        else:
            project_default = project.get("default", {})
            if (isinstance(project_default, dict)
                    and isinstance(project_default.get("provider"), str)
                    and project_default["provider"].strip()
                    and isinstance(project_default.get("model"), str)
                    and project_default["model"].strip()):
                default["provider"] = project_default["provider"]
                default["model"] = project_default["model"]
            project_models = project.get("models", {})
            if isinstance(project_models, dict):
                for role in ROLES:
                    if role in project_models:
                        value = project_models[role]
                        if isinstance(value, list):
                            models[role] = value
                        else:
                            LOGGER.warning("Ignoring invalid model route list for role=%s", role)
    template["default"] = default
    template["models"] = models
    return template


def load_default_model(project_root):
    """Return the selected project's default provider/model pair."""
    default = load_routing_config(project_root).get("default", {})
    if not isinstance(default, dict):
        return {}
    provider, model = default.get("provider"), default.get("model")
    if (isinstance(provider, str) and provider.strip()
            and isinstance(model, str) and model.strip()):
        return {"provider": provider, "model": model}
    return {}


def save_default_model(project_root, provider, model):
    """Persist the default provider/model in the selected project's routing file."""
    project_file = pathlib.Path(project_root).expanduser().resolve() / ".shai" / "model_routing.json"
    project_file.parent.mkdir(parents=True, exist_ok=True)
    data = _read_json(project_file)
    if data.get("version") != SCHEMA_VERSION:
        data = {"version": SCHEMA_VERSION}
    default = data.get("default")
    if not isinstance(default, dict):
        default = {}
    default["provider"] = provider
    default["model"] = model
    data["default"] = default
    models = data.get("models")
    if isinstance(models, dict):
        data["models"] = models
    else:
        data.pop("models", None)
    if "availability" not in data:
        template = _read_json(TEMPLATE_FILE)
        if isinstance(template.get("availability"), dict):
            data["availability"] = template["availability"]
    temporary = project_file.with_name(project_file.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + chr(10), encoding="utf-8")
    temporary.replace(project_file)
    return project_file


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


def resolve_model_candidates(task_role, project_root, default_provider, default_model,
                             provider_registry=None, model_discovery=None):
    """Return configured route candidates followed by the active default.

    No provider catalogue is queried. Candidate availability is checked lazily
    by the first actual completion request, so unused/discontinued models stay
    silent and cannot delay startup.
    """
    fallback = {
        "provider": default_provider, "model": default_model,
        "role": task_role if task_role in ROLES else "default",
        "routed": False, "reason": "default model",
    }
    if task_role not in ROLES or not default_provider or not default_model:
        return [fallback]

    config = load_routing_config(project_root)
    candidates = _valid_candidates(config.get("models", {}).get(task_role, []))
    if provider_registry is None:
        from modules.provider_selection import DEFAULT_PROVIDERS as provider_registry

    routes = []
    seen = set()
    for candidate in candidates:
        provider, model = candidate["provider"], candidate["model"]
        if provider not in provider_registry:
            LOGGER.warning("Skipping route role=%s: unknown provider=%s", task_role, provider)
            continue
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        is_active = key == (default_provider, default_model)
        routes.append({
            "provider": provider, "model": model, "role": task_role,
            "routed": not is_active,
            "reason": "active default model" if is_active else "priority route",
        })

    if (default_provider, default_model) not in seen:
        routes.append(fallback)
    if not routes:
        routes.append(fallback)
    return routes


def resolve_model(task_role, project_root, default_provider, default_model,
                  provider_registry=None, model_discovery=None):
    """Resolve the highest-priority explicitly configured route.

    Kept as a single-route compatibility API. Runtime callers should use
    ``resolve_model_candidates`` and ``request_with_model_fallback`` so a
    discontinued inactive route can be skipped without surfacing an error.
    ``model_discovery`` remains accepted but is deliberately not invoked.
    """
    return resolve_model_candidates(
        task_role, project_root, default_provider, default_model,
        provider_registry=provider_registry, model_discovery=model_discovery,
    )[0]


def is_model_unavailable_error(error):
    """Recognize explicit model-name/availability failures, not general API errors."""
    detail = str(error).lower()
    markers = (
        "model_not_found", "model not found", "unknown model",
        "no such model", "model does not exist", "model doesn't exist",
        "model is unavailable", "model unavailable", "no endpoints found for model",
        "no available model found", "model is not available",
    )
    if any(marker in detail for marker in markers):
        return True
    # Providers often interpolate the model identifier between these words,
    # e.g. "model 'xyz' was not found".
    return bool(re.search(
        r"model.{0,160}(?:not found|was not found|does not exist|doesn't exist|is unavailable|is not available)",
        detail,
    ))


def request_with_model_fallback(routes, request_callable):
    """Try configured routes in priority order, silently skipping dead models.

    Only a model explicitly marked as a routed/inactive candidate is eligible
    for silent failover. Errors from the user's active default model are always
    propagated.
    """
    for route in routes:
        try:
            return request_callable(route)
        except Exception as error:
            if route.get("routed") and is_model_unavailable_error(error):
                LOGGER.debug(
                    "Skipping unavailable inactive model route role=%s provider=%s model=%s",
                    route.get("role"), route.get("provider"), route.get("model"),
                )
                continue
            raise
    raise RuntimeError("No model route was available for this request.")

def clear_availability_cache():
    """Backward-compatible no-op; routing no longer caches live discovery."""
    return None
