"""Provider-neutral retries for successful API calls that contain no model output."""
import copy
import time


def _has_text(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return True
            if isinstance(item, dict):
                if _has_text(item.get("text")):
                    return True
        return False
    return False


def response_has_output(response, response_format):
    """True when a response contains user-visible text or a valid tool call."""
    if not isinstance(response, dict):
        return False
    if response_format == "anthropic" or (
        "choices" not in response and isinstance(response.get("content"), list)
    ):
        content = response.get("content")
        if not isinstance(content, list):
            return False
        return any(
            isinstance(block, dict) and (
                (block.get("type") == "text" and _has_text(block.get("text")))
                or block.get("type") == "tool_use"
            )
            for block in content
        )

    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            continue
        if message.get("tool_calls"):
            return True
        if _has_text(message.get("content")) or _has_text(message.get("refusal")):
            return True
    return False


def request_with_empty_response_retries(
    request_callable, history, response_format, settings, logger,
    retry_delay_seconds=0.5,
):
    """Retry an empty completion for any provider without mutating real history."""
    enabled = settings.get("empty_response_retry_enabled", True) is True
    maximum = settings.get("empty_response_max_retries", 2)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        maximum = 2

    for attempt in range(maximum + 1):
        request_history = copy.deepcopy(history)
        if attempt:
            request_history.append({
                "role": "user",
                "content": (
                    "Your previous response contained no text and no tool calls. "
                    "Please retry the original request and provide a complete "
                    "response. If you need to use tools, return valid tool calls."
                ),
            })
        response = request_callable(request_history)
        if response_has_output(response, response_format):
            if attempt:
                logger.info(
                    "Recovered from empty model response after %d retry attempt(s)",
                    attempt,
                )
            return response
        if not enabled or attempt >= maximum:
            logger.warning(
                "Model returned no output provider_format=%s attempts=%d",
                response_format, attempt + 1,
            )
            return response
        logger.warning(
            "Model returned no output provider_format=%s; retrying %d/%d",
            response_format, attempt + 1, maximum,
        )
        print(
            "\n[Shai] Model returned no output. Retrying {}/{}.".format(
                attempt + 1, maximum
            )
        )
        if retry_delay_seconds:
            time.sleep(retry_delay_seconds)
    return response
