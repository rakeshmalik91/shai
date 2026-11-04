"""Bounded history, tool-result and argument safeguards for Shai.

This module is intentionally provider-neutral and Python 3.9 compatible.
"""
import copy
import json


def _json_chars(value):
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(repr(value))


def _truncate_text(value, limit):
    value = str(value)
    if len(value) <= limit:
        return value
    marker = "\n...[TRUNCATED: {} characters omitted]...\n".format(
        len(value) - limit
    )
    available = max(0, limit - len(marker))
    head = available // 2
    tail = available - head
    return value[:head] + marker + (value[-tail:] if tail else "")


def bound_tool_result(result, max_chars=8000):
    """Serialize a tool result to bounded JSON, preserving both ends."""
    try:
        rendered = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = json.dumps({"result": str(result)}, ensure_ascii=False)
    return _truncate_text(rendered, max(256, int(max_chars)))


def _is_anthropic_tool_result(message):
    if message.get("role") != "user":
        return False
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "tool_result"
        for item in content
    )


def _bound_message_tool_content(message, tool_result_chars):
    message = copy.deepcopy(message)
    role = message.get("role")
    if role == "tool":
        message["content"] = _truncate_text(
            message.get("content", ""), tool_result_chars
        )
    elif _is_anthropic_tool_result(message):
        blocks = []
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                block = dict(block)
                block["content"] = _truncate_text(
                    block.get("content", ""), tool_result_chars
                )
            blocks.append(block)
        message["content"] = blocks
    return message


def _turn_groups(messages):
    """Split messages into user turns without splitting tool-result blocks."""
    groups = []
    current = []
    for message in messages:
        if (message.get("role") == "user"
                and not _is_anthropic_tool_result(message)):
            if current:
                groups.append(current)
            current = [message]
        else:
            if not current:
                current = [message]
            else:
                current.append(message)
    if current:
        groups.append(current)
    return groups


def _summarize_old_groups(groups, max_chars=5000):
    """Create a compact, deterministic summary of older turns."""
    lines = ["[Earlier conversation compacted to fit the model context.]"]
    for group in groups:
        user_text = []
        assistant_text = []
        tool_names = []
        for message in group:
            role = message.get("role")
            content = message.get("content")
            if role == "user" and isinstance(content, str):
                user_text.append(content)
            elif role == "assistant":
                if isinstance(content, str) and content.strip():
                    assistant_text.append(content)
                for call in message.get("tool_calls", []) or []:
                    function = call.get("function", {})
                    if isinstance(function, dict):
                        name = function.get("name")
                        if name:
                            tool_names.append(str(name))
        if user_text:
            lines.append("Earlier request: " + _truncate_text(" ".join(user_text), 700))
        if tool_names:
            lines.append("Tools used: " + ", ".join(tool_names[-12:]))
        if assistant_text:
            lines.append("Earlier response: " + _truncate_text(" ".join(assistant_text), 700))
        if _json_chars(lines) >= max_chars:
            break
    return _truncate_text("\n".join(lines), max_chars)


def _compact_latest_group(group, max_recent_tool_cycles=6):
    """Keep the original request, newest complete tool cycles and final text."""
    if not group:
        return group
    first_user = group[0] if group[0].get("role") == "user" else None
    rest = group[1:] if first_user else group[:]
    chunks = []
    tool_chunk_indices = []
    index = 0
    while index < len(rest):
        message = rest[index]
        chunk = [message]
        index += 1
        content_blocks = message.get("content")
        has_anthropic_tool_use = (
            isinstance(content_blocks, list)
            and any(isinstance(block, dict) and block.get("type") == "tool_use"
                    for block in content_blocks)
        )
        is_tool_cycle = (
            message.get("role") == "assistant"
            and (message.get("tool_calls") or has_anthropic_tool_use)
        )
        if is_tool_cycle:
            while index < len(rest) and rest[index].get("role") == "tool":
                chunk.append(rest[index])
                index += 1
            if index < len(rest) and _is_anthropic_tool_result(rest[index]):
                chunk.append(rest[index])
                index += 1
            tool_chunk_indices.append(len(chunks))
        chunks.append(chunk)

    limit = max(1, int(max_recent_tool_cycles))
    if len(tool_chunk_indices) <= limit:
        return group

    keep_indices = set(tool_chunk_indices[-limit:])
    last_tool_index = tool_chunk_indices[-1]
    # Retain the final assistant text/status after the most recent tool result.
    keep_indices.update(
        index for index in range(last_tool_index + 1, len(chunks))
    )
    kept = [chunk for index, chunk in enumerate(chunks) if index in keep_indices]
    removed_cycles = len(tool_chunk_indices) - limit
    compacted = []
    if first_user:
        compacted.append(first_user)
    compacted.append({
        "role": "assistant",
        "content": (
            "[Earlier tool activity in this request compacted: {} older "
            "tool-call cycle(s) omitted. The original user request is retained.]"
        ).format(removed_cycles)
    })
    for chunk in kept:
        compacted.extend(chunk)
    return compacted

def compact_conversation_history(messages, max_chars=90000,
                                 max_tool_result_chars=8000,
                                 keep_recent_turns=4,
                                 max_recent_tool_cycles=6):
    """Return bounded provider history while retaining the latest user ask."""
    if not isinstance(messages, list):
        return messages
    max_chars = max(4000, int(max_chars))
    tool_limit = max(256, int(max_tool_result_chars))
    copied = [
        _bound_message_tool_content(item, tool_limit)
        for item in messages if isinstance(item, dict)
    ]
    system = [item for item in copied if item.get("role") == "system"]
    non_system = [item for item in copied if item.get("role") != "system"]
    if _json_chars(copied) <= max_chars:
        return copied

    groups = _turn_groups(non_system)
    if not groups:
        return system

    latest_original = groups[-1]
    latest_user = next(
        (item for item in latest_original
         if item.get("role") == "user" and not _is_anthropic_tool_result(item)),
        None
    )
    recent = groups[-max(1, int(keep_recent_turns)):]
    older = groups[:-len(recent)] if recent else groups[:]

    def build_candidate(recent_groups, old_groups, cycle_limit):
        candidate = list(system)
        if old_groups:
            candidate.append({
                "role": "assistant",
                "content": _summarize_old_groups(old_groups, max_chars=5000)
            })
        if recent_groups:
            candidate.extend(recent_groups[:-1])
            latest = _compact_latest_group(
                recent_groups[-1], max_recent_tool_cycles=cycle_limit
            )
            candidate.extend(latest)
        return candidate

    cycle_limit = max(1, int(max_recent_tool_cycles))
    candidate = build_candidate(recent, older, cycle_limit)

    # Drop the oldest retained turns one at a time, adding them to summary.
    while len(recent) > 1 and _json_chars(candidate) > max_chars:
        older.append(recent.pop(0))
        candidate = build_candidate(recent, older, cycle_limit)

    # Reduce retained tool cycles from the current turn until it fits.
    while cycle_limit > 1 and _json_chars(candidate) > max_chars:
        cycle_limit -= 1
        candidate = build_candidate(recent, older, cycle_limit)

    if _json_chars(candidate) <= max_chars:
        return candidate

    # Last-resort compact view: exact latest request plus one newest complete
    # tool cycle and a short note. Never leave an orphaned tool response.
    latest_group = recent[-1] if recent else latest_original
    compact_latest = _compact_latest_group(latest_group, 1)
    compacted = list(system)
    if older or len(compact_latest) < len(latest_group):
        compacted.append({
            "role": "assistant",
            "content": "[Older conversation and tool activity compacted.]"
        })
    compacted.extend(compact_latest)

    # If a single tool cycle is still too large, remove it as a whole while
    # retaining the user request. Tool results can be fetched again if needed.
    if _json_chars(compacted) > max_chars:
        user = latest_user or (
            compact_latest[0] if compact_latest
            and compact_latest[0].get("role") == "user" else None
        )
        compacted = list(system)
        if user is not None:
            user_copy = copy.deepcopy(user)
            content = user_copy.get("content", "")
            user_copy["content"] = _truncate_text(
                content, max(1000, max_chars // 3)
            )
            compacted.append(user_copy)
        compacted.append({
            "role": "assistant",
            "content": "[Earlier context and tool results compacted to fit the model context. Continue from the original request.]"
        })
    return compacted

def validate_tool_arguments(name, args, function_definitions,
                            reject_unknown=True):
    """Validate tool args against the advertised JSON schema."""
    if not isinstance(args, dict):
        return "Tool arguments must be a JSON object."
    schema = None
    for item in function_definitions:
        function = item.get("function", {})
        if function.get("name") == name:
            schema = function.get("parameters", {})
            break
    if schema is None:
        return "No parameter schema is registered for tool {!r}.".format(name)

    properties = schema.get("properties", {})
    required = schema.get("required", [])
    missing = [key for key in required if key not in args]
    if missing:
        return "Missing required argument(s): {}.".format(", ".join(missing))
    if reject_unknown:
        unknown = [key for key in args if key not in properties]
        if unknown:
            return "Unexpected argument(s): {}. Allowed arguments: {}.".format(
                ", ".join(sorted(unknown)), ", ".join(sorted(properties))
            )

    type_map = {
        "string": str, "integer": int, "number": (int, float),
        "boolean": bool, "object": dict, "array": list
    }
    for key, value in args.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        python_type = type_map.get(expected)
        if python_type:
            if expected == "integer" and isinstance(value, bool):
                return "Argument {!r} must be an integer.".format(key)
            if expected == "number" and isinstance(value, bool):
                return "Argument {!r} must be a number.".format(key)
            if not isinstance(value, python_type):
                return "Argument {!r} must be {}.".format(key, expected)
        if "enum" in spec and value not in spec["enum"]:
            return "Argument {!r} must be one of: {}.".format(
                key, ", ".join(str(item) for item in spec["enum"])
            )
    return None
