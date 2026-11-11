"""Provider handler contract and defensive parsing tests."""
import unittest

from modules.providers import get_handler


class ProviderHandlerTests(unittest.TestCase):
    def test_registry_covers_every_configured_provider(self):
        from modules.provider_selection import DEFAULT_PROVIDERS
        for provider in DEFAULT_PROVIDERS:
            self.assertIsNotNone(get_handler(provider), provider)

    def test_openai_catalogue_and_metadata(self):
        handler = get_handler("openai")
        self.assertEqual(handler.discover_models({"data": [
            {"id": "gpt-a"}, None, {"id": ""}, {"name": "ignored"}, {"id": "gpt-b"}
        ]}), ["gpt-a", "gpt-b"])
        self.assertEqual(handler.metadata({
            "context_length": 128000, "max_output_tokens": 16000,
            "tool_calling": True, "supports_structured_output": True,
            "supports_reasoning": False,
            "input_modalities": ["text", "image"], "output_modalities": ["text"]
        }), {
            "context_window": 128000, "max_output_tokens": 16000,
            "supports_tools": True, "supports_structured_output": True,
            "supports_reasoning": False,
            "supported_input_types": ["text", "image"],
            "supported_output_types": ["text"]
        })

    def test_anthropic_catalogue_and_limits(self):
        handler = get_handler("anthropic")
        self.assertEqual(handler.discover_models({"data": [
            {"id": "claude-a"}, {"id": 123}, {}, "bad"
        ]}), ["claude-a"])
        self.assertEqual(handler.metadata({
            "max_input_tokens": 200000, "context_window": 200000,
            "max_output_tokens": 8192
        }), {"context_window": 200000, "max_output_tokens": 8192})

    def test_openrouter_metadata_and_malformed_nested_fields(self):
        handler = get_handler("openrouter")
        metadata = handler.metadata({
            "id": "vendor/model",
            "top_provider": {"context_length": 65536,
                             "max_completion_tokens": 4096},
            "supported_parameters": ["tools", "response_format", "reasoning"],
            "architecture": {"modality": "text+image->text"}
        })
        self.assertEqual(metadata["context_window"], 65536)
        self.assertEqual(metadata["max_output_tokens"], 4096)
        self.assertEqual(metadata["supported_input_types"], ["text", "image"])
        self.assertEqual(metadata["supported_output_types"], ["text"])
        self.assertTrue(metadata["supports_tools"])
        self.assertTrue(metadata["supports_structured_output"])
        self.assertTrue(metadata["supports_reasoning"])
        self.assertEqual(handler.metadata({
            "top_provider": [], "supported_parameters": [None, 2],
            "architecture": {"modality": 12}
        }), {})

    def test_gemini_populates_all_fields_available_in_model_resource(self):
        handler = get_handler("gemini")
        models = handler.discover_models({"models": [
            {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed-a", "supportedGenerationMethods": ["embedContent"]},
            {"name": None, "supportedGenerationMethods": ["generateContent"]},
            None,
            {"name": "models/gemini-b", "supportedGenerationMethods": ["generateContent"]},
        ]})
        self.assertEqual(models, ["gemini-a", "gemini-b"])
        metadata = handler.metadata({
            "name": "models/gemini-a",
            "displayName": "Gemini A",
            "description": "Multimodal model",
            "inputTokenLimit": 1000000,
            "outputTokenLimit": 8192,
            "supportedGenerationMethods": ["generateContent", "countTokens"],
            "supported_input_types": ["text", "image", "audio", "video"],
            "supported_output_types": ["text"],
            "supports_tools": True,
            "supports_structured_output": True,
            "supports_reasoning": True,
            "version": "001",
            "temperature": 1.0,
            "topP": 0.9,
            "topK": 40,
        })
        self.assertEqual(metadata["context_window"], 1000000)
        self.assertEqual(metadata["max_output_tokens"], 8192)
        self.assertEqual(metadata["supported_input_types"], ["text", "image", "audio", "video"])
        self.assertEqual(metadata["supported_output_types"], ["text"])
        self.assertTrue(metadata["supports_tools"])
        self.assertTrue(metadata["supports_structured_output"])
        self.assertTrue(metadata["supports_reasoning"])
        self.assertEqual(metadata["supported_generation_methods"],
                         ["generateContent", "countTokens"])
        self.assertEqual(metadata["display_name"], "Gemini A")
        self.assertEqual(metadata["description"], "Multimodal model")
        self.assertEqual(metadata["version"], "001")
        self.assertEqual(metadata["temperature"], 1.0)
        self.assertEqual(metadata["top_p"], 0.9)
        self.assertEqual(metadata["top_k"], 40)

    def test_gemini_safe_defaults_when_optional_fields_missing(self):
        metadata = get_handler("gemini").metadata({
            "inputTokenLimit": True, "outputTokenLimit": "8192",
            "supportedGenerationMethods": ["generateContent"]
        })
        self.assertEqual(metadata, {
            "supported_input_types": ["text"],
            "supported_output_types": ["text"],
            "supported_generation_methods": ["generateContent"]
        })

    def test_kilo_and_kilo_free_share_gateway_metadata_parser(self):
        payload = {"data": [{"id": "vendor/model:free"}]}
        self.assertEqual(get_handler("kilo").discover_models(payload),
                         ["vendor/model:free"])
        self.assertEqual(get_handler("kilo-free").metadata({
            "architecture": {"modality": "text+image->text"},
            "supported_parameters": ["tools"],
            "top_provider": {"context_length": 32000}
        })["context_window"], 32000)

    def test_ollama_model_name_fallback_and_invalid_entries(self):
        self.assertEqual(get_handler("ollama").discover_models({"models": [
            {"name": "llama:latest"}, {"model": "qwen:7b"},
            {"name": 7}, {}, None, "bad"
        ]}), ["llama:latest", "qwen:7b"])

    def test_all_handlers_are_total_for_invalid_payloads(self):
        for provider in ("openai", "anthropic", "openrouter", "gemini",
                         "kilo", "kilo-free", "ollama"):
            handler = get_handler(provider)
            self.assertEqual(handler.discover_models(None), [], provider)
            self.assertEqual(handler.discover_models({"data": None, "models": None}), [], provider)
            self.assertEqual(handler.metadata(None), {}, provider)
            self.assertEqual(handler.metadata([]), {}, provider)

    def test_provider_discovery_integration_uses_correct_response_shapes(self):
        from unittest import mock
        from modules import provider_selection as selection
        cases = {
            "openai": {"data": [{"id": "gpt-a"}, {"id": "gpt-b"}]},
            "anthropic": {"data": [{"id": "claude-a"}]},
            "openrouter": {"data": [{"id": "vendor/model:free"}]},
            "gemini": {"models": [
                {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
            ]},
            "kilo": {"data": [{"id": "vendor/paid"}]},
            "kilo-free": {"data": [{"id": "vendor/free:free"}]},
            "ollama": {"models": [{"name": "llama:latest"}]},
        }
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            original = selection.CONFIG_FILE
            selection.CONFIG_FILE = Path(temp) / "shai_config.json"
            try:
                for provider, payload in cases.items():
                    with self.subTest(provider=provider), \
                         mock.patch.object(selection, "provider_api_key", return_value=None), \
                         mock.patch.object(selection, "_get_json", return_value=payload):
                        result = selection.get_available_models(provider)
                        self.assertTrue(result, provider)
            finally:
                selection.CONFIG_FILE = original

    def test_selected_detail_metadata_is_persisted_for_each_remote_provider(self):
        from unittest import mock
        from modules import provider_selection as selection
        import tempfile
        from pathlib import Path
        payloads = {
            "openai": {"id": "m", "context_length": 64000, "max_output_tokens": 4000,
                       "tool_calling": True},
            "anthropic": {"id": "m", "max_input_tokens": 200000, "max_output_tokens": 8192},
            "openrouter": {"id": "m", "context_length": 128000,
                           "max_completion_tokens": 4096,
                           "supported_parameters": ["tools"]},
            "gemini": {"name": "models/m", "inputTokenLimit": 1000000,
                       "outputTokenLimit": 8192,
                       "supportedGenerationMethods": ["generateContent", "countTokens"]},
        }
        with tempfile.TemporaryDirectory() as temp:
            original = selection.CONFIG_FILE
            selection.CONFIG_FILE = Path(temp) / "assets" / "shai_config.json"
            selection.CONFIG_FILE.parent.mkdir()
            try:
                for provider, payload in payloads.items():
                    with self.subTest(provider=provider), \
                         mock.patch.object(selection, "provider_api_key", return_value=None), \
                         mock.patch.object(selection, "_get_json", return_value=payload):
                        result = selection.get_selected_model_details(provider, "m")
                        self.assertTrue(result, provider)
                        saved = selection.CONFIG_FILE.parent / "model_config.json"
                        from modules.model_config import load_model_config, get_model_config
                        model = get_model_config(provider, "m", load_model_config(saved))
                        self.assertTrue(model.get("context_window"), provider)
                        self.assertTrue(model.get("max_output_tokens"), provider)
            finally:
                selection.CONFIG_FILE = original


if __name__ == "__main__":
    unittest.main()
