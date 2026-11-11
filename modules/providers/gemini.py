"""Google Gemini model discovery and metadata handler."""
from .base import ProviderHandler

class GeminiHandler(ProviderHandler):
    name = "gemini"

    def discover_models(self, payload):
        items = (payload.get("models") or []) if isinstance(payload, dict) else []
        models = []
        for item in items:
            if not isinstance(item, dict):
                continue
            methods = item.get("supportedGenerationMethods", [])
            if not isinstance(methods, list) or "generateContent" not in methods:
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            if name.startswith("models/"):
                name = name[len("models/"):]
            if name:
                models.append(name)
        return models

    def metadata(self, item):
        result = super().metadata(item)
        if not isinstance(item, dict):
            return result
        # Gemini's Model resource exposes limits and supported generation APIs.
        for source, target in (("inputTokenLimit", "context_window"),
                               ("outputTokenLimit", "max_output_tokens")):
            value = item.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result[target] = value
        methods = item.get("supportedGenerationMethods")
        if isinstance(methods, list):
            clean_methods = list(dict.fromkeys(
                value for value in methods if isinstance(value, str) and value.strip()
            ))
            if clean_methods:
                result["supported_generation_methods"] = clean_methods
            if "generateContent" in clean_methods:
                result.setdefault("supported_input_types", ["text"])
                result.setdefault("supported_output_types", ["text"])
        # Retain descriptive/model tuning fields for diagnostics and future
        # routing decisions; model_config intentionally supports extensions.
        for source, target in (("displayName", "display_name"),
                               ("description", "description"),
                               ("version", "version"),
                               ("temperature", "temperature"),
                               ("topP", "top_p"),
                               ("topK", "top_k"),
                               ("maxTemperature", "max_temperature")):
            value = item.get(source)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                result[target] = value
        return result
