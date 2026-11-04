"""OpenAI Chat Completions reasoning-summary handler."""
from .openai_compatible import extract as _extract_openai_compatible


def extract(response):
    return _extract_openai_compatible(response)
