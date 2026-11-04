"""Provider-specific extraction and display of reasoning summaries."""
import json
import logging
import pathlib

from . import anthropic, gemini, kilo, openai, openrouter

LOGGER = logging.getLogger("shai.reasoning")
HANDLERS = {
    "anthropic": anthropic,
    "gemini": gemini,
    "openai": openai,
    "openrouter": openrouter,
    "kilo": kilo,
    "kilo-free": kilo,
}
MAX_DISPLAY_CHARS = 6000
CONFIG_FILE = pathlib.Path(__file__).resolve().parents[2] / "shai_config.json"


def _display_settings():
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        settings = config.get("reasoning_display", {})
        if not isinstance(settings, dict):
            return True, MAX_DISPLAY_CHARS
        enabled = settings.get("enabled", True) is True
        limit = settings.get("max_chars", MAX_DISPLAY_CHARS)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            limit = MAX_DISPLAY_CHARS
        return enabled, limit
    except (OSError, ValueError, TypeError):
        return True, MAX_DISPLAY_CHARS


def extract_reasoning_summary(provider, response):
    handler = HANDLERS.get(provider)
    if handler is None:
        LOGGER.debug("No reasoning-summary handler for provider=%s", provider)
        return ""
    try:
        summary = handler.extract(response)
    except Exception:
        LOGGER.exception("Could not extract reasoning summary provider=%s", provider)
        return ""
    if summary:
        LOGGER.debug("Provider reasoning summary received provider=%s chars=%d",
                     provider, len(summary))
    return summary


def display_reasoning_summary(provider, response, max_chars=None):
    """Print a provider-supplied summary, never raw private reasoning fields."""
    enabled, configured_limit = _display_settings()
    if not enabled:
        return False
    if max_chars is None:
        max_chars = configured_limit
    summary = extract_reasoning_summary(provider, response)
    if not summary:
        return False
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "\n...[summary truncated]..."
    print("\n[Thinking summary]\n" + summary)
    return True
