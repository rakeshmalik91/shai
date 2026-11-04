"""Shared interactive provider and model selection for Shai."""

import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent.parent
CONFIG_FILE = SCRIPT_DIR / "shai_config.json"

MODEL_CONTEXT_WINDOWS = {}

PROVIDERS = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "models_url": "https://openrouter.ai/api/v1/models",
        "key_env": "OPENROUTER_API_KEY",
        "format": "openai",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "key_env": "GEMINI_API_KEY",
        "format": "openai",
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "models_url": "https://api.anthropic.com/v1/models?limit=1000",
        "key_env": "ANTHROPIC_API_KEY",
        "format": "anthropic",
    },
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "models_url": "https://api.openai.com/v1/models",
        "key_env": "OPENAI_API_KEY",
        "format": "openai",
    },
    "kilo": {
        "url": "https://ai-gateway.kilo.ai/api/v1/chat/completions",
        "models_url": "https://ai-gateway.kilo.ai/api/v1/models",
        "key_env": "KILO_API_KEY",
        "format": "openai",
    },
    "kilo-free": {
        "url": "https://ai-gateway.kilo.ai/api/v1/chat/completions",
        "models_url": "https://ai-gateway.kilo.ai/api/v1/models",
        "key_env": "KILO_API_KEY",
        "format": "openai",
    },
}


def load_config():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(provider, model, context_window_tokens=None):
    """Update provider/model and context metadata, preserving other settings."""
    config = load_config()
    config["provider"] = provider
    config["model"] = model
    context_key = "{}::{}".format(provider, model)
    context_mapping = config.get("model_context_windows", {})
    if not isinstance(context_mapping, dict):
        context_mapping = {}
    if isinstance(context_window_tokens, int) and not isinstance(context_window_tokens, bool) and context_window_tokens > 0:
        context_mapping[context_key] = context_window_tokens
        config["context_window_tokens"] = context_window_tokens
    else:
        saved_limit = context_mapping.get(context_key)
        if isinstance(saved_limit, int) and saved_limit > 0:
            config["context_window_tokens"] = saved_limit
        else:
            config.pop("context_window_tokens", None)
    # Sort model_context_windows by key for consistent ordering
    config["model_context_windows"] = dict(sorted(context_mapping.items()))
    temporary = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(CONFIG_FILE)

def _get_json(url, key, provider):
    if provider == "gemini":
        separator = "&" if "?" in url else "?"
        url += separator + urllib.parse.urlencode({"key": key})
        headers = {}
    elif provider == "anthropic":
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
    else:
        headers = {"Authorization": "Bearer " + key}

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
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
    """Return a persisted provider-reported or user-configured context limit."""
    config = load_config()
    key = "{}::{}".format(provider, model)
    mapping = config.get("model_context_windows", {})
    if isinstance(mapping, dict):
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    if config.get("provider") == provider and config.get("model") == model:
        value = config.get("context_window_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def get_available_models(provider):
    config = PROVIDERS[provider]
    key = os.environ.get(config["key_env"])
    if not key:
        raise RuntimeError(config["key_env"] + " is not set. Source setup_keys.sh first.")

    data = _get_json(config["models_url"], key, provider)
    MODEL_CONTEXT_WINDOWS.clear()

    if provider == "gemini":
        models = []
        for item in data.get("models", []):
            if "generateContent" not in item.get("supportedGenerationMethods", []):
                continue
            name = item.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/"):]
            if name:
                models.append(name)
                limit = _model_context_limit(item)
                if limit:
                    MODEL_CONTEXT_WINDOWS[name] = limit
    else:
        models = []
        for item in data.get("data", []):
            model_name = item.get("id", "")
            if model_name:
                models.append(model_name)
                limit = _model_context_limit(item)
                if limit:
                    MODEL_CONTEXT_WINDOWS[model_name] = limit

    if provider in ("openrouter", "kilo-free"):
        models = [model for model in models if model.endswith(":free")]

    return sorted(set(model for model in models if model))


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
    """Prompt for provider, then model, on every invocation.

    Provider 0 reuses the last provider/model pair and skips the model prompt.

    If auto_select is True, automatically use the last saved provider/model
    if valid (provider exists and model is available), otherwise fall back
    to interactive selection.
    """
    saved = load_config()
    saved_provider = saved.get("provider")
    saved_model = saved.get("model")

    # Auto-select: try to use saved provider/model without prompting
    if auto_select and saved_provider in PROVIDERS and saved_model:
        try:
            models = get_available_models(saved_provider)
            if saved_model in models:
                context_limit = MODEL_CONTEXT_WINDOWS.get(saved_model)
                if context_limit:
                    print("Model context window: {:,} tokens".format(context_limit))
                else:
                    print("Model context window: unknown (configure it in shai_config.json if needed)")
                print("Selected: {} / {} (auto-selected)".format(saved_provider, saved_model))
                return saved_provider, saved_model
            else:
                print("Saved model '{}' not available for provider '{}'; falling back to interactive selection.".format(saved_model, saved_provider))
        except Exception as error:
            print("Could not validate saved selection ({}); falling back to interactive selection.".format(error))

    names = list(PROVIDERS)
    print("\nSelect provider:")
    if saved_provider in PROVIDERS and saved_model:
        print("  0. Last used: {} / {}".format(saved_provider, saved_model))
    else:
        print("  0. Last used: (not configured)")
    for number, name in enumerate(names, 1):
        label = "Kilo Gateway (all models)" if name == "kilo" else name
        if name == "kilo-free":
            label = "Kilo Gateway (free models only)"
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
    if not models:
        raise RuntimeError("No models are currently available for " + provider + ".")

    preferred = saved_model if saved_provider == provider else None
    model = choose_model(models, preferred)
    context_limit = MODEL_CONTEXT_WINDOWS.get(model)
    save_config(provider, model, context_limit)
    if context_limit:
        print("Model context window: {:,} tokens".format(context_limit))
    else:
        print("Model context window: unknown (configure it in shai_config.json if needed)")
    print("Selected:", provider, "/", model)
    return provider, model
