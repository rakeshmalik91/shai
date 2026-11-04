"""Shared interactive provider and model selection for Shai."""

import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent.parent
CONFIG_FILE = SCRIPT_DIR / "shai_config.json"

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


def save_config(provider, model):
    """Update provider/model while preserving all other Shai settings."""
    config = load_config()
    config["provider"] = provider
    config["model"] = model
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


def get_available_models(provider):
    config = PROVIDERS[provider]
    key = os.environ.get(config["key_env"])
    if not key:
        raise RuntimeError(config["key_env"] + " is not set. Source setup_keys.sh first.")

    data = _get_json(config["models_url"], key, provider)

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
    else:
        models = [
            item.get("id", "")
            for item in data.get("data", [])
            if item.get("id")
        ]

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


def initialize_provider():
    """Prompt for provider, then model, on every invocation.

    Provider 0 reuses the last provider/model pair and skips the model prompt.
    """
    saved = load_config()
    saved_provider = saved.get("provider")
    saved_model = saved.get("model")

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
    save_config(provider, model)
    print("Selected:", provider, "/", model)
    return provider, model
