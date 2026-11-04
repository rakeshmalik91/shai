"""Gemini OpenAI-compatibility reasoning-summary handler."""
from .common import summary_from_fields, summary_blocks, join_summaries


def extract(response):
    choices = response.get("choices", []) if isinstance(response, dict) else []
    message = choices[0].get("message", {}) if choices else {}
    if not isinstance(message, dict):
        message = {}
    content = message.get("content", [])
    return join_summaries(
        summary_from_fields(response, ("reasoning_summary", "thought_summary")),
        summary_from_fields(message, ("reasoning_summary", "thought_summary")),
        summary_blocks(content, {"summary", "thought_summary", "reasoning_summary"}),
    )
