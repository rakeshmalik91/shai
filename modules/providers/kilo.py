"""Kilo Gateway catalogue handler (OpenRouter-compatible schema)."""
from .openrouter import OpenRouterHandler

class KiloHandler(OpenRouterHandler):
    name = "kilo"
    uses_catalogue_metadata = True

    def cache_catalogue(self, payload, store, provider):
        items = payload.get("data", []) if isinstance(payload, dict) else []
        store[provider] = {
            item["id"]: item for item in items
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }

    def catalogue_metadata(self, model, store, provider=None):
        return self.metadata(store.get(provider or self.name, {}).get(model))
