"""OpenRouter model catalogue metadata handler."""
from .base import ProviderHandler

class OpenRouterHandler(ProviderHandler):
    name = "openrouter"

    def metadata(self, item):
        result = super().metadata(item)
        if not isinstance(item, dict):
            return result
        top = item.get("top_provider")
        if isinstance(top, dict):
            for source, target in (("context_length", "context_window"),
                                   ("max_completion_tokens", "max_output_tokens")):
                value = top.get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    result[target] = value
        params = item.get("supported_parameters")
        if isinstance(params, list):
            params = {v.lower() for v in params if isinstance(v, str)}
            if params:
                result["supports_tools"] = bool({"tools", "tool_choice"} & params)
            if "response_format" in params:
                result["supports_structured_output"] = True
            if {"reasoning", "include_reasoning"} & params:
                result["supports_reasoning"] = True
        architecture = item.get("architecture")
        modality = architecture.get("modality") if isinstance(architecture, dict) else None
        if isinstance(modality, str) and "->" in modality:
            before, after = modality.split("->", 1)
            for key, raw in (("supported_input_types", before),
                             ("supported_output_types", after)):
                values = [part.strip().lower() for part in raw.split("+") if part.strip()]
                if values:
                    result[key] = values
        return result
