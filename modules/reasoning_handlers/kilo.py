"""Kilo Gateway reasoning-summary handler."""
from .openai_compatible import extract as _extract_openai_compatible


def extract(response):
    # Kilo Gateway uses an OpenAI-compatible response envelope. Model-specific
    # reasoning summaries are commonly carried in reasoning_details.
    return _extract_openai_compatible(response)
