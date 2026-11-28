"""Shared provider handler primitives."""

class ProviderHandler:
    name = None
    uses_catalogue_metadata = False

    def cache_catalogue(self, payload, store, provider):
        """Hook for providers whose list response is the only detail source."""
        return None

    def catalogue_metadata(self, model, store):
        return {}

    def discover_models(self, payload):
        items = (payload.get("data") or []) if isinstance(payload, dict) else []
        return [item["id"] for item in items if isinstance(item, dict)
                and isinstance(item.get("id"), str) and item["id"].strip()]

    def metadata(self, item):
        if not isinstance(item, dict):
            return {}
        result = {}
        for source in ("context_length", "context_window", "max_context_length",
                       "input_token_limit", "inputTokenLimit", "max_input_tokens"):
            value = item.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result["context_window"] = value
                break
        for source in ("max_output_tokens", "max_completion_tokens",
                       "output_token_limit", "outputTokenLimit"):
            value = item.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result["max_output_tokens"] = value
                break
        architecture = item.get("architecture")
        input_types = item.get("supported_input_types", item.get("input_modalities"))
        output_types = item.get("supported_output_types", item.get("output_modalities"))
        if isinstance(architecture, dict):
            input_types = input_types or architecture.get("input_modalities")
            output_types = output_types or architecture.get("output_modalities")
        for key, value in (("supported_input_types", input_types),
                           ("supported_output_types", output_types)):
            if isinstance(value, list):
                result[key] = list(dict.fromkeys(v.strip().lower() for v in value
                                  if isinstance(v, str) and v.strip()))
        for source, target in (("tool_calling", "supports_tools"),
                               ("supports_tools", "supports_tools"),
                               ("supports_structured_output", "supports_structured_output"),
                               ("supports_reasoning", "supports_reasoning")):
            if isinstance(item.get(source), bool):
                result[target] = item[source]
        return result
