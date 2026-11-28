"""OpenAI-compatible model catalogue and detail handler."""
from .base import ProviderHandler

class OpenAIHandler(ProviderHandler):
    name = "openai"
