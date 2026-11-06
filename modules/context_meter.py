"""Approximate prompt/context usage and normalize provider token usage.

The pre-request estimate is deliberately labelled approximate. Exact token
usage is taken from provider response metadata whenever it is supplied.
"""
import json
import math


def estimate_text_tokens(text):
    """Conservative, dependency-free estimate (roughly four UTF-8 chars/token)."""
    if not text:
        return 0
    encoded_length = len(str(text).encode("utf-8"))
    return max(1, int(math.ceil(encoded_length / 4.0)))


def estimate_messages_tokens(messages, tools=None, system_text=None):
    payload = {"messages": messages or []}
    if tools:
        payload["tools"] = tools
    if system_text:
        payload["system"] = system_text
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Add a small per-message framing allowance; tool schemas are included.
    count = estimate_text_tokens(serialized)
    count += 8 * len(messages or [])
    return count


def get_context_window(provider, model, config=None):
    """Resolve only explicit or provider-reported limits; never guess."""
    config = config if isinstance(config, dict) else {}
    key = "{}::{}".format(provider, model)
    # Explicit user overrides always win over provider-reported metadata.
    overrides = config.get("model_context_window_overrides", {})
    if isinstance(overrides, dict):
        value = overrides.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    mapping = config.get("model_context_windows", {})
    if isinstance(mapping, dict):
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    if config.get("provider") == provider and config.get("model") == model:
        value = config.get("context_window_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def extract_usage(response, provider_format=None):
    """Normalize OpenAI-compatible and Anthropic usage structures."""
    if not isinstance(response, dict):
        return {}
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return {}

    if provider_format == "anthropic" or "input_tokens" in usage:
        prompt = usage.get("input_tokens")
        completion = usage.get("output_tokens")
        total = usage.get("total_tokens")
    else:
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        total = usage.get("total_tokens")

    result = {}
    for key, value in (("prompt_tokens", prompt),
                       ("completion_tokens", completion),
                       ("total_tokens", total)):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    if "total_tokens" not in result and (
        "prompt_tokens" in result or "completion_tokens" in result
    ):
        result["total_tokens"] = (
            result.get("prompt_tokens", 0) +
            result.get("completion_tokens", 0)
        )
    return result


def format_context_status(estimated_prompt_tokens, context_limit=None,
                          generated_tokens=None, exact=False):
    """Format a concise status line for the terminal spinner and prompt."""
    label = "Prompt" if exact else "Prompt estimate"
    if generated_tokens is not None:
        used = max(0, estimated_prompt_tokens) + max(0, generated_tokens)
        label = "Context used"
    else:
        used = max(0, estimated_prompt_tokens)

    if context_limit:
        percentage = min(999.0, (used / float(context_limit)) * 100.0)
        return "{}: {:,} / {:,} tokens ({:.1f}%)".format(
            label, used, context_limit, percentage
        )
    return "{}: ~{:,} tokens (context limit unknown)".format(label, used)


def log_and_display_usage(logger, response, provider_format, estimated_prompt,
                          context_limit, prefix="[Shai]"):
    """Print exact usage when present and log estimate/actual provider usage."""
    usage = extract_usage(response, provider_format)
    if not usage:
        logger.debug(
            "Token usage unavailable; estimated_prompt_tokens=%d context_limit=%s",
            estimated_prompt, context_limit
        )
        status = format_context_status(estimated_prompt, context_limit)
        try:
            from modules.live_status import STATUS
            STATUS.set_context(status)
        except Exception:
            print("{} {}".format(prefix, status))
        return usage

    prompt = usage.get("prompt_tokens", estimated_prompt)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", prompt + completion)
    line = format_context_status(
        prompt, context_limit, generated_tokens=completion, exact=True
    )
    try:
        from modules.live_status import STATUS
        STATUS.set_context(line + " | prompt {:,} + generated {:,} = {:,}".format(
            prompt, completion, total
        ))
    except Exception:
        print("{} {} | prompt {:,} + generated {:,} = {:,} tokens".format(
            prefix, line, prompt, completion, total
        ))
    logger.debug(
        "Token usage exact_prompt=%s completion=%s total=%s estimated_prompt=%d context_limit=%s",
        prompt, completion, total, estimated_prompt, context_limit
    )
    return usage
