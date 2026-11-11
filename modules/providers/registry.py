"""Provider handler registry."""
from .openai import OpenAIHandler
from .anthropic import AnthropicHandler
from .openrouter import OpenRouterHandler
from .gemini import GeminiHandler
from .kilo import KiloHandler
from .ollama import OllamaHandler

_HANDLERS = {
    "openai": OpenAIHandler(),
    "anthropic": AnthropicHandler(),
    "openrouter": OpenRouterHandler(),
    "gemini": GeminiHandler(),
    "kilo": KiloHandler(),
    "kilo-free": KiloHandler(),
    "ollama": OllamaHandler(),
}

def get_handler(provider):
    try:
        return _HANDLERS[provider]
    except KeyError:
        raise KeyError("Unknown provider handler: " + str(provider))
