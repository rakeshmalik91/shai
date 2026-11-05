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
        tool_summaries = []
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
            elif role == "tool" or _is_anthropic_tool_result(message):
                tool_summaries.append(_extract_tool_result_summary(message, 220))
        if user_text:
            lines.append("Earlier request: " + _truncate_text(" ".join(user_text), 700))
        if tool_names:
            lines.append("Tools used: " + ", ".join(tool_names[-12:]))
        if tool_summaries:
            lines.append("Completed work: " + _truncate_text(" | ".join(tool_summaries[-8:]), 1200))
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
    omitted_chunks = [
        chunk for index, chunk in enumerate(chunks)
        if index not in keep_indices
    ]
    compacted.append({
        "role": "assistant",
        "content": (
            "[Earlier tool activity in this request compacted: {} older "
            "tool-call cycle(s) omitted. The original user request is retained.]\n{}"
        ).format(
            removed_cycles,
            _summarize_omitted_tool_chunks(omitted_chunks, max_chars=3000)
        )
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

class ToolArgumentFailureTracker:
    """Track invalid arguments per tool and disable repeatedly failing tools."""

    def __init__(self, max_failures=2):
        self.max_failures = max(1, int(max_failures))
        self.failures = {}
        self.last_errors = {}
        self.disabled = set()

    def record(self, tool_name, error):
        count = self.failures.get(tool_name, 0) + 1
        self.failures[tool_name] = count
        self.last_errors[tool_name] = str(error)
        # Invalid arguments are rejected, but a tool is never permanently
        # disabled: the model can correct parameters and continue the task.
        return count, False

    def is_disabled(self, tool_name):
        return False


def tool_call_signature(name, args, default_read_file_lines=100):
    """Exact repeat key based on tool name and every normalized parameter."""
    normalized_args = copy.deepcopy(args) if isinstance(args, dict) else args
    if name == "read_file" and isinstance(normalized_args, dict):
        normalized_args.setdefault("offset", 0)
        normalized_args.setdefault("limit", int(default_read_file_lines))
    return json.dumps(
        {"name": str(name), "args": normalized_args},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def format_tool_call_for_display(name, args, max_chars=120):
    """Show a compact parameter preview suitable for the one-line status panel."""
    def compact(value, string_limit=48):
        if isinstance(value, str):
            if len(value) > string_limit:
                return value[:string_limit - 1] + "…"
            return value
        if isinstance(value, dict):
            return {key: compact(item, string_limit) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            if len(value) > 3:
                return [compact(item, string_limit) for item in value[:3]] + [
                    "...(+{} items)".format(len(value) - 3)
                ]
            return [compact(item, string_limit) for item in value]
        return value

    try:
        compact_args = compact(args if isinstance(args, dict) else {"raw": args})
        rendered = json.dumps(
            compact_args,
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError):
        rendered = repr(args)
    limit = max(80, int(max_chars))
    if len(rendered) > limit:
        marker = "...[{} parameter characters omitted]...".format(len(rendered) - limit)
        head = max(1, (limit - len(marker)) // 2)
        tail = max(1, limit - len(marker) - head)
        rendered = rendered[:head] + marker + rendered[-tail:]
    # The live status panel already prints the tool name. Return only its
    # arguments here; including another tool-name prefix makes the status
    # line look like the same tool call is being printed twice.
    return rendered


def read_file_cache_marker(path, offset=0, limit=100):
    """Stable marker used to avoid returning the same file chunk repeatedly."""
    return "[Shai read_file chunk path={} offset={} limit={}]".format(
        path, offset, limit
    )


def marker_in_history(history, marker):
    """Return True when a prior tool result containing marker is still present."""
    if not marker or not isinstance(history, list):
        return False

    def contains(value):
        if isinstance(value, str):
            return marker in value
        if isinstance(value, dict):
            return any(contains(item) for item in value.values())
        if isinstance(value, list):
            return any(contains(item) for item in value)
        return False

    return any(contains(message) for message in history)


def _extract_tool_result_summary(message, max_chars=260):
    content = message.get("content", "")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                content = parsed.get("result", parsed.get("error", content))
        except (ValueError, TypeError):
            pass
    elif isinstance(content, list):
        content = " ".join(
            str(block.get("text", block.get("content", "")))
            for block in content if isinstance(block, dict)
        )
    text = " ".join(str(content).split())
    return _truncate_text(text, max_chars)


def _summarize_omitted_tool_chunks(chunks, max_chars=3000):
    """Keep a compact record of completed tool work when old cycles are dropped."""
    lines = ["[Completed work from older tool calls, compacted for context:]" ]
    for chunk in chunks:
        names = []
        summaries = []
        for message in chunk:
            if message.get("role") == "assistant":
                for call in message.get("tool_calls", []) or []:
                    function = call.get("function", {})
                    if isinstance(function, dict) and function.get("name"):
                        names.append(str(function.get("name")))
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    summaries.append(_truncate_text(content.strip(), 180))
            elif message.get("role") == "tool":
                summaries.append(_extract_tool_result_summary(message, 260))
            elif _is_anthropic_tool_result(message):
                summaries.append(_extract_tool_result_summary(message, 260))
        if names or summaries:
            label = ", ".join(names) if names else "tool activity"
            detail = " | ".join(item for item in summaries if item)
            lines.append("- {}: {}".format(label, detail or "completed"))
        if _json_chars(lines) >= max_chars:
            break
    return _truncate_text("\n".join(lines), max_chars)


def update_no_progress(recent_batches, batch_signature, no_progress_count,
                       had_errors, successful_calls):
    """Compatibility helper for callers that keep loop state separately."""
    repeated = batch_signature in recent_batches[-3:]
    # Successful repeated calls still represent work (for example, reading
    # adjacent chunks or revisiting a file). Only error-only iterations count.
    if successful_calls > 0:
        no_progress_count = 0
    elif had_errors:
        no_progress_count += 1
    else:
        no_progress_count = 0
    recent_batches.append(batch_signature)
    del recent_batches[:-4]
    return no_progress_count, repeated


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
        if "minimum" in spec and value < spec["minimum"]:
            return "Argument {!r} must be at least {}.".format(key, spec["minimum"])
        if "maximum" in spec and value > spec["maximum"]:
            return "Argument {!r} must be at most {}.".format(key, spec["maximum"])
        if "enum" in spec and value not in spec["enum"]:
            return "Argument {!r} must be one of: {}.".format(
                key, ", ".join(str(item) for item in spec["enum"])
            )
    return None
