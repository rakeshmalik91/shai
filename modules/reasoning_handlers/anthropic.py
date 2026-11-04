"""Anthropic Messages API reasoning-summary handler."""
from .common import summary_from_fields, summary_blocks, join_summaries


def extract(response):
    content = response.get("content", []) if isinstance(response, dict) else []
    # Anthropic's ordinary `thinking` blocks can be private internal reasoning.
    # Display only blocks explicitly marked as summaries by the provider.
    return join_summaries(
        summary_from_fields(response, ("thinking_summary", "reasoning_summary")),
        summary_blocks(content, {"thinking_summary", "reasoning_summary", "summary"}),
    )
