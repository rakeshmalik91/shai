"""Helpers for extracting provider-supplied reasoning summaries only.

Raw private chain-of-thought fields (reasoning, reasoning_content, analysis,
thinking) are deliberately not surfaced. This module accepts only fields that
providers explicitly identify as summaries or summary blocks.
"""


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text") or item.get("summary_text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts)
    if isinstance(value, dict):
        text = value.get("text") or value.get("summary_text")
        return text.strip() if isinstance(text, str) else ""
    return ""


def summary_from_fields(obj, field_names):
    if not isinstance(obj, dict):
        return ""
    parts = []
    for name in field_names:
        value = _text(obj.get(name))
        if value and value not in parts:
            parts.append(value)
    return "\n".join(parts)


def summary_blocks(blocks, accepted_types):
    if not isinstance(blocks, list):
        return ""
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if str(block.get("type", "")).lower() not in accepted_types:
            continue
        value = _text(block.get("summary") or block.get("text") or block.get("content"))
        if value and value not in parts:
            parts.append(value)
    return "\n".join(parts)


def join_summaries(*values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return "\n".join(result)
