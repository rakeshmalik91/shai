"""Shared interactive provider and model selection for Shai."""

import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent.parent
CONFIG_FILE = SCRIPT_DIR / "assets" / "shai_config.json"

MODEL_CONTEXT_WINDOWS = {}
# Retain catalogue records only in memory so Kilo metadata can be persisted
# for the one model the user selects, without a second undocumented request.
MODEL_CATALOGUE_ITEMS = {}

DEFAULT_PROVIDERS = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "models_url": "https://openrouter.ai/api/v1/models",
        "model_detail_url": "https://openrouter.ai/api/v1/models/{model}/endpoints",
        "key_env": "OPENROUTER_API_KEY",
        "format": "openai",
        "auth_type": "bearer",
        "headers": {"HTTP-Referer": "https://openrouter.ai/", "X-Title": "Shai"},
        "models_filter": "free_only",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "model_detail_url": "https://generativelanguage.googleapis.com/v1beta/models/{model}",
        "key_env": "GEMINI_API_KEY",
        "format": "openai",
        "auth_type": "query",
        "auth_query_param": "key",
        "models_filter": "all",
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "models_url": "https://api.anthropic.com/v1/models?limit=1000",
        "model_detail_url": "https://api.anthropic.com/v1/models/{model}",
        "key_env": "ANTHROPIC_API_KEY",
        "format": "anthropic",
        "auth_type": "header",
        "auth_header": "x-api-key",
        "headers": {"anthropic-version": "2023-06-01"},
        "models_filter": "all",
    },
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "models_url": "https://api.openai.com/v1/models",
        "model_detail_url": "https://api.openai.com/v1/models/{model}",
        "key_env": "OPENAI_API_KEY",
        "format": "openai",
        "auth_type": "bearer",
        "models_filter": "all",
    },
    "kilo": {
        "url": "https://ai-gateway.kilo.ai/api/v1/chat/completions",
        "models_url": "https://ai-gateway.kilo.ai/api/v1/models",
        "key_env": "KILO_API_KEY",
        "format": "openai",
        "auth_type": "bearer",
        "models_filter": "exclude_free",
    },
    "kilo-free": {
        "url": "https://ai-gateway.kilo.ai/api/v1/chat/completions",
        "models_url": "https://ai-gateway.kilo.ai/api/v1/models",
        "key_env": "KILO_API_KEY",
        "format": "openai",
        "auth_type": "bearer",
        "models_filter": "free_only",
    },
    "ollama": {
        "url": "http://127.0.0.1:11434/v1/chat/completions",
        "models_url": "http://127.0.0.1:11434/api/tags",
        "key_env": None,
        "format": "openai",
        "auth_type": "none",
        "models_filter": "ollama",
        "timeout_seconds": 300,
    },
}

# Public compatibility mapping; use get_provider_config() for configured values.
PROVIDERS = DEFAULT_PROVIDERS



def _merge_config_section(base, extra):
    if not isinstance(base, dict) or not isinstance(extra, dict):
        return
    for k, v in extra.items():
        if k not in base:
            base[k] = v
        elif isinstance(base[k], dict) and isinstance(v, dict):
            _merge_config_section(base[k], v)


def load_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {}
        for filename in ("provider_config.json", "model_config.json", "cli_config.json", "logging_config.json", "command_config.json"):
            try:
                with (CONFIG_FILE.parent / filename).open("r", encoding="utf-8") as handle:
                    section = json.load(handle)
                if isinstance(section, dict):
                    if filename == "model_config.json":
                        data.setdefault("model_config", {})
                        _merge_config_section(data["model_config"], section)
                    else:
                        _merge_config_section(data, section)
            except (OSError, ValueError, TypeError):
                pass
        return data
    except (OSError, ValueError):
        return {}


def get_provider_config(provider):
    """Return defaults merged with shai_config.json's providers.<name> overrides.

    Supported settings include url/chat_url, models_url, key_env,
    format, auth_type, auth_header, auth_prefix, auth_query_param, headers,
    models_filter and timeout_seconds. A base_url override derives the
    conventional OpenAI-compatible endpoints unless explicitly overridden.
    """
    if provider not in DEFAULT_PROVIDERS:
        raise KeyError("Unknown provider: " + str(provider))
    result = dict(DEFAULT_PROVIDERS[provider])
    result["headers"] = dict(result.get("headers", {}))
    config = load_config()
    overrides = config.get("providers", {})
    override = overrides.get(provider, {}) if isinstance(overrides, dict) else {}
    if isinstance(override, dict):
        result.update({key: value for key, value in override.items()
                       if key != "headers"})
        if isinstance(override.get("headers"), dict):
            result["headers"].update(override["headers"])
        if override.get("base_url"):
            base = str(override["base_url"]).rstrip("/")
            if "chat_url" not in override and "url" not in override:
                result["url"] = base + "/chat/completions"
            if "models_url" not in override:
                result["models_url"] = base + "/models"
        if "chat_url" in override:
            result["url"] = override["chat_url"]
    return result


def provider_api_key(provider):
    """Return configured API key, or None for keyless/local providers."""
    config = get_provider_config(provider)
    env_name = config.get("key_env")
    if not env_name:
        return None
    value = os.environ.get(env_name)
    if not value:
        raise RuntimeError(str(env_name) + " is not set. Source setup_keys.sh first.")
    return value


def build_provider_url(provider, endpoint="chat", api_key=None):
    config = get_provider_config(provider)
    url = config.get("models_url") if endpoint == "models" else config.get("url")
    if not url:
        raise RuntimeError("No {} URL configured for provider '{}'.".format(endpoint, provider))
    if config.get("auth_type") == "query" and api_key:
        parameter = config.get("auth_query_param", "key")
        separator = "&" if "?" in url else "?"
        url += separator + urllib.parse.urlencode({parameter: api_key})
    return url


def build_provider_headers(provider, api_key=None, extra_headers=None):
    config = get_provider_config(provider)
    headers = dict(config.get("headers", {}))
    auth_type = config.get("auth_type", "bearer")
    if auth_type == "bearer" and api_key:
        headers["Authorization"] = str(config.get("auth_prefix", "Bearer ")) + api_key
    elif auth_type == "header" and api_key:
        headers[config.get("auth_header", "Authorization")] = api_key
    elif auth_type == "none":
        pass
    headers["Content-Type"] = "application/json"
    if isinstance(extra_headers, dict):
        headers.update(extra_headers)
    return headers


def provider_timeout(provider, fallback=300):
    value = get_provider_config(provider).get("timeout_seconds", fallback)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else fallback


def save_config(provider, model, context_window_tokens=None):
    """Persist selected provider/model and provider-reported model metadata."""
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            config = {}
    config["provider"], config["model"] = provider, model
    temporary = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(CONFIG_FILE)

    from modules.model_config import load_model_config, save_model_config
    model_config = load_model_config(CONFIG_FILE.parent / "model_config.json")
    key = "{}::{}".format(provider, model)
    metadata = model_config["models"].setdefault(key, {})
    if isinstance(context_window_tokens, int) and not isinstance(context_window_tokens, bool) and context_window_tokens > 0:
        metadata["context_window"] = context_window_tokens
    save_model_config(model_config, CONFIG_FILE.parent / "model_config.json")


def _get_json(url, key, provider, timeout=None):
    config = get_provider_config(provider)
    # Preserve caller-supplied URL for backwards-compatible tests/custom calls.
    if config.get("auth_type") == "query" and key:
        separator = "&" if "?" in url else "?"
        url += separator + urllib.parse.urlencode({
            config.get("auth_query_param", "key"): key
        })
    headers = build_provider_headers(provider, key)
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=(timeout or provider_timeout(provider))) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "Model-list API error {}: {}".format(error.code, detail[:1500])
        )
    except urllib.error.URLError as error:
        raise RuntimeError("Network error while loading models: " + str(error))


def _model_context_limit(item):
    """Extract a provider-reported input context limit when available."""
    if not isinstance(item, dict):
        return None
    for key in ("context_length", "context_window", "max_context_length",
                "input_token_limit", "inputTokenLimit"):
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def get_saved_context_window(provider, model):
    """Return the effective model context limit, including user overrides."""
    from modules.model_config import get_model_config, load_model_config
    return get_model_config(provider, model, load_model_config(
        CONFIG_FILE.parent / "model_config.json"
    )).get("context_window")


def _model_metadata(item, provider=None):
    """Compatibility wrapper; provider-specific parsing lives in handlers."""
    from modules.providers import get_handler
    return get_handler(provider or "openai").metadata(item)


def _save_discovered_metadata(provider, model, metadata):
    if not metadata:
        return
    try:
        from modules.model_config import update_model_metadata
        update_model_metadata(provider, model, metadata,
                              CONFIG_FILE.parent / "model_config.json")
    except (OSError, ValueError, TypeError) as error:
        print("Warning: could not save model metadata: {}".format(error))


def get_available_models(provider):
    """Fetch model IDs only; never fetch or persist metadata for the catalogue."""
    config = get_provider_config(provider)
    key = provider_api_key(provider)
    url = build_provider_url(provider, "models", key)
    # This request is only for the list of model IDs. Detailed metadata is
    # fetched later, once the user has selected exactly one model.
    data = _get_json(url, None if config.get("auth_type") == "query" else key, provider)

    from modules.providers import get_handler
    handler = get_handler(provider)
    models = handler.discover_models(data)
    handler.cache_catalogue(data, MODEL_CATALOGUE_ITEMS, provider)

    filter_mode = config.get("models_filter", "all")
    if filter_mode == "free_only":
        models = [model for model in models if model.endswith(":free")]
    elif filter_mode == "exclude_free":
        models = [model for model in models if not model.endswith(":free")]
    return sorted(set(model for model in models if model))


def get_selected_model_details(provider, model, timeout=8):
    """Fetch metadata for exactly one explicitly selected model.

    This deliberately avoids the provider-wide model catalogue. Detail lookup
    is best-effort: unsupported detail endpoints or discontinued models do not
    prevent the user from selecting the model and trying a completion request.
    """
    config = get_provider_config(provider)
    from modules.providers import get_handler
    handler = get_handler(provider)
    if handler.uses_catalogue_metadata:
        metadata = handler.catalogue_metadata(model, MODEL_CATALOGUE_ITEMS, provider)
        if metadata:
            _save_discovered_metadata(provider, model, metadata)
            if metadata.get("context_window"):
                MODEL_CONTEXT_WINDOWS[model] = metadata["context_window"]
        return metadata

    template = config.get("model_detail_url")
    if not template or provider == "ollama":
        return {}
    encoded_model = urllib.parse.quote(str(model).strip(), safe="/:")
    url = str(template).format(model=encoded_model)
    key = provider_api_key(provider)
    if config.get("auth_type") == "query" and key:
        parameter = config.get("auth_query_param", "key")
        separator = "&" if "?" in url else "?"
        url += separator + urllib.parse.urlencode({parameter: key})
    try:
        payload = _get_json(
            url, None if config.get("auth_type") == "query" else key,
            provider, timeout=timeout
        )
    except (RuntimeError, OSError, ValueError, TypeError):
        return {}

    item = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        item = payload["data"]
    metadata = _model_metadata(item, provider=provider)
    if metadata:
        _save_discovered_metadata(provider, model, metadata)
        if metadata.get("context_window"):
            MODEL_CONTEXT_WINDOWS[model] = metadata["context_window"]
    return metadata


def choose_model(models, preferred=None):
    if not models:
        raise RuntimeError("No models are currently available.")

    print("\nAvailable models:\n")
    for number, model in enumerate(models, 1):
        marker = " [last used]" if model == preferred else ""
        print("{:3}. {}{}".format(number, model, marker))

    if preferred in models:
        default_number = models.index(preferred) + 1
        prompt = "\nChoose model [{}] (Enter keeps it): ".format(default_number)
    else:
        default_number = None
        prompt = "\nChoose model number: "

    while True:
        choice = input(prompt).strip()
        if not choice and default_number is not None:
            return models[default_number - 1]
        try:
            index = int(choice) - 1
            if 0 <= index < len(models):
                return models[index]
        except ValueError:
            pass
        print("Invalid selection. Enter a number from the list.")


def initialize_provider(auto_select=False):
    """Prompt for provider, list model IDs, then fetch details for the selection.

    Provider 0 reuses the last provider/model pair and skips model discovery.

    If auto_select is True, automatically use the last saved provider/model
    when the provider and model are configured; no catalogue request is made.
    """
    saved = load_config()
    saved_provider = saved.get("provider")
    saved_model = saved.get("model")

    # Startup must not depend on a live model-catalogue request. Reuse the
    # explicitly saved provider/model. Interactive selection fetches model IDs
    # only, then metadata for the single model selected by the user.
    if auto_select and saved_provider in PROVIDERS and saved_model:
        context_limit = get_saved_context_window(saved_provider, saved_model)
        if context_limit:
            print("Model context window: {:,} tokens".format(context_limit))
        else:
            print("Model context window: unknown (configure it in model_config.json if needed)")
        print("Selected: {} / {} (auto-selected; model discovery skipped)".format(
            saved_provider, saved_model
        ))
        return saved_provider, saved_model

    names = list(PROVIDERS)
    print("\nSelect provider:")
    if saved_provider in PROVIDERS and saved_model:
        print("  0. Last used: {} / {}".format(saved_provider, saved_model))
    else:
        print("  0. Last used: (not configured)")
    for number, name in enumerate(names, 1):
        labels = {
            "kilo": "Kilo Gateway (paid/non-free models)",
            "kilo-free": "Kilo Gateway (free models only)",
            "ollama": "Ollama (local)",
        }
        label = labels.get(name, name)
        print("{:3}. {}".format(number, label))

    while True:
        choice = input("\nChoose provider number: ").strip()
        if choice == "0":
            if saved_provider not in PROVIDERS or not saved_model:
                print("No previous provider/model selection is saved.")
                continue
            provider, model = saved_provider, saved_model
            print("Using last used: {} / {}".format(provider, model))
            return provider, model
        try:
            index = int(choice) - 1
            if 0 <= index < len(names):
                provider = names[index]
                break
        except ValueError:
            pass
        print("Invalid selection. Enter 0 or a provider number.")

    print("\nProvider:", provider)
    print("Loading available models...")
    models = get_available_models(provider)
    preferred = saved_model if saved_provider == provider else None
    if not models:
        raise RuntimeError("No models are currently available for provider '{}'.".format(provider))
    model = choose_model(models, preferred=preferred)

    # Persist the user's explicit selection immediately. Model-detail lookup is
    # best-effort and must never be a prerequisite for updating the active
    # provider/model in shai_config.json. This also protects the selection if
    # the detail endpoint is slow, unavailable, or interrupted.
    save_config(provider, model)

    print("Fetching details for selected model...")
    metadata = get_selected_model_details(provider, model)
    context_limit = metadata.get("context_window") or get_saved_context_window(provider, model)
    if context_limit:
        save_config(provider, model, context_limit)
    context_limit = get_saved_context_window(provider, model) or context_limit
    if context_limit:
        print("Model context window: {:,} tokens".format(context_limit))
    else:
        print("Model context window: unknown (configure it in model_config.json if needed)")
    print("Selected:", provider, "/", model)
    return provider, model
