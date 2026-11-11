"""Ollama local model catalogue handler."""
from .base import ProviderHandler

class OllamaHandler(ProviderHandler):
    name = "ollama"

    def discover_models(self, payload):
        items = (payload.get("models") or []) if isinstance(payload, dict) else []
        models = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name.strip():
                models.append(name.strip())
        return models
