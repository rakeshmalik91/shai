"""Summary extraction for OpenAI-compatible Chat Completions responses."""
from .common import summary_from_fields, summary_blocks, join_summaries


def extract(response):
    choices = response.get("choices", []) if isinstance(response, dict) else []
    message = choices[0].get("message", {}) if choices else {}
    if not isinstance(message, dict):
        message = {}

    # Do not read message.reasoning / reasoning_content: those may contain
    # private raw reasoning rather than a user-facing summary.
    return join_summaries(
        summary_from_fields(response, ("reasoning_summary", "thought_summary")),
        summary_from_fields(message, ("reasoning_summary", "thought_summary")),
        summary_blocks(
            message.get("reasoning_details"),
            {"summary", "reasoning_summary", "summary_text", "reasoning.summary"}
        ),
    )
