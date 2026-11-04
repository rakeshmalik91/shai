"""OpenRouter Chat Completions reasoning-summary handler."""
from .openai_compatible import extract as _extract_openai_compatible


def extract(response):
    # OpenRouter's Chat Completions may expose reasoning_details. Only
    # explicitly summarized blocks are displayed; raw reasoning is withheld.
    return _extract_openai_compatible(response)
