"""Anthropic model catalogue and detail handler."""
from .base import ProviderHandler

class AnthropicHandler(ProviderHandler):
    name = "anthropic"

    def discover_models(self, payload):
        return super().discover_models(payload)
